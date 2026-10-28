//! Step 11.2: strictly read-only, offline, fail-closed assessment of V3 quoter JSON.
//! No signing, transaction construction, or execution authorization.
use ethers::types::U256;
use serde_json::Value;
use std::collections::BTreeSet;

const ALG_FACTORY:&str="0x411b0facc3489691f28ad58c47006af5e3ab3a28";
const ALG_QUOTER:&str="0xa15f0d7377b2a0c0c10db057f641bed21028fc89";
const UNI_FACTORY:&str="0x1f98431c8ad98523631ae4a59f267346ea31f984";
const UNI_QUOTER:&str="0x61ffe014ba17989e743c5f6cb21bf9697530b21e";
const VAULT:&str="0xba12222222228d8ba445958a75a0704d566bf2c8";
const DAI:&str="0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";
const WETH:&str="0x7ceb23fd6bc0add59e62ac25578270cff1b9f619";
const USDC:&str="0x2791bca1f2de4661ed88a30c99a7a9449aa84174";
fn get<'a>(v:&'a Value,k:&str)->Result<&'a Value,String>{v.get(k).ok_or_else(||format!("missing {k}"))}
fn string<'a>(v:&'a Value,k:&str)->Result<&'a str,String>{get(v,k)?.as_str().ok_or_else(||format!("invalid string {k}"))}
fn number(v:&Value,k:&str)->Result<u64,String>{get(v,k)?.as_u64().ok_or_else(||format!("invalid unsigned {k}"))}
fn expect(v:&Value,k:&str,s:&str)->Result<(),String>{if string(v,k)?==s{Ok(())}else{Err(format!("unexpected {k}"))}}
fn raw(v:&Value,k:&str)->Result<U256,String>{
 let s=string(v,k)?;
 if s.is_empty()||s.len()>78||(s.len()>1&&s.starts_with('0'))||!s.bytes().all(|c|c.is_ascii_digit()){
  return Err(format!("invalid raw value {k}"));
 }
 U256::from_dec_str(s).map_err(|_|format!("overflow {k}"))
}
fn valid_hex(s:&str,l:usize)->bool{s.len()==l&&s.starts_with("0x")&&s[2..].bytes().all(|c|c.is_ascii_hexdigit()&&!c.is_ascii_uppercase())&&s[2..].bytes().any(|c|c!=b'0')}
fn token(s:&str)->Option<(&'static str,u64)>{match s{"DAI"=>Some((DAI,18)),"WETH"=>Some((WETH,18)),"USDCe"=>Some((USDC,6)),_=>None}}
fn pair(s:&str)->Option<(&'static str,&'static str)>{match s{"DAI_WETH"=>Some(("DAI","WETH")),"USDCe_WETH"=>Some(("USDCe","WETH")),"DAI_USDCe"=>Some(("DAI","USDCe")),_=>None}}
fn pool(v:&Value,a:&str,b:&str,fee:Option<u64>)->Result<U256,String>{
 if !valid_hex(string(v,"address")?,42){return Err("pool address malformed".into());}
 let t0=string(v,"token0")?;let t1=string(v,"token1")?;
 if !((t0==a&&t1==b)||(t0==b&&t1==a))||t0>=t1{return Err("pool tokens/order mismatch".into());}
 let liq=raw(v,"liquidity_raw")?;
 if liq >= (U256::one()<<128){return Err("pool liquidity overflow".into());}
 if let Some(tier)=fee{
  if number(v,"fee_tier")?!=tier{return Err("pool fee-tier mismatch".into());}
 } else if !get(v,"fee_tier")?.is_null(){return Err("Algebra pool cannot have static tier".into());}
 Ok(liq)
}

pub(crate) struct Summary{pub blocks:usize,pub gross_rejected:usize,pub gross_positive:usize,pub skipped:usize,pub vault_rejected:usize,pub fallback:usize}

pub(crate) fn assess(json:&str)->Result<Summary,String>{
 let v:Value=serde_json::from_str(json).map_err(|_|"invalid json")?;
 if number(&v,"schema_version")?!=1||number(&v,"chain_id")?!=137{return Err("wrong version/chain".into());}
 for (k,e) in [("evidence_kind","historical_polygon_v3_algebra_uniswap_readonly"),
  ("algebra_factory",ALG_FACTORY),("algebra_quoter",ALG_QUOTER),
  ("uniswap_factory",UNI_FACTORY),("uniswap_quoter",UNI_QUOTER),("vault",VAULT)]{
  expect(&v,k,e)?;
 }
 let safety=get(&v,"safety")?;
 for key in ["uses_state_overrides","writes_or_broadcasts_transactions","proves_executor_eth_call",
     "proves_gas_or_net_profit","proves_mempool_or_inclusion","executor_supports_v3"]{
  if get(safety,key)?.as_bool()!=Some(false){return Err(format!("unsafe flag {key}"));}
 }
 let tokens=get(&v,"tokens")?.as_array().ok_or("tokens missing")?;
 if tokens.len()!=3{return Err("unexpected token count".into());}
 let mut seen_tokens=BTreeSet::new();
 for t in tokens{
  let symbol=string(t,"symbol")?;let (addr,dec)=token(symbol).ok_or("unvetted token")?;
  expect(t,"address",addr)?;
  if number(t,"decimals")?!=dec||!seen_tokens.insert(symbol){return Err("token metadata invalid".into());}
 }
 let sizes=get(&v,"sizes_whole_units")?.as_array().ok_or("missing sizes")?;
 if sizes.is_empty()||sizes.len()>4{return Err("bad size count".into());}
 let mut unique_sizes=BTreeSet::new();
 for sz in sizes{let n=sz.as_u64().ok_or("size invalid")?;
  if n==0||n>1000||!unique_sizes.insert(n){return Err("size duplicated or unsupported".into());}}
 let tiers=get(&v,"tiers")?.as_array().ok_or("missing fee tiers")?;
 if tiers.is_empty()||tiers.len()>2{return Err("fee tiers count invalid".into());}
 let mut unique_tiers=BTreeSet::new();
 for tier in tiers{let n=tier.as_u64().ok_or("invalid tier")?;
  if ![500u64,3000].contains(&n)||!unique_tiers.insert(n){return Err("invalid or repeated tier".into());}}
 let blocks=get(&v,"blocks")?.as_array().ok_or("missing blocks")?;
 if blocks.is_empty()||blocks.len()>6{return Err("invalid block count".into());}
 let mut sb=BTreeSet::new();
 let (mut rejected,mut positive,mut skipped,mut vault_rejected,mut fallback)=(0,0,0,0,0);
 for block in blocks{
  let height=number(block,"number")?;
  if height==0||!sb.insert(height)||!valid_hex(string(block,"hash")?,66){return Err("invalid block identity".into());}
  match string(block,"provider_role")?{"primary"=>{},"fallback"=>fallback+=1,_=>return Err("unknown provider".into())}
  let fee_wad=raw(block,"fee_rate_wad")?;
  if fee_wad>U256::exp10(16){return Err("flash fee out of bounds".into());}
  let pp=get(block,"pairs")?.as_array().ok_or("missing pairs")?;
  if pp.len()!=3{return Err("expected three pairs per block".into());}
  let mut pairs=BTreeSet::new();
  for p in pp{
   let id=string(p,"pair_id")?;
   let (borrow,mid)=pair(id).ok_or("unvetted pair")?;
   if !pairs.insert(id){return Err("duplicate pair".into());}
   expect(p,"borrow_symbol",borrow)?; expect(p,"mid_symbol",mid)?;
   let (a,dec)=token(borrow).ok_or("borrow invalid")?;let (b,_)=token(mid).ok_or("mid invalid")?;
   let balance=raw(p,"vault_balance_raw")?;
   let ap=get(p,"algebra_pool")?;
   let alg_liq=if ap.is_null(){None}else{Some(pool(ap,a,b,None)?)};
   let unis=get(p,"uniswap_pools")?.as_object().ok_or("missing uniswap pools")?;
   if unis.len()!=unique_tiers.len(){return Err("uniswap pool tiers mismatch".into());}
   let mut lp=std::collections::BTreeMap::new();
   for tier in &unique_tiers{
    let key=tier.to_string();let uv=unis.get(&key).ok_or("missing tier pool")?;
    let liq=if uv.is_null(){None}else{Some(pool(uv,a,b,Some(*tier))?)};
    if let Some(u)=uv.get("address").and_then(Value::as_str){
      if ap.get("address").and_then(Value::as_str)==Some(u){return Err("both protocols refer to same pool".into());}
    }
    lp.insert(*tier,liq);
   }
   let q=get(p,"quotes")?.as_array().ok_or("quotes missing")?;
   if q.len()!=unique_tiers.len()*unique_sizes.len()*2{return Err("missing/extra route quotes".into());}
   let mut routes=BTreeSet::new();
   for qr in q{
    let tier=number(qr,"tier")?;let sz=number(qr,"size_units")?;
    if !unique_tiers.contains(&tier)||!unique_sizes.contains(&sz){return Err("unrequested quote".into());}
    let dir=string(qr,"direction")?;
    if !["algebra_then_uniswap","uniswap_then_algebra"].contains(&dir){return Err("unsupported V3 route".into());}
    if !routes.insert((tier,sz,dir)){return Err("duplicate quote".into());}
    let principal=raw(qr,"principal_raw")?;
    let expected=U256::from(sz)*U256::exp10(dec as usize);
    if principal!=expected{return Err("amount/decimals mismatch".into());}
    let flash=raw(qr,"flash_fee_raw")?;
    let correct=(principal*fee_wad+U256::exp10(18)-U256::one())/U256::exp10(18);
    if flash!=correct{return Err("forged flash loan fee".into());}
    let status=string(qr,"status")?;
    let alc=alg_liq;let unc=lp[&tier];
    if status!="OK"{
      for k in ["first_out_raw","returned_raw","algebra_dynamic_fee_raw","uniswap_quoter_gas_hint"] {
        if !get(qr,k)?.is_null(){return Err("skipped quote contains misleading partial output".into());}
      }
      if status=="SKIP_MISSING_POOL"&&(alc.is_none()||unc.is_none()){} else
      if status=="SKIP_ZERO_ACTIVE_LIQUIDITY"&&alc.is_some()&&unc.is_some()&&
        (alc==Some(U256::zero())||unc==Some(U256::zero())){} else
      if status=="SKIP_QUOTER_REVERT"&&alc.is_some()&&unc.is_some()&&
        alc!=Some(U256::zero())&&unc!=Some(U256::zero()){} else{
        return Err("unsupported or contradictory skip status".into());
      }
      skipped+=1;continue;
    }
    if alc.is_none()||unc.is_none()||alc==Some(U256::zero())||unc==Some(U256::zero()){
      return Err("OK without live pools and liquidity".into());
    }
    let first=raw(qr,"first_out_raw")?;
    let returned=raw(qr,"returned_raw")?;
    let alg_fee=raw(qr,"algebra_dynamic_fee_raw")?;
    let hint=raw(qr,"uniswap_quoter_gas_hint")?;
    if first.is_zero()||returned.is_zero()||alg_fee>=U256::from(10000u64)||hint.is_zero(){return Err("invalid quoter values".into());}
    if balance<principal {vault_rejected+=1;}
    else if returned<=principal+flash {rejected+=1;}
    else {positive+=1;}
   }
  }
 }
 Ok(Summary{blocks:blocks.len(),gross_rejected:rejected,gross_positive:positive,skipped,vault_rejected,fallback})
}

pub(crate) fn print_report(json:&str)->Result<(),String>{
 let s=assess(json)?;
 println!("STEP 11.2 — POLYGON V3 ALGEBRA / UNISWAP HISTORICAL READ-ONLY");
 println!("blocks={} gross_rejected={} vault_rejected={} skipped={} gross_positive_unassessed={} fallback_blocks={}",
  s.blocks,s.gross_rejected,s.vault_rejected,s.skipped,s.gross_positive,s.fallback);
 println!("ALWAYS BLOCK: existing Solidity executor is V2-only; this V3 report does not prove executable net profit.");
 println!("NO SIGNING / NO BROADCAST / NO RELAY SUBMISSION");
 Ok(())
}

#[cfg(test)]
mod tests{
 use super::*;
 use serde_json::json;
 fn sample()->Value{
  let a=DAI;let b=WETH;
  let (t0,t1)=if a<b{(a,b)}else{(b,a)};
  let pool_a=json!({"address":"0x0000000000000000000000000000000000000001", "token0":t0,"token1":t1,"liquidity_raw":"10000","fee_tier":null});
  let pool_u=json!({"address":"0x0000000000000000000000000000000000000002", "token0":t0,"token1":t1,"liquidity_raw":"10000","fee_tier":500});
  let qp=|pair:&str,br:&str,mi:&str|{
   let mut r=Vec::new();
   for d in ["algebra_then_uniswap","uniswap_then_algebra"]{
    r.push(json!({"tier":500,"direction":d,"size_units":1,"status":"SKIP_MISSING_POOL",
     "principal_raw":if br=="USDCe"{"1000000"}else{"1000000000000000000"},
     "flash_fee_raw":"0","first_out_raw":null,"returned_raw":null,
     "algebra_dynamic_fee_raw":null,"uniswap_quoter_gas_hint":null}));
   }
   json!({"pair_id":pair,"borrow_symbol":br,"mid_symbol":mi,"vault_balance_raw":"1000000000000000000000",
    "algebra_pool":null,"uniswap_pools":{"500":null},"quotes":r})
  };
  let mut p=qp("DAI_WETH","DAI","WETH");
  p["algebra_pool"]=pool_a;
  p["uniswap_pools"]["500"]=pool_u;
  for qr in p["quotes"].as_array_mut().unwrap(){
   qr["status"]=json!("OK");qr["first_out_raw"]=json!("1000000000000");
   qr["returned_raw"]=json!("994000000000000000");
   qr["algebra_dynamic_fee_raw"]=json!("300");qr["uniswap_quoter_gas_hint"]=json!("65000");
  }
  json!({"schema_version":1,"evidence_kind":"historical_polygon_v3_algebra_uniswap_readonly",
   "chain_id":137,"algebra_factory":ALG_FACTORY,"algebra_quoter":ALG_QUOTER,
   "uniswap_factory":UNI_FACTORY,"uniswap_quoter":UNI_QUOTER,"vault":VAULT,
   "tokens":[{"symbol":"DAI","address":DAI,"decimals":18},
     {"symbol":"WETH","address":WETH,"decimals":18},
     {"symbol":"USDCe","address":USDC,"decimals":6}],
   "tiers":[500],"sizes_whole_units":[1],
   "blocks":[{"number":96179857,"hash":"0x1111111111111111111111111111111111111111111111111111111111111111",
    "provider_role":"primary","fee_rate_wad":"0","pairs":[p,
      qp("USDCe_WETH","USDCe","WETH"),qp("DAI_USDCe","DAI","USDCe")]}],
   "safety":{"uses_state_overrides":false,"writes_or_broadcasts_transactions":false,
    "proves_executor_eth_call":false,"proves_gas_or_net_profit":false,
    "proves_mempool_or_inclusion":false,"executor_supports_v3":false}})
 }
 #[test]fn rejects_gross_loss(){let v=sample();let s=assess(&v.to_string()).unwrap();assert_eq!(s.gross_rejected,2);assert_eq!(s.skipped,4);}
 #[test]fn positive_is_unassessed(){let mut v=sample();v["blocks"][0]["pairs"][0]["quotes"][0]["returned_raw"]=json!("1001000000000000000");let s=assess(&v.to_string()).unwrap();assert_eq!(s.gross_positive,1);}
 #[test]fn rejects_unsafe_executor_claim(){let mut v=sample();v["safety"]["executor_supports_v3"]=json!(true);assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_quote_duplicate(){let mut v=sample();v["blocks"][0]["pairs"][0]["quotes"][1]["direction"]=json!("algebra_then_uniswap");assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_bad_pool_fee(){let mut v=sample();v["blocks"][0]["pairs"][0]["uniswap_pools"]["500"]["fee_tier"]=json!(3000);assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_changed_pool_token(){let mut v=sample();v["blocks"][0]["pairs"][0]["algebra_pool"]["token0"]=json!(USDC);assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_wrong_fee(){let mut v=sample();v["blocks"][0]["fee_rate_wad"]=json!("1000000000000000");assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_broken_skip(){let mut v=sample();v["blocks"][0]["pairs"][1]["quotes"][0]["returned_raw"]=json!("2");assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_fake_new_pair(){let mut v=sample();v["blocks"][0]["pairs"][2]["pair_id"]=json!("SCAM_USDC");assert!(assess(&v.to_string()).is_err());}
 #[test]fn rejects_duplicate_block(){let mut v=sample();let b=v["blocks"][0].clone();v["blocks"].as_array_mut().unwrap().push(b);assert!(assess(&v.to_string()).is_err());}
}
