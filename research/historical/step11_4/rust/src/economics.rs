//! Step 9: evidence-labeled, conservative, NON-AUTHORIZING gas/flash economics.
//! Values are *raw borrowed-token units* and *wei*. No floating point.
//! A receipt from a local FORK is not a real transaction receipt; a live gas
//! quote is not historical inclusion evidence. This module NEVER authorizes
//! Mainnet execution, even when all mathematical thresholds are satisfied.
use ethers::types::{Address, U256, U512};
use serde_json::Value;
use std::str::FromStr;

const SCALE: u64 = 10_000;
const WAD: u64 = 1_000_000_000_000_000_000;
const POLYGON_DAI: &str = "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Classification { Rejected, Unassessed, SyntheticModelOnly, HistoricalModelOnly }
impl Classification {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Rejected => "REJECTED_ESTIMATED_LOSS_OR_BELOW_FLOOR",
            Self::Unassessed => "UNASSESSED_MISSING_OR_INCOMPATIBLE_EVIDENCE",
            Self::SyntheticModelOnly => "SYNTHETIC_FORK_MODEL_ONLY_NO_EXECUTION_AUTHORIZATION",
            Self::HistoricalModelOnly => "HISTORICAL_MODEL_ONLY_NO_EXECUTION_AUTHORIZATION",
        }
    }
}
#[derive(Debug, Clone, Copy)]
struct Reference { native_reserve: U256, borrowed_reserve: U256 }

#[derive(Debug, Clone)]
pub(crate) struct EconomicEvidence {
    scenario: String,
    fork_block: u64,
    gas_quote_block: u64,
    principal: U256,
    received: U256,
    fee_rate_wad: Option<U256>,
    reported_flash_fee: U256,
    local_gas_used: u64,
    current_gas_price_wei: Option<U256>,
    gas_units_multiplier_bps: u64,
    gas_price_multiplier_bps: u64,
    minimum_net_bps: u64,
    quick_reference: Option<Reference>,
    sushi_reference: Option<Reference>,
    flash_fee_rpc_verified: bool,
    price_rpc_verified: bool,
    conversion_rpc_verified: bool,
    dai: Address,
}
#[derive(Debug, Clone)]
pub(crate) struct Assessment {
    pub(crate) classification: Classification,
    pub(crate) reasons: Vec<&'static str>,
    pub(crate) principal: U256,
    pub(crate) final_balance: U256,
    pub(crate) flash_fee: Option<U256>,
    pub(crate) native_gas_wei_budget: Option<U256>,
    pub(crate) borrowed_token_gas_budget: Option<U256>,
    pub(crate) positive_net_token: Option<U256>,
    pub(crate) minimum_profit_floor: Option<U256>,
}

// Wide multiplication with upward rounding. Any overflow -> UNKNOWN.
fn mul_div_up(a: U256, b: U256, denominator: U256) -> Option<U256> {
    if denominator.is_zero() { return None; }
    let prod = U512::from(a) * U512::from(b);
    let den = U512::from(denominator);
    let mut quotient = prod / den;
    if !(prod % den).is_zero() { quotient = quotient.checked_add(U512::one())?; }
    let mut buffer = [0u8; 64];
    quotient.to_big_endian(&mut buffer);
    if buffer[..32].iter().any(|x| *x != 0) { return None; }
    Some(U256::from_big_endian(&buffer[32..]))
}
fn as_uint_string(v: &Value, name: &str) -> Result<U256, String> {
    let strval = v.get(name).and_then(Value::as_str)
        .ok_or_else(|| format!("{name}: expected decimal STRING (no floating point)"))?;
    if strval.is_empty() || !strval.bytes().all(|x| x.is_ascii_digit()) {
        return Err(format!("{name}: invalid nonnegative decimal string"));
    }
    U256::from_dec_str(strval).map_err(|_| format!("{name}: value exceeds uint256"))
}
fn as_optional_uint(v: &Value, name: &str) -> Result<Option<U256>, String> {
    if v.get(name).map_or(true, Value::is_null) { Ok(None) }
    else { as_uint_string(v, name).map(Some) }
}
fn as_u64(v: &Value, key: &str) -> Result<u64, String> {
    v.get(key).and_then(Value::as_u64).ok_or_else(|| format!("{key}: expected nonnegative integer"))
}
fn as_bool(v: &Value, key: &str) -> Result<bool, String> {
    v.get(key).and_then(Value::as_bool).ok_or_else(|| format!("{key}: expected boolean"))
}
fn parse_reference(v: &Value, key: &str) -> Result<Option<Reference>, String> {
    match v.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(obj) if obj.is_object() => Ok(Some(Reference {
            native_reserve: as_uint_string(obj, "native_reserve")?,
            borrowed_reserve: as_uint_string(obj, "borrowed_reserve")?,
        })),
        _ => Err(format!("{key}: must be object or null")),
    }
}
impl EconomicEvidence {
    pub(crate) fn from_json(s: &str) -> Result<Self, String> {
        let v: Value = serde_json::from_str(s).map_err(|e| format!("invalid JSON: {e}"))?;
        let scenario = v.get("scenario").and_then(Value::as_str)
            .ok_or("scenario missing")?.to_string();
        if scenario != "synthetic_fork" && scenario != "historical_untouched" {
            return Err("scenario must be synthetic_fork or historical_untouched".into());
        }
        let dai = Address::from_str(v.get("token_in").and_then(Value::as_str)
            .ok_or("token_in must be a hex string")?).map_err(|_| "invalid token_in address")?;
        let p = Self {
            scenario,
            fork_block: as_u64(&v, "fork_block")?,
            gas_quote_block: as_u64(&v, "gas_quote_block")?,
            principal: as_uint_string(&v, "principal_raw")?,
            received: as_uint_string(&v, "received_raw")?,
            fee_rate_wad: as_optional_uint(&v, "balancer_fee_rate_wad")?,
            reported_flash_fee: as_uint_string(&v, "flash_fee_reported_raw")?,
            local_gas_used: as_u64(&v, "local_fork_gas_used")?,
            current_gas_price_wei: as_optional_uint(&v, "rpc_current_gas_price_wei")?,
            gas_units_multiplier_bps: as_u64(&v, "gas_units_multiplier_bps")?,
            gas_price_multiplier_bps: as_u64(&v, "gas_price_multiplier_bps")?,
            minimum_net_bps: as_u64(&v, "minimum_net_profit_bps")?,
            quick_reference: parse_reference(&v, "quick_wpol_reference")?,
            sushi_reference: parse_reference(&v, "sushi_wpol_reference")?,
            flash_fee_rpc_verified: as_bool(&v, "flash_fee_historical_rpc_read")?,
            price_rpc_verified: as_bool(&v, "gas_price_live_rpc_read")?,
            conversion_rpc_verified: as_bool(&v, "conversion_historical_rpc_read")?,
            dai,
        };
        Ok(p)
    }
}
fn fail_missing(e: &EconomicEvidence, why: &'static str) -> Assessment {
    Assessment { classification: Classification::Unassessed, reasons: vec![why],
        principal: e.principal, final_balance: e.received,
        flash_fee: None, native_gas_wei_budget: None, borrowed_token_gas_budget: None,
        positive_net_token: None, minimum_profit_floor: None }
}

pub(crate) fn evaluate(e: &EconomicEvidence) -> Assessment {
    if e.dai != Address::from_str(POLYGON_DAI).expect("constant token") {
        return fail_missing(e, "this evidence format is only calibrated for Polygon DAI as the borrowed token");
    }
    if e.fork_block == 0 || e.gas_quote_block == 0 || e.local_gas_used == 0
        || e.local_gas_used > 5_000_000 || e.principal.is_zero() || e.received.is_zero() {
        return fail_missing(e, "missing/invalid fork block, principal, actual output or local gas measurement");
    }
    if !(10_000..=50_000).contains(&e.gas_units_multiplier_bps)
        || !(10_000..=50_000).contains(&e.gas_price_multiplier_bps)
        || !(1..=10_000).contains(&e.minimum_net_bps) {
        return fail_missing(e, "gas budget multipliers or positive profit floor out of range");
    }
    if !e.flash_fee_rpc_verified || !e.price_rpc_verified || !e.conversion_rpc_verified {
        return fail_missing(e, "fee, price or conversion source missing successful RPC verification");
    }
    let Some(rate) = e.fee_rate_wad else {
        return fail_missing(e, "historical Balancer flash fee rate missing");
    };
    if rate > U256::from(WAD) {
        return fail_missing(e, "Balancer fee exceeds 100 percent");
    }
    let Some(expected_fee) = mul_div_up(e.principal, rate, U256::from(WAD)) else {
        return fail_missing(e, "flash fee cannot be calculated safely");
    };
    if expected_fee != e.reported_flash_fee {
        return fail_missing(e, "reported fork flash fee differs from historical Vault rate");
    }
    let Some(price) = e.current_gas_price_wei.filter(|p| !p.is_zero()) else {
        return fail_missing(e, "live gas price missing or zero");
    };
    let Some(quick) = e.quick_reference else { return fail_missing(e, "missing QuickSwap WPOL/DAI reference pool"); };
    let Some(sushi) = e.sushi_reference else { return fail_missing(e, "missing SushiSwap WPOL/DAI reference pool"); };
    for pool in [quick, sushi] {
        if pool.native_reserve.is_zero() || pool.borrowed_reserve.is_zero() {
            return fail_missing(e, "zero WPOL or borrowed-token reference reserve");
        }
    }
    let Some(gas_units_budget) = mul_div_up(U256::from(e.local_gas_used), U256::from(e.gas_units_multiplier_bps), U256::from(SCALE)) else {
        return fail_missing(e, "overflow in gas unit safety buffer");
    };
    let Some(gas_price_budget) = mul_div_up(price, U256::from(e.gas_price_multiplier_bps), U256::from(SCALE)) else {
        return fail_missing(e, "overflow in gas price safety buffer");
    };
    let Some(native_budget) = gas_units_budget.checked_mul(gas_price_budget) else {
        return fail_missing(e, "overflow in native gas-cost calculation");
    };
    // Reject gas conversions that use >1% of ANY of the two native-token pools.
    if native_budget >= quick.native_reserve / U256::from(100)
        || native_budget >= sushi.native_reserve / U256::from(100) {
        return fail_missing(e, "native gas budget too large relative to conversion pool liquidity");
    }
    let Some(q_token) = mul_div_up(native_budget, quick.borrowed_reserve, quick.native_reserve) else {
        return fail_missing(e, "overflow in QuickSwap native-to-DAI quote");
    };
    let Some(s_token) = mul_div_up(native_budget, sushi.borrowed_reserve, sushi.native_reserve) else {
        return fail_missing(e, "overflow in SushiSwap native-to-DAI quote");
    };
    if q_token.is_zero() || s_token.is_zero() {
        return fail_missing(e, "gas token conversion rounded to zero");
    }
    let high = q_token.max(s_token);
    let low = q_token.min(s_token);
    let Some(high_scaled) = high.checked_mul(U256::from(10_000)) else {
        return fail_missing(e, "overflow in cross-DEX conversion divergence test");
    };
    let Some(low_scaled) = low.checked_mul(U256::from(11_000)) else {
        return fail_missing(e, "overflow in cross-DEX conversion divergence test");
    };
    if high_scaled > low_scaled {
        return fail_missing(e, "QuickSwap/SushiSwap gas conversion differs by over 10 percent");
    }
    let Some(floor) = mul_div_up(e.principal, U256::from(e.minimum_net_bps), U256::from(SCALE)) else {
        return fail_missing(e, "overflow in minimum profit floor");
    };
    let Some(total) = e.principal.checked_add(expected_fee).and_then(|v| v.checked_add(high)) else {
        return fail_missing(e, "overflow adding principal, fee and gas");
    };
    let net = e.received.checked_sub(total);
    let good = net.is_some_and(|x| x >= floor);
    Assessment {
        classification: if !good { Classification::Rejected }
            else if e.scenario == "synthetic_fork" { Classification::SyntheticModelOnly }
            else { Classification::HistoricalModelOnly },
        reasons: if good { vec!["gas price belongs to a different (live) block than fork state",
            "local fork gas is not a real Mainnet receipt or inclusion guarantee",
            "token conversion is spot-reserve based, not a hardened execution oracle",
            "economic pass is NOT execution authorization"] }
            else { vec!["after fee, buffered gas and minimum profit, the route is not modeled as profitable"] },
        principal: e.principal,
        final_balance: e.received,
        flash_fee: Some(expected_fee),
        native_gas_wei_budget: Some(native_budget),
        borrowed_token_gas_budget: Some(high),
        positive_net_token: net,
        minimum_profit_floor: Some(floor),
    }
}

pub(crate) fn print_report(json: &str) -> Result<(), String> {
    let e = EconomicEvidence::from_json(json)?;
    let report = evaluate(&e);
    println!("STEP 9 EVIDENCE-BASED ECONOMIC MODEL — READ-ONLY");
    println!("scenario={} fork_block={} gas_quote_live_block={} borrowed_token={:?}", e.scenario, e.fork_block, e.gas_quote_block, e.dai);
    println!("principal_raw={} returned_raw={} (raw Polygon DAI units)", report.principal, report.final_balance);
    println!("historical_balancer_fee_raw={:?}", report.flash_fee);
    println!("native_gas_budget_wei={:?} gas_in_dai_raw={:?}", report.native_gas_wei_budget, report.borrowed_token_gas_budget);
    println!("modeled_positive_net_dai_raw={:?} minimum_profit_floor_raw={:?}", report.positive_net_token, report.minimum_profit_floor);
    println!("VERDICT: {}", report.classification.label());
    for reason in report.reasons { println!("  NOTE: {reason}"); }
    println!("ALWAYS BLOCK: no signing, no broadcasting, no live execution authorization.");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn valid() -> EconomicEvidence {
        EconomicEvidence { scenario: "synthetic_fork".into(), fork_block: 96179857,
            gas_quote_block: 100_000_000, principal: U256::from_dec_str("5000000000000000000").unwrap(),
            received: U256::from_dec_str("7451954393802018299").unwrap(), fee_rate_wad: Some(U256::zero()),
            reported_flash_fee: U256::zero(), local_gas_used: 428_064,
            current_gas_price_wei: Some(U256::from(500_000_000u64)),
            gas_units_multiplier_bps: 15_000, gas_price_multiplier_bps: 20_000,
            minimum_net_bps: 20,
            quick_reference: Some(Reference { native_reserve: U256::from_dec_str("100000000000000000000").unwrap(), borrowed_reserve: U256::from_dec_str("300000000000000000000000").unwrap() }),
            sushi_reference: Some(Reference { native_reserve: U256::from_dec_str("100000000000000000000").unwrap(), borrowed_reserve: U256::from_dec_str("305000000000000000000000").unwrap() }),
            flash_fee_rpc_verified: true, price_rpc_verified: true, conversion_rpc_verified: true,
            dai: Address::from_str(POLYGON_DAI).unwrap() }
    }
    #[test]
    fn upward_rounding_is_conservative() {
        assert_eq!(mul_div_up(101.into(), 1.into(), 100.into()), Some(2.into()));
        assert_eq!(mul_div_up(100.into(), 1.into(), 100.into()), Some(1.into()));
        assert_eq!(mul_div_up(1.into(), 0.into(), 10.into()), Some(U256::zero()));
        assert_eq!(mul_div_up(1.into(), 1.into(), 0.into()), None);
    }
    #[test]
    fn synthetic_profit_never_authorizes_trade() {
        let a = evaluate(&valid());
        assert_eq!(a.classification, Classification::SyntheticModelOnly);
        assert!(a.positive_net_token.unwrap() > a.minimum_profit_floor.unwrap());
        assert_eq!(a.borrowed_token_gas_budget.unwrap(), U256::from_dec_str("1958392800000000000").unwrap());
    }
    #[test]
    fn historical_profit_is_still_model_only() {
        let mut e = valid(); e.scenario = "historical_untouched".into();
        assert_eq!(evaluate(&e).classification, Classification::HistoricalModelOnly);
    }
    #[test]
    fn missing_fee_information_fails_closed() {
        let mut e = valid(); e.fee_rate_wad = None;
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
        let mut e = valid(); e.flash_fee_rpc_verified = false;
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
    }
    #[test]
    fn mismatched_flash_fee_fails_closed() {
        let mut e = valid(); e.reported_flash_fee = U256::one();
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
        let mut e = valid(); e.fee_rate_wad = Some(U256::from_dec_str("10000000000000000").unwrap());
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
    }
    #[test]
    fn missing_or_divergent_gas_conversion_fails_closed() {
        let mut e = valid(); e.quick_reference = None;
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
        let mut e = valid(); e.sushi_reference.as_mut().unwrap().borrowed_reserve = U256::from_dec_str("400000000000000000000000").unwrap();
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
    }
    #[test]
    fn unprofitable_trade_rejected_without_overflow() {
        let mut e = valid(); e.received = U256::from_dec_str("5000000000000000001").unwrap();
        assert_eq!(evaluate(&e).classification, Classification::Rejected);
    }
    #[test]
    fn non_dai_pair_unassessed() {
        let mut e = valid(); e.dai = Address::from_low_u64_be(5);
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
    }
    #[test]
    fn json_rejects_floating_point_and_unknown_scenario() {
        let j = r#"{"scenario":"synthetic_fork","principal_raw":1}"#;
        assert!(EconomicEvidence::from_json(j).is_err());
        let j = r#"{"scenario":"made_up"}"#;
        assert!(EconomicEvidence::from_json(j).is_err());
    }
    #[test]
    fn gas_price_zeros_fail_closed() {
        let mut e = valid(); e.current_gas_price_wei = Some(U256::zero());
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
        let mut e = valid(); e.local_gas_used = 0;
        assert_eq!(evaluate(&e).classification, Classification::Unassessed);
    }
}
