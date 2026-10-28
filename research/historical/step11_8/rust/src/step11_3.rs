//! Step 11.3 bounded event-triggered V2/V3 research report verification.
//! JSON is untrusted/offline RPC evidence. Nothing grants permission to transact.
use ethers::types::U256;
use serde_json::Value;
use std::collections::{BTreeMap,BTreeSet};

const DAI:&str="0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";
const WETH:&str="0x7ceb23fd6bc0add59e62ac25578270cff1b9f619";
const USDC:&str="0x2791bca1f2de4661ed88a30c99a7a9449aa84174";
fn get<'a>(v:&'a Value,k:&str)->Result<&'a Value,String>{v.get(k).ok_or_else(||format!("missing {k}"))}
fn string<'a>(v:&'a Value,k:&str)->Result<&'a str,String>{get(v,k)?.as_str().ok_or_else(||format!("not string {k}"))}
fn number(v:&Value,k:&str)->Result<u64,String>{get(v,k)?.as_u64().ok_or_else(||format!("not uint {k}"))}
fn raw(v:&Value,k:&str)->Result<U256,String>{
 let s=string(v,k)?;
 if s.is_empty()||s.len()>78||(s.len()>1&&s.starts_with('0'))||!s.bytes().all(|c|c.is_ascii_digit()){
  return Err(format!("invalid decimal {k}"));
 }
 U256::from_dec_str(s).map_err(|_|format!("overflow {k}"))
}
fn hex(s:&str,l:usize)->bool{s.len()==l&&s.starts_with("0x")&&s[2..].bytes().all(|c|c.is_ascii_hexdigit()&&!c.is_ascii_uppercase())&&s[2..].bytes().any(|c|c!=b'0')}
fn token(symbol:&str)->Option<(&'static str,u64)>{match symbol{"DAI"=>Some((DAI,18)),"WETH"=>Some((WETH,18)),"USDCe"=>Some((USDC,6)),_=>None}}
fn pair(id:&str)->Option<(&'static str,&'static str)>{match id{"DAI_WETH"=>Some(("DAI","WETH")),"USDCe_WETH"=>Some(("USDCe","WETH")),"DAI_USDCe"=>Some(("DAI","USDCe")),_=>None}}
fn items<'a>(v:&'a Value,k:&str)->Result<&'a Vec<Value>,String>{get(v,k)?.as_array().ok_or_else(||format!("not array {k}"))}
fn pool(v:&Value,a:&str,b:&str,kind:&str,tier:u64)->Result<Option<String>,String>{
 if v.is_null(){return Ok(None)}
 let addr=string(v,"address")?;
 if !hex(addr,42){return Err("invalid pool address".into())}
 let t0=string(v,"token0")?;let t1=string(v,"token1")?;
 if !((t0==a&&t1==b)||(t0==b&&t1==a))||t0>=t1{return Err("pool token identity invalid".into())}
 match kind{
  "v2"=>{
   let r0=raw(v,"reserve0_raw")?;let r1=raw(v,"reserve1_raw")?;
   if r0.is_zero()||r1.is_zero()||r0>=(U256::one()<<112)||r1>=(U256::one()<<112){return Err("V2 reserve invalid".into())}
  },
  "algebra"|"uniswap"=>{
   let liq=raw(v,"liquidity_raw")?;
   if liq>=(U256::one()<<128){return Err("V3 liquidity invalid".into())}
   if kind=="algebra"{if !get(v,"fee_tier")?.is_null(){return Err("algebra tier forged".into())}}
   else if number(v,"fee_tier")?!=tier{return Err("Uniswap pool tier forged".into())}
  },
  _=>return Err("unknown pool kind".into()),
 }
 Ok(Some(addr.to_string()))
}
fn pair_ids(v:&Value)->Result<BTreeSet<String>,String>{
 let pairs=items(v,"requested_pairs")?;
 if pairs.is_empty()||pairs.len()>3{return Err("bad pair count".into())}
 let mut seen=BTreeSet::new();
 for p in pairs{let id=p.as_str().ok_or("invalid pair id")?;
  if pair(id).is_none()||!seen.insert(id.to_string()){return Err("unvetted or repeated pair".into())}}
 Ok(seen)
}

#[derive(Debug,Default)]
pub(crate) struct Summary{pub blocks:usize,pub quotes:usize,pub gross_rejected:usize,
 pub positive_unassessed:usize,pub vault_rejected:usize,pub skipped:usize,pub events:u64}

pub(crate) fn assess(json:&str)->Result<Summary,String>{
 let v:Value=serde_json::from_str(json).map_err(|_|"invalid JSON")?;
 if number(&v,"schema_version")?!=1||number(&v,"chain_id")?!=137||
  string(&v,"evidence_kind")?!="polygon_historical_pool_event_mixed_v2_v3_readonly"{
  return Err("wrong network/schema/kind".into());
 }
 let safety=get(&v,"safety")?;
 for key in ["writes_or_broadcasts_transactions","uses_state_overrides","proves_executor_eth_call",
  "proves_gas_or_net_profit","proves_inclusion","executor_supports_mixed_routes"]{
  if get(safety,key)?.as_bool()!=Some(false){return Err(format!("unsafe claim {key}"))}
 }
 if !["primary","fallback"].contains(&string(&v,"provider_role")?){return Err("provider role invalid".into())}
 let window=get(&v,"window")?;
 let first=number(window,"from_block")?;let last=number(window,"to_block")?;
 if first==0||first>last||last-first>=400||!hex(string(window,"anchor_hash")?,66){return Err("invalid window".into())}
 let pairs=pair_ids(&v)?;
 let tiers=items(&v,"tiers")?;
 if tiers.is_empty()||tiers.len()>2{return Err("invalid tiers".into())}
 let mut ts=BTreeSet::new();
 for n in tiers{let t=n.as_u64().ok_or("tier not integer")?;
  if ![500,3000].contains(&t)||!ts.insert(t){return Err("bad tier".into())}}
 let sizes=items(&v,"sizes_whole_units")?;
 if sizes.is_empty()||sizes.len()>3{return Err("invalid sizes".into())}
 let mut ss=BTreeSet::new();
 for s in sizes{let n=s.as_u64().ok_or("size not integer")?;
  if n==0||n>100||!ss.insert(n){return Err("bad size".into())}}
 let blocks=items(&v,"selected_blocks")?;
 if blocks.len()>2{return Err("too many selected blocks".into())}
 let mut seen_blocks=BTreeSet::new();let mut summary=Summary::default();
 for block in blocks{
  let height=number(block,"number")?;
  if height<first||height>last||!seen_blocks.insert(height)||!hex(string(block,"hash")?,66){return Err("block identity invalid".into())}
  let events=number(block,"activity_event_count")?;
  if events==0||events>4500{return Err("invalid activity count".into())}
  summary.events+=events;
  let active=items(block,"active_pairs")?;
  let mut active_ids=BTreeSet::new();
  for p in active{let id=p.as_str().ok_or("invalid active pair")?;
   if !pairs.contains(id)||!active_ids.insert(id.to_string()){return Err("bad active pair".into())}}
  if active_ids.is_empty(){return Err("event block with no active pair".into())}
  let fee=raw(block,"fee_rate_wad")?;
  if fee>U256::exp10(16){return Err("flash fee too high".into())}
  let entries=items(block,"pairs")?;
  if entries.len()!=pairs.len(){return Err("incomplete pair snapshots".into())}
  let mut seen_pairs=BTreeSet::new();
  for entry in entries{
   let id=string(entry,"pair_id")?;
   if !pairs.contains(id)||!seen_pairs.insert(id){return Err("duplicate/unrequested snapshot".into())}
   let (borrow,mid)=pair(id).ok_or("invalid pair")?;
   if string(entry,"borrow_symbol")?!=borrow||string(entry,"mid_symbol")?!=mid{return Err("borrow/mid mismatch".into())}
   let (a,dec)=token(borrow).ok_or("invalid borrowed asset")?;
   let (b,_)=token(mid).ok_or("invalid middle asset")?;
   let balance=raw(entry,"vault_balance_raw")?;
   let v2=get(entry,"v2")?.as_object().ok_or("V2 pools missing")?;
   let v3=get(entry,"v3")?.as_object().ok_or("V3 pools missing")?;
   if v2.len()!=2||v3.len()!=ts.len()+1{return Err("unexpected pool keys".into())}
   let mut pool_addresses=BTreeSet::new();
   let mut v2_ok=BTreeMap::new();let mut v3_ok=BTreeMap::new();
   for venue in ["quick","sushi"]{
    let p=v2.get(venue).ok_or("V2 venue missing")?;
    if let Some(address)=pool(p,a,b,"v2",0)?{
     if !pool_addresses.insert(address){return Err("reused pool across venues".into())}
     v2_ok.insert(venue.to_string(),true);
    }else{v2_ok.insert(venue.to_string(),false);}
   }
   for (venue,kind,tier) in std::iter::once(("algebra".to_string(),"algebra",0u64)).chain(
       ts.iter().map(|t|(format!("uniswap_{t}"),"uniswap",*t))){
    let p=v3.get(&venue).ok_or("V3 venue missing")?;
    if let Some(address)=pool(p,a,b,kind,tier)?{
     if !pool_addresses.insert(address){return Err("reused pool across venues".into())}
     v3_ok.insert(venue,true);
    }else{v3_ok.insert(venue,false);}
   }
   let routes=items(entry,"quotes")?;
   if routes.len()!=2*(ts.len()+1)*ss.len()*2{return Err("incomplete route matrix".into())}
   let mut seen_routes=BTreeSet::new();
   for q in routes{
    let venue2=string(q,"v2")?;let venue3=string(q,"v3")?;let dir=string(q,"direction")?;
    let size=number(q,"size_units")?;
    if !v2_ok.contains_key(venue2)||!v3_ok.contains_key(venue3)||
      !["v2_then_v3","v3_then_v2"].contains(&dir)||!ss.contains(&size)||
      !seen_routes.insert((venue2.to_string(),venue3.to_string(),dir.to_string(),size)){
       return Err("invalid or duplicate quote route".into());
    }
    let principal=raw(q,"principal_raw")?;
    if principal!=U256::from(size)*U256::exp10(dec as usize){return Err("invalid principal/decimals".into())}
    let correct=(principal*fee+U256::exp10(18)-U256::one())/U256::exp10(18);
    if raw(q,"flash_fee_raw")?!=correct{return Err("incorrect Balancer fee".into())}
    summary.quotes+=1;
    let status=string(q,"status")?;
    if status!="OK"{
     if !get(q,"first_out_raw")?.is_null()||!get(q,"returned_raw")?.is_null(){return Err("skipped quote has output".into())}
     match status{
      "SKIP_NO_POOL_ACTIVITY" if !active_ids.contains(id)=>{},
      "SKIP_MISSING_POOL" if active_ids.contains(id)&&
        (!v2_ok[venue2]||!v3_ok[venue3])=>{},
      "SKIP_ZERO_V3_LIQUIDITY" if active_ids.contains(id)&&v2_ok[venue2]&&v3_ok[venue3]&&
        raw(&v3[venue3],"liquidity_raw")?.is_zero()=>{},
      "SKIP_V3_QUOTER_REVERT" if active_ids.contains(id)&&v2_ok[venue2]&&v3_ok[venue3]&&
        !raw(&v3[venue3],"liquidity_raw")?.is_zero()=>{},
      _=>return Err("incorrect skip reason".into()),
     }
     summary.skipped+=1;
     continue;
    }
    if !active_ids.contains(id)||!v2_ok[venue2]||!v3_ok[venue3]||
      raw(&v3[venue3],"liquidity_raw")?.is_zero(){return Err("OK without active/liquid pool".into())}
    let output=raw(q,"returned_raw")?;
    if raw(q,"first_out_raw")?.is_zero()||output.is_zero(){return Err("invalid output amounts".into())}
    if balance<principal{summary.vault_rejected+=1;}
    else if output<=principal+correct{summary.gross_rejected+=1;}
    else{summary.positive_unassessed+=1;}
   }
  }
 }
 if number(&v,"selected_log_count")?!=summary.events{return Err("log count mismatch".into())}
 summary.blocks=blocks.len();
 Ok(summary)
}

pub(crate) fn print_report(json:&str)->Result<(),String>{
 let s=assess(json)?;
 println!("STEP 11.3 — ACTIVITY-TRIGGERED V2/V3 HISTORICAL RESEARCH — READ-ONLY");
 println!("selected_blocks={} pool_activity_logs={} route_quotes={} gross_rejected={} vault_rejected={} skipped={} gross_positive_unassessed={}",
   s.blocks,s.events,s.quotes,s.gross_rejected,s.vault_rejected,s.skipped,s.positive_unassessed);
 println!("ALWAYS BLOCK: pool logs and mixed quotes are not transaction execution, gas-adjusted profit or inclusion evidence.");
 println!("NO PRIVATE KEY / NO SIGNER / NO BROADCAST / NO RELAY SUBMISSION");
 Ok(())
}

#[cfg(test)]
mod tests{
 use super::*;
 use serde_json::json;
 fn sample()->Value{
  let (t0,t1)=if DAI<WETH{(DAI,WETH)}else{(WETH,DAI)};
  let v2=json!({"address":"0x0000000000000000000000000000000000000001", "token0":t0,"token1":t1,"reserve0_raw":"100000","reserve1_raw":"100000"});
  let alg=json!({"address":"0x0000000000000000000000000000000000000002","token0":t0,"token1":t1,"liquidity_raw":"100000","fee_tier":null});
  let q=|ve:&str,vr:&str|json!({"v2":ve,"v3":vr,"direction":"v2_then_v3","size_units":1,"status":"OK",
   "principal_raw":"1000000000000000000","flash_fee_raw":"0", "first_out_raw":"100000", "returned_raw":"994000000000000000"});
  let mut qq=Vec::new();
  for venue in ["quick","sushi"]{
   for dir in ["v2_then_v3","v3_then_v2"]{
    let mut x=q(venue,"algebra");x["direction"]=json!(dir);qq.push(x);
   }
  }
  json!({"schema_version":1,"evidence_kind":"polygon_historical_pool_event_mixed_v2_v3_readonly","chain_id":137,
   "window":{"from_block":100,"to_block":150,"anchor_hash":"0x1111111111111111111111111111111111111111111111111111111111111111"},
   "requested_pairs":["DAI_WETH"],"tiers":[500],"sizes_whole_units":[1],"provider_role":"primary","selected_log_count":2,
   "selected_blocks":[{"number":120,"hash":"0x2222222222222222222222222222222222222222222222222222222222222222",
    "activity_event_count":2,"active_pairs":["DAI_WETH"],"fee_rate_wad":"0",
    "pairs":[{"pair_id":"DAI_WETH","borrow_symbol":"DAI","mid_symbol":"WETH",
       "vault_balance_raw":"10000000000000000000","v2":{"quick":v2,"sushi":null},
       "v3":{"algebra":alg,"uniswap_500":null},"quotes":qq}]}],
    "safety":{"writes_or_broadcasts_transactions":false,"uses_state_overrides":false,
     "proves_executor_eth_call":false,"proves_gas_or_net_profit":false,"proves_inclusion":false,
     "executor_supports_mixed_routes":false}})
 }
 fn make_valid()->Value{
  let mut v=sample();
  let r=v["selected_blocks"][0]["pairs"][0]["quotes"].as_array_mut().unwrap();
  let mut more=r.clone();
  for item in more.iter_mut(){item["v3"]=json!("uniswap_500");}
  r.append(&mut more);
  for q in r.iter_mut(){
   if q["v2"]=="sushi"||q["v3"]=="uniswap_500"{
    q["status"]=json!("SKIP_MISSING_POOL");q["first_out_raw"]=Value::Null;q["returned_raw"]=Value::Null;
   }
  }
  v
 }
 #[test]fn baseline_has_two_gross_losses(){let v=make_valid();let s=assess(&v.to_string()).unwrap();assert_eq!((s.gross_rejected,s.skipped),(2,6));}
 #[test]fn gross_positive_unassessed(){let mut v=make_valid();v["selected_blocks"][0]["pairs"][0]["quotes"][0]["returned_raw"]=json!("1001000000000000000");assert_eq!(assess(&v.to_string()).unwrap().positive_unassessed,1);}
 #[test]fn unknown_pair_rejected(){let mut v=make_valid();v["requested_pairs"][0]=json!("SCAM");assert!(assess(&v.to_string()).is_err());}
 #[test]fn unsafe_claim_rejected(){let mut v=make_valid();v["safety"]["executor_supports_mixed_routes"]=json!(true);assert!(assess(&v.to_string()).is_err());}
 #[test]fn malformed_duplicate_quote_rejected(){let mut v=make_valid();v["selected_blocks"][0]["pairs"][0]["quotes"][1]["direction"]=json!("v2_then_v3");assert!(assess(&v.to_string()).is_err());}
 #[test]fn wrong_fee_rejected(){let mut v=make_valid();v["selected_blocks"][0]["pairs"][0]["quotes"][0]["flash_fee_raw"]=json!("1");assert!(assess(&v.to_string()).is_err());}
 #[test]fn invented_success_without_pool_rejected(){let mut v=make_valid();let q=&mut v["selected_blocks"][0]["pairs"][0]["quotes"][2];q["status"]=json!("OK");q["first_out_raw"]=json!("123");q["returned_raw"]=json!("123");assert!(assess(&v.to_string()).is_err());}
 #[test]fn mismatched_logs_rejected(){let mut v=make_valid();v["selected_log_count"]=json!(9);assert!(assess(&v.to_string()).is_err());}
 #[test]fn invalid_block_hash_rejected(){let mut v=make_valid();v["selected_blocks"][0]["hash"]=json!("oops");assert!(assess(&v.to_string()).is_err());}
 #[test]fn zero_log_window_is_nontradable(){let mut v=make_valid();v["selected_blocks"]=json!([]);v["selected_log_count"]=json!(0);assert_eq!(assess(&v.to_string()).unwrap().quotes,0);}
}
