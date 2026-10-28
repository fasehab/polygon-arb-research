//! Step 11: Offline fail-closed multi-pair historical quote review.
//! JSON is an untrusted RPC-derived research artifact, not signed proof.
//! No method here authorizes submission or constructs transactions.
use ethers::types::U256;
use serde_json::Value;
use std::collections::BTreeSet;

const DAI: &str = "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";
const WETH: &str = "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619";
const USDC: &str = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174";
const VAULT: &str = "0xba12222222228d8ba445958a75a0704d566bf2c8";
const QUICK: &str = "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff";
const SUSHI: &str = "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506";
const Q_FACTORY: &str = "0x5757371414417b8c6caad45baef941abc7d3ab32";
const S_FACTORY: &str = "0xc35dadb65012ec5796536bd9864ed8773abc74c4";

fn get<'a>(v: &'a Value, key: &str) -> Result<&'a Value, String> {
    v.get(key).ok_or_else(||format!("missing {key}"))
}
fn str_at<'a>(v: &'a Value, key: &str) -> Result<&'a str,String> {
    get(v,key)?.as_str().ok_or_else(||format!("expected string {key}"))
}
fn eq_str(v: &Value, key: &str, expected: &str) -> Result<(),String> {
    if str_at(v,key)?!=expected {return Err(format!("unexpected {key}"));} Ok(())
}
fn u64_at(v: &Value,key: &str) -> Result<u64,String> {
    get(v,key)?.as_u64().ok_or_else(||format!("invalid integer field {key}"))
}
fn decimal(v: &Value,key: &str) -> Result<U256,String> {
    let s=str_at(v,key)?;
    if s.is_empty() || s.len()>78 || s.len()>1 && s.starts_with('0')
       || !s.bytes().all(|c|c.is_ascii_digit()) {
        return Err(format!("invalid raw decimal {key}"));
    }
    U256::from_dec_str(s).map_err(|_|format!("overflow {key}"))
}
fn address(s: &str) -> bool {
    s.len()==42 && s.starts_with("0x") && s[2..].bytes().all(|c|c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        && s[2..].bytes().any(|c|c!=b'0')
}
fn hash(s: &str) -> bool {
    s.len()==66 && s.starts_with("0x") && s[2..].bytes().all(|c|c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
       && s[2..].bytes().any(|c|c!=b'0')
}
fn token(sym: &str) -> Option<(&'static str,u32)> {
    match sym {"DAI"=>Some((DAI,18)),"WETH"=>Some((WETH,18)),"USDCe"=>Some((USDC,6)),_=>None}
}
fn pair_spec(id: &str) -> Option<(&'static str,&'static str)> {
    match id {"DAI_WETH"=>Some(("DAI","WETH")),
        "USDCe_WETH"=>Some(("USDCe","WETH")),
        "DAI_USDCe"=>Some(("DAI","USDCe")),_=>None}
}
fn check_snapshot(v: &Value, borrow: &str, mid: &str) -> Result<(String,U256,U256),String> {
    let pool=str_at(v,"address")?;
    if !address(pool) {return Err("invalid pool address".into());}
    let zero=str_at(v,"token0")?;
    let one=str_at(v,"token1")?;
    if !((zero==borrow && one==mid) || (zero==mid && one==borrow)) {
        return Err("pool token order mismatch".into());
    }
    let r0=decimal(v,"reserve0_raw")?;
    let r1=decimal(v,"reserve1_raw")?;
    if r0.is_zero()||r1.is_zero()||r0 >= (U256::one()<<112)||r1 >= (U256::one()<<112) {
        return Err("reserve out of bounds".into());
    }
    Ok((pool.into(), if zero==borrow {r0} else {r1}, if zero==mid {r0} else {r1}))
}

#[derive(Clone,Copy,Debug,PartialEq,Eq)]
pub(crate) enum Verdict { RejectGross, RejectVaultLiquidity, UnassessedNeedFullReplay }
impl Verdict { fn label(self)->&'static str {
    match self {Self::RejectGross=>"REJECT_GROSS_UNPROFITABLE",
      Self::RejectVaultLiquidity=>"REJECT_VAULT_LIQUIDITY",
      Self::UnassessedNeedFullReplay=>"UNASSESSED_NEEDS_UNMODIFIED_FORK_GAS_AND_INCLUSION"}
}}
#[derive(Debug,Clone)]
pub(crate) struct Candidate {
    pub(crate) block:u64, pub(crate) pair_id:String, pub(crate) route:String,
    pub(crate) principal:U256, pub(crate) returned:U256, pub(crate) fee:U256,
    pub(crate) verdict:Verdict,
}
#[derive(Debug)]
pub(crate) struct Summary {
    pub(crate) candidates:Vec<Candidate>,pub(crate) skipped:usize,
    pub(crate) blocks:usize,pub(crate) provider_fallback_blocks:usize,
}

pub(crate) fn assess(json: &str) -> Result<Summary,String> {
    let v: Value=serde_json::from_str(json).map_err(|_|"invalid JSON")?;
    if u64_at(&v,"schema_version")?!=1 || u64_at(&v,"chain_id")?!=137 {
        return Err("invalid schema or chain ID".into());
    }
    eq_str(&v,"evidence_kind","historical_v2_multi_pair_quotes_no_execution")?;
    for (key,expected) in [("vault",VAULT),("quick_router",QUICK),
         ("sushi_router",SUSHI),("quick_factory",Q_FACTORY),("sushi_factory",S_FACTORY)] {
        eq_str(&v,key,expected)?;
    }
    let safety=get(&v,"safety")?;
    for key in ["uses_state_overrides","writes_or_broadcasts_transactions",
        "proves_executor_eth_call","proves_gas_or_net_profit","proves_mempool_or_inclusion"] {
        if get(safety,key)?.as_bool()!=Some(false) {
            return Err(format!("unsafe evidence claim {key}"));
        }
    }
    let t=get(&v,"tokens")?.as_array().ok_or("invalid tokens")?;
    if t.len()!=3 {return Err("expected exactly 3 reviewed test candidates".into());}
    let mut symbols=BTreeSet::new();
    for item in t {
        let symbol=str_at(item,"symbol")?;
        let (contract,decimals)=token(symbol).ok_or("unreviewed token")?;
        eq_str(item,"address",contract)?;
        if u64_at(item,"decimals")?!=u64::from(decimals)||!symbols.insert(symbol.to_string()) {
            return Err("duplicate/invalid token metadata".into());
        }
    }
    let sizes=get(&v,"sizes_whole_units")?.as_array().ok_or("missing sizes")?;
    if sizes.is_empty()||sizes.len()>5 {return Err("trade size count invalid".into());}
    let mut requested=BTreeSet::new();
    for sz in sizes {
        let n=sz.as_u64().ok_or("invalid trade size")?;
        if n==0||n>1000||!requested.insert(n) {return Err("duplicate/out-of-bounds trade size".into());}
    }
    let blocks=get(&v,"blocks")?.as_array().ok_or("missing blocks")?;
    if blocks.is_empty()||blocks.len()>8 {return Err("block count out of bounds".into());}
    let mut seen_blocks=BTreeSet::new();
    let mut candidates=Vec::new();
    let mut skipped=0usize;
    let mut fallback_blocks=0usize;
    for block in blocks {
        let number=u64_at(block,"number")?;
        if number==0||!seen_blocks.insert(number) {return Err("duplicate or zero block".into());}
        if !hash(str_at(block,"hash")?) {return Err("invalid block hash".into());}
        match str_at(block,"provider_role")? {
            "primary"=>{},"fallback"=>fallback_blocks+=1,
            _=>return Err("unknown provider role".into()),
        }
        let fee_wad=decimal(block,"fee_rate_wad")?;
        if fee_wad>U256::exp10(16) {return Err("flash loan fee outside cap".into());}
        let pairs=get(block,"pairs")?.as_array().ok_or("missing pair array")?;
        if pairs.len()!=3 {return Err("expected three independent pair results for each block".into());}
        let mut found_pairs=BTreeSet::new();
        for pair in pairs {
            let pair_id=str_at(pair,"pair_id")?;
            let (borrow_sym,mid_sym)=pair_spec(pair_id).ok_or("unreviewed pair")?;
            if !found_pairs.insert(pair_id.to_string()) {return Err("duplicate pair result".into());}
            eq_str(pair,"borrow_symbol",borrow_sym)?;
            eq_str(pair,"mid_symbol",mid_sym)?;
            let (borrow,decimals)=token(borrow_sym).ok_or("unreviewed borrowed token")?;
            let (mid,_)=token(mid_sym).ok_or("unreviewed intermediate token")?;
            let balance=decimal(pair,"vault_balance_raw")?;
            let status=str_at(pair,"status")?;
            let qpool=get(pair,"quick_pool")?;
            let spool=get(pair,"sushi_pool")?;
            let q=get(pair,"quotes")?.as_array().ok_or("missing quotes")?;
            if status!="OK" {
                let status_valid=match status {
                  "SKIP_MISSING_QUICK_POOL"=>qpool.is_null(),
                  "SKIP_MISSING_SUSHI_POOL"=>!qpool.is_null() && spool.is_null(),
                  "SKIP_QUOTE_INCOMPLETE"=>!qpool.is_null() && !spool.is_null(),
                  _=>false,
                };
                if !status_valid||!q.is_empty() {return Err("invalid skip evidence or misleading partial quotes".into());}
                // Validate any observed pool, even when its partner is absent.
                if !qpool.is_null(){check_snapshot(qpool,borrow,mid)?;}
                if !spool.is_null(){check_snapshot(spool,borrow,mid)?;}
                skipped+=1;
                continue;
            }
            if qpool.is_null()||spool.is_null(){return Err("OK pair without both pools".into());}
            let quick=check_snapshot(qpool,borrow,mid)?;
            let sushi=check_snapshot(spool,borrow,mid)?;
            if quick.0==sushi.0 {return Err("same DEX pair address".into());}
            if q.len()!=sizes.len()*2 {return Err("incomplete bidirectional historical quotes".into());}
            let mut seen_quotes=BTreeSet::new();
            for item in q {
                let route=str_at(item,"route")?;
                if route!="quick_then_sushi" && route!="sushi_then_quick" {
                    return Err("invalid route".into());
                }
                let principal=decimal(item,"principal_raw")?;
                let first=decimal(item,"first_out_raw")?;
                let returned=decimal(item,"returned_raw")?;
                let fee=decimal(item,"flash_fee_raw")?;
                if first.is_zero()||returned.is_zero() {return Err("zero quote output".into());}
                // Every requested size/direction must occur exactly once.
                let mut known_size=false;
                for sz in &requested {
                    let expected=U256::from(*sz).checked_mul(U256::exp10(decimals as usize))
                        .ok_or("principal overflow")?;
                    if principal==expected {known_size=true;break;}
                }
                if !known_size||!seen_quotes.insert((principal.to_string(),route.to_string())) {
                    return Err("unknown size/duplicate quote".into());
                }
                let (first_pool,second_pool)=if route=="quick_then_sushi" {(&quick,&sushi)}else{(&sushi,&quick)};
                if first>=first_pool.2||returned>=second_pool.1 {
                    return Err("router output exceeds pool reserve".into());
                }
                let wad=U256::exp10(18);
                let expected_fee=principal.checked_mul(fee_wad)
                    .and_then(|x|x.checked_add(wad-U256::one()))
                    .ok_or("flash fee multiplication overflow")?/wad;
                if fee!=expected_fee {return Err("flash fee mismatch".into());}
                let repay=principal.checked_add(fee).ok_or("repayment overflow")?;
                let verdict=if principal>balance {Verdict::RejectVaultLiquidity}
                    else if returned<=repay {Verdict::RejectGross}
                    else {Verdict::UnassessedNeedFullReplay};
                candidates.push(Candidate{block:number,pair_id:pair_id.into(),route:route.into(),
                    principal,returned,fee,verdict});
            }
        }
    }
    Ok(Summary{candidates,skipped,blocks:blocks.len(),provider_fallback_blocks:fallback_blocks})
}

pub(crate) fn print_report(json:&str)->Result<(),String> {
    let report=assess(json)?;
    println!("STEP 11 — MULTI-PAIR MULTI-BLOCK HISTORICAL RESEARCH — READ-ONLY");
    println!("blocks={} skipped_pair_snapshots={} fallback_blocks={}",
             report.blocks,report.skipped,report.provider_fallback_blocks);
    for c in &report.candidates {
        println!("block={} pair={} route={} principal_raw={} returned_raw={} flash_fee_raw={} VERDICT={}",
            c.block,c.pair_id,c.route,c.principal,c.returned,c.fee,c.verdict.label());
    }
    println!("SUMMARY quoted={} rejected_gross={} rejected_vault={} gross_positive_unassessed={}",
        report.candidates.len(),
        report.candidates.iter().filter(|x|x.verdict==Verdict::RejectGross).count(),
        report.candidates.iter().filter(|x|x.verdict==Verdict::RejectVaultLiquidity).count(),
        report.candidates.iter().filter(|x|x.verdict==Verdict::UnassessedNeedFullReplay).count());
    println!("ALWAYS BLOCK: snapshots do not prove executor success, same-state gas, inclusion or economic profit.");
    println!("NO PRIVATE KEY / NO SIGNER / NO BROADCAST / NO RELAY SUBMISSION");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn pool(x:&str,borrow:&str,mid:&str)->Value {
        serde_json::json!({"address":x,"token0":mid,"token1":borrow,
          "reserve0_raw":"1000000000000000000000","reserve1_raw":"900000000000000000000000"})
    }
    fn fixture()->Value {
        let qp="0x1111111111111111111111111111111111111111";
        let sp="0x2222222222222222222222222222222222222222";
        let mut pairs=Vec::new();
        for (id,borrow,mid,dec) in [("DAI_WETH",DAI,WETH,18usize),
                         ("USDCe_WETH",USDC,WETH,6usize),
                         ("DAI_USDCe",DAI,USDC,18usize)] {
            let sym=if borrow==DAI {"DAI"}else{"USDCe"};
            let msym=if mid==WETH {"WETH"}else{"USDCe"};
            let principal=U256::exp10(dec);
            // For DAI_USDCe mid reserve is huge in raw units; these are illustrative offline fixtures.
            let records=vec![
                serde_json::json!({"route":"quick_then_sushi","principal_raw":principal.to_string(),
                   "first_out_raw":"1000","returned_raw":(principal-U256::one()).to_string(),"flash_fee_raw":"0"}),
                serde_json::json!({"route":"sushi_then_quick","principal_raw":principal.to_string(),
                   "first_out_raw":"1000","returned_raw":(principal-U256::one()).to_string(),"flash_fee_raw":"0"})];
            pairs.push(serde_json::json!({"pair_id":id,"borrow_symbol":sym,"mid_symbol":msym,
                 "vault_balance_raw":"100000000000000000000000000","status":"OK",
                 "quick_pool":pool(qp,borrow,mid),"sushi_pool":pool(sp,borrow,mid),"quotes":records}));
        }
        serde_json::json!({"schema_version":1,"chain_id":137,
            "evidence_kind":"historical_v2_multi_pair_quotes_no_execution",
            "vault":VAULT,"quick_router":QUICK,"sushi_router":SUSHI,
            "quick_factory":Q_FACTORY,"sushi_factory":S_FACTORY,
            "tokens":[{"symbol":"DAI","address":DAI,"decimals":18},
                      {"symbol":"WETH","address":WETH,"decimals":18},
                      {"symbol":"USDCe","address":USDC,"decimals":6}],
            "sizes_whole_units":[1],"blocks":[{"number":96179857,
            "hash":format!("0x{}","a".repeat(64)),"fee_rate_wad":"0","provider_role":"primary","pairs":pairs}],
            "safety":{"uses_state_overrides":false,"writes_or_broadcasts_transactions":false,
                  "proves_executor_eth_call":false,"proves_gas_or_net_profit":false,
                  "proves_mempool_or_inclusion":false}})
    }
    #[test] fn valid_three_pair_baseline_rejects_gross_negative() {
        let r=assess(&fixture().to_string()).unwrap();
        assert_eq!(r.candidates.len(),6);
        assert_eq!(r.skipped,0);
        assert!(r.candidates.iter().all(|c|c.verdict==Verdict::RejectGross));
    }
    #[test] fn gross_positive_is_never_execution_approved() {
        let mut v=fixture();
        v["blocks"][0]["pairs"][0]["quotes"][0]["returned_raw"]=serde_json::json!("1000000000000000001");
        let r=assess(&v.to_string()).unwrap();
        assert_eq!(r.candidates[0].verdict,Verdict::UnassessedNeedFullReplay);
    }
    #[test] fn bad_chain_or_contract_or_asset_rejected() {
        let mut v=fixture();v["chain_id"]=serde_json::json!(1);assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();v["quick_factory"]=serde_json::json!("0x123");assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();v["tokens"][2]["address"]=serde_json::json!(DAI);assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn bad_block_hash_and_replayed_block_rejected() {
        let mut v=fixture();v["blocks"][0]["hash"]=serde_json::json!("0x123");assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();let copied=v["blocks"][0].clone();v["blocks"].as_array_mut().unwrap().push(copied);
        assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn duplicate_quote_or_missing_route_rejected() {
        let mut v=fixture();v["blocks"][0]["pairs"][0]["quotes"][1]["route"]=serde_json::json!("quick_then_sushi");
        assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();v["blocks"][0]["pairs"][0]["quotes"].as_array_mut().unwrap().pop();
        assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn unsafe_flag_and_fee_forgery_rejected() {
        let mut v=fixture();v["safety"]["writes_or_broadcasts_transactions"]=serde_json::json!(true);
        assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();v["blocks"][0]["pairs"][0]["quotes"][0]["flash_fee_raw"]=serde_json::json!("1");
        assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn malformed_pool_and_overlarge_quote_rejected() {
        let mut v=fixture();v["blocks"][0]["pairs"][0]["quick_pool"]["token0"]=serde_json::json!(DAI);
        assert!(assess(&v.to_string()).is_err());
        let mut v=fixture();v["blocks"][0]["pairs"][0]["quotes"][0]["first_out_raw"]=
            serde_json::json!("99999999999999999999999");
        assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn valid_missing_pool_is_visible_and_nontradable() {
        let mut v=fixture();v["blocks"][0]["pairs"][1]["status"]=serde_json::json!("SKIP_MISSING_QUICK_POOL");
        v["blocks"][0]["pairs"][1]["quick_pool"]=Value::Null;
        v["blocks"][0]["pairs"][1]["quotes"]=serde_json::json!([]);
        let s=assess(&v.to_string()).unwrap();assert_eq!(s.skipped,1);assert_eq!(s.candidates.len(),4);
    }
    #[test] fn invalid_skipped_pair_cannot_hide_positive_quote() {
        let mut v=fixture();v["blocks"][0]["pairs"][1]["status"]=serde_json::json!("SKIP_QUOTE_INCOMPLETE");
        assert!(assess(&v.to_string()).is_err());
    }
    #[test] fn vault_liquidity_shortfall_rejected() {
        let mut v=fixture();v["blocks"][0]["pairs"][0]["vault_balance_raw"]=serde_json::json!("0");
        let report=assess(&v.to_string()).unwrap();
        assert_eq!(report.candidates[0].verdict,Verdict::RejectVaultLiquidity);
    }
}
