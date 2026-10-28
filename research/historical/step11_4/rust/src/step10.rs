//! Step 10: Offline, fail-closed verification of a pinned historical quote snapshot.
//! Not execution authorization. Quote records are NOT signed RPC attestations.
use ethers::types::U256;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

const DAI: &str = "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";
const WETH: &str = "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619";
const QUICK: &str = "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff";
const SUSHI: &str = "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506";
const QUICK_FACTORY: &str = "0x5757371414417b8c6caad45baef941abc7d3ab32";
const SUSHI_FACTORY: &str = "0xc35dadb65012ec5796536bd9864ed8773abc74c4";
const VAULT: &str = "0xba12222222228d8ba445958a75a0704d566bf2c8";
const QUICK_PAIR: &str = "0x4a35582a710e1f4b2030a3f826da20bfb6703c09";
const SUSHI_PAIR: &str = "0x6ff62bfb8c12109e8000935a6de54dad83a4f39f";

fn field<'a>(value: &'a Value, key: &str) -> Result<&'a Value, String> {
    value.get(key).ok_or_else(|| format!("missing field {key}"))
}
fn literal(value: &Value, key: &str, expected: &str) -> Result<(), String> {
    let actual = field(value,key)?.as_str().ok_or(format!("invalid {key}"))?;
    if actual != expected { return Err(format!("{key} is not pinned or verified")); }
    Ok(())
}
fn number(value: &Value, key: &str) -> Result<U256, String> {
    let s = field(value,key)?.as_str().ok_or(format!("{key} must be a raw decimal string"))?;
    if s.is_empty() || s.len() > 78 || (s.len() > 1 && s.starts_with('0')) || !s.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("invalid decimal integer {key}"));
    }
    U256::from_dec_str(s).map_err(|_|format!("out-of-range integer {key}"))
}
fn check_pair(pair: &Value, expected: &str) -> Result<(),String> {
    literal(pair,"address",expected)?;
    let a = field(pair,"token0")?.as_str().ok_or("missing pool token0")?;
    let b = field(pair,"token1")?.as_str().ok_or("missing pool token1")?;
    if !((a==DAI && b==WETH) || (a==WETH && b==DAI)) {return Err("unexpected pair tokens".into());}
    let r0 = number(pair,"reserve0_raw")?;
    let r1 = number(pair,"reserve1_raw")?;
    if r0.is_zero() || r1.is_zero() || r0 >= (U256::one()<<112) || r1 >= (U256::one()<<112) {
        return Err("invalid historical reserves".into());
    }
    Ok(())
}

#[derive(Debug,PartialEq,Eq)]
pub(crate) enum Decision { RejectGross, UnassessedNeedFork, RejectLiquidity }
impl Decision { fn label(&self)-> &'static str {match self {
    Self::RejectGross => "REJECT_GROSS_UNPROFITABLE",
    Self::UnassessedNeedFork => "UNASSESSED_REQUIRES_FULL_FORK_ETH_CALL_AND_GAS",
    Self::RejectLiquidity => "REJECT_VAULT_LIQUIDITY",
}}}
#[derive(Debug)]
pub(crate) struct Route { pub route: String, pub principal: U256, pub first: U256, pub returned: U256, pub fee: U256, pub decision: Decision }

pub(crate) fn assess(json: &str) -> Result<Vec<Route>,String> {
    let root: Value = serde_json::from_str(json).map_err(|_| "invalid evidence JSON")?;
    if field(&root,"schema_version")?.as_u64()!=Some(1)
        || field(&root,"chain_id")?.as_u64()!=Some(137)
        || field(&root,"block")?.as_u64()!=Some(96_179_857) {
        return Err("mismatched RPC chain, schema or historical fork block".into());
    }
    literal(&root,"evidence_kind","unmodified_historical_v2_quotes_only")?;
    for (key,value) in [("borrow_token",DAI),("intermediate_token",WETH),
        ("vault",VAULT),("quick_router",QUICK),("sushi_router",SUSHI),
        ("quick_factory",QUICK_FACTORY),("sushi_factory",SUSHI_FACTORY)] { literal(&root,key,value)?; }
    check_pair(field(&root,"quick_pair")?,QUICK_PAIR)?;
    check_pair(field(&root,"sushi_pair")?,SUSHI_PAIR)?;
    let saf = field(&root,"safety")?;
    if field(saf,"uses_state_overrides")?.as_bool()!=Some(false)
        || field(saf,"writes_or_broadcasts_transactions")?.as_bool()!=Some(false)
        || field(saf,"proves_executor_eth_call")?.as_bool()!=Some(false)
        || field(saf,"proves_gas_or_net_profit")?.as_bool()!=Some(false) {
        return Err("unsafe or incorrectly represented evidence".into());
    }
    let vault = number(&root,"vault_dai_balance_raw")?;
    if vault.is_zero() {return Err("Vault liquidity unavailable".into());}
    let fee_wad=number(&root,"fee_rate_wad")?;
    let wad=U256::exp10(18);
    if fee_wad>U256::exp10(16) {return Err("unexpected historical flash fee".into());}
    let inputs=field(&root,"quotes")?.as_array().ok_or("missing historical quote array")?;
    if inputs.is_empty() || inputs.len()>24 {return Err("quote count out of bounds".into());}
    let mut seen=BTreeSet::new();
    let mut route_groups=BTreeMap::<String,u8>::new();
    let mut assessed=Vec::new();
    for item in inputs {
        let route=field(item,"route")?.as_str().ok_or("missing route")?;
        if route!="quick_then_sushi" && route!="sushi_then_quick" {return Err("unknown DEX route".into());}
        let principal=number(item,"principal_raw")?;
        let first=number(item,"first_out_weth_raw")?;
        let returned=number(item,"second_out_dai_raw")?;
        let fee=number(item,"flash_fee_raw")?;
        if principal.is_zero() || principal>U256::exp10(21) || first.is_zero() || returned.is_zero() {
            return Err("zero or out-of-range quoted trade".into());
        }
        let expected_fee=principal.checked_mul(fee_wad)
            .and_then(|v|v.checked_add(wad-U256::one()))
            .ok_or("fee arithmetic overflow")? / wad;
        if fee!=expected_fee {return Err("reported flash fee is inconsistent with historical fee".into());}
        let grp=principal.to_string();
        if !seen.insert((grp.clone(),route.to_string())) {return Err("duplicate route quote".into());}
        let direction=if route=="quick_then_sushi" {1u8} else {2u8};
        *route_groups.entry(grp).or_insert(0) |= direction;
        let decision=if principal > vault { Decision::RejectLiquidity }
            else if returned <= principal.checked_add(fee).ok_or("repayment arithmetic overflow")? {
                Decision::RejectGross
            } else { Decision::UnassessedNeedFork };
        assessed.push(Route{route:route.into(),principal,first,returned,fee,decision});
    }
    if route_groups.values().any(|v|*v!=3) {return Err("both router directions must be present for every principal".into());}
    Ok(assessed)
}

pub(crate) fn print_report(json: &str) -> Result<(),String> {
    let routes=assess(json)?;
    println!("STEP 10 — UNMODIFIED HISTORICAL QUOTE ASSESSMENT (READ-ONLY)");
    println!("Pinned Polygon 137 block 96179857, DAI/WETH on QuickSwap V2 and SushiSwap V2");
    for r in &routes {
        println!("route={} principal_dai_raw={} first_out_weth_raw={} returned_dai_raw={} fee_raw={} VERDICT={}",
            r.route,r.principal,r.first,r.returned,r.fee,r.decision.label());
    }
    let gross_rejected=routes.iter().filter(|r|r.decision==Decision::RejectGross).count();
    let needs_fork=routes.iter().filter(|r|r.decision==Decision::UnassessedNeedFork).count();
    println!("SUMMARY quote_records={} gross_rejected={} require_fork_and_gas={} ",routes.len(),gross_rejected,needs_fork);
    println!("CAUTION: raw JSON is an offline report, not a signed RPC proof. Independently run the unchanged-state Foundry tests.");
    println!("ALWAYS BLOCK: historical quotes do not prove a profitable trade, actual executor eth_call, gas or inclusion.");
    println!("NO PRIVATE KEY / NO SIGNER / NO BROADCAST");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn base() -> Value {
        serde_json::json!({
            "schema_version":1,"chain_id":137,"block":96179857,"evidence_kind":"unmodified_historical_v2_quotes_only",
            "borrow_token":DAI,"intermediate_token":WETH,"vault":VAULT,"quick_router":QUICK,"sushi_router":SUSHI,
            "quick_factory":QUICK_FACTORY,"sushi_factory":SUSHI_FACTORY,
            "quick_pair":{"address":QUICK_PAIR,"token0":WETH,"token1":DAI,"reserve0_raw":"12411600611230105507","reserve1_raw":"28870579914812110757142"},
            "sushi_pair":{"address":SUSHI_PAIR,"token0":WETH,"token1":DAI,"reserve0_raw":"18646138739345391071","reserve1_raw":"43378384500059261218960"},
            "vault_dai_balance_raw":"100000000000000000000", "fee_rate_wad":"0",
            "safety":{"uses_state_overrides":false,"writes_or_broadcasts_transactions":false,"proves_executor_eth_call":false,"proves_gas_or_net_profit":false},
            "quotes":[
             {"route":"quick_then_sushi","principal_raw":"5000000000000000000","first_out_weth_raw":"2000000000000000","second_out_dai_raw":"4900000000000000000","flash_fee_raw":"0"},
             {"route":"sushi_then_quick","principal_raw":"5000000000000000000","first_out_weth_raw":"2000000000000000","second_out_dai_raw":"4950000000000000000","flash_fee_raw":"0"}
            ]})
    }
    #[test] fn unmodified_unprofitable_quotes_rejected() {
        let routes=assess(&base().to_string()).unwrap();
        assert_eq!(routes.len(),2);
        assert!(routes.iter().all(|x|x.decision==Decision::RejectGross));
    }
    #[test] fn positive_quote_never_approved_without_fork() {
        let mut e=base();e["quotes"][0]["second_out_dai_raw"]=Value::String("5100000000000000000".into());
        assert_eq!(assess(&e.to_string()).unwrap()[0].decision,Decision::UnassessedNeedFork);
    }
    #[test] fn chain_or_block_mismatch_fails_closed() {
        let mut e=base();e["chain_id"]=serde_json::json!(1);assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["block"]=serde_json::json!(96179858);assert!(assess(&e.to_string()).is_err());
    }
    #[test] fn fake_pool_or_missing_reserve_rejected() {
        let mut e=base();e["quick_pair"]["address"]=Value::String(SUSHI_PAIR.into());assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["quick_pair"]["reserve0_raw"]=Value::String("0".into());assert!(assess(&e.to_string()).is_err());
    }
    #[test] fn duplicate_or_unpaired_direction_rejected() {
        let mut e=base();e["quotes"][1]["route"]=Value::String("quick_then_sushi".into());assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["quotes"].as_array_mut().unwrap().pop();assert!(assess(&e.to_string()).is_err());
    }
    #[test] fn forged_fee_and_overflow_rejected() {
        let mut e=base();e["quotes"][0]["flash_fee_raw"]=Value::String("1".into());assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["quotes"][0]["principal_raw"]=Value::String("1e18".into());assert!(assess(&e.to_string()).is_err());
    }
    #[test] fn unsafe_flags_rejected() {
        let mut e=base();e["safety"]["uses_state_overrides"]=Value::Bool(true);assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["safety"]["proves_executor_eth_call"]=Value::Bool(true);assert!(assess(&e.to_string()).is_err());
    }
    #[test] fn missing_vault_liquidity_rejected() {
        let mut e=base();e["vault_dai_balance_raw"]=Value::String("0".into());assert!(assess(&e.to_string()).is_err());
        let mut e=base();e["vault_dai_balance_raw"]=Value::String("1000000000000000000".into());
        assert!(assess(&e.to_string()).unwrap().iter().all(|r|r.decision==Decision::RejectLiquidity));
    }
}
