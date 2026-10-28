//! Step 7 read-only, fail-closed preflight *policy* and V2 quote guardrails.
//!
//! This is NOT EVM execution simulation, a guarantee of pending-tx ordering,
//! a real flash-loan liquidity check, or permission to trade. No signer.
use crate::get_amount_out;
use crate::optimizer::{SignedDelta, V2Route, flash_fee_ceil, mul_div_ceil};
use ethers::types::U256;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Scenario {
    ConfirmedSnapshot,
    HypotheticalPostPending,
}
impl Scenario {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::ConfirmedSnapshot => {
                "CONFIRMED_SNAPSHOT (does not guarantee future inclusion state)"
            }
            Self::HypotheticalPostPending => "HYPOTHETICAL_PENDING (ordering NOT verified)",
        }
    }
}

/// DRAFT ABI fields for the Step 5 Solidity TradeParams tuple.
/// Never pass them to a signer automatically. They're quote-based limits,
/// NOT evidence that a contract call would succeed.
#[derive(Clone, Copy, Debug)]
pub(crate) struct DraftLimits {
    pub(crate) principal: U256,
    pub(crate) expected_first: U256,
    pub(crate) expected_second: U256,
    pub(crate) min_out_a: U256,
    pub(crate) min_out_b: U256,
    pub(crate) maximum_flash_fee: U256,
    pub(crate) minimum_profit: U256,
}

pub(crate) fn draft_limits(
    route: V2Route,
    principal: U256,
    fee_cap_bps: u64,
    max_slippage_bps: u64,
    minimum_profit_bps: u64,
) -> Option<DraftLimits> {
    // The user must not be able to enter unrealistic fee/slippage percentages.
    if principal.is_zero()
        || fee_cap_bps > 10_000
        || max_slippage_bps > 5_000
        || minimum_profit_bps == 0
        || minimum_profit_bps > 10_000
    {
        return None;
    }
    let first = get_amount_out(principal, route.first_in, route.first_out)?;
    if first.is_zero() {
        return None;
    }
    let second = get_amount_out(first, route.second_in, route.second_out)?;
    if second.is_zero() {
        return None;
    }
    let retained_bps = U256::from(10_000u64 - max_slippage_bps);
    let min_out_a = first
        .checked_mul(retained_bps)?
        .checked_div(U256::from(10_000u64))?;
    let min_out_b = second
        .checked_mul(retained_bps)?
        .checked_div(U256::from(10_000u64))?;
    if min_out_a.is_zero() || min_out_b.is_zero() {
        return None;
    }
    Some(DraftLimits {
        principal,
        expected_first: first,
        expected_second: second,
        min_out_a,
        min_out_b,
        maximum_flash_fee: flash_fee_ceil(principal, fee_cap_bps)?,
        minimum_profit: mul_div_ceil(
            principal,
            U256::from(minimum_profit_bps),
            U256::from(10_000u64),
        )
        .filter(|v| !v.is_zero())?,
    })
}

#[derive(Clone, Copy)]
pub(crate) struct Evidence {
    pub(crate) scenario: Scenario,
    pub(crate) pre_gas: SignedDelta,
    pub(crate) flash_fee_verified: bool,
    pub(crate) gas_token_cost_verified: bool,
    pub(crate) actual_loan_liquidity_verified: bool,
    pub(crate) allowed_tokens_verified: bool,
    pub(crate) same_state_full_eth_call_passed: bool,
    pub(crate) execution_order_verified: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Verdict {
    Reject,
    Unassessed,
    ModelOnly,
}
impl Verdict {
    pub(crate) fn label(self) -> &'static str {
        match self {
            Self::Reject => "REJECT",
            Self::Unassessed => "UNASSESSED",
            Self::ModelOnly => "MODEL_ONLY_NO_EXECUTION_AUTHORIZATION",
        }
    }
}

/// This is a diagnostic classifier, NEVER an execution permission gate.
/// Even if all inputs were true, deployment/review is outside its scope.
pub(crate) fn assess(e: Evidence) -> (Verdict, Vec<&'static str>) {
    if !e.pre_gas.strictly_positive() {
        return (Verdict::Reject, vec!["non-positive modeled pre-gas profit"]);
    }
    let mut missing = Vec::new();
    if !e.flash_fee_verified {
        missing.push("flash-loan fee is not independently verified");
    }
    if !e.gas_token_cost_verified {
        missing.push("gas conversion/cost is not execution-verified");
    }
    if !e.actual_loan_liquidity_verified {
        missing.push("actual Vault borrowing capacity unverified");
    }
    if !e.allowed_tokens_verified {
        missing.push("deployed contract token authorization unverified");
    }
    if !e.same_state_full_eth_call_passed {
        missing.push("full Step 5 contract eth_call has not passed");
    }
    if !e.execution_order_verified {
        missing.push("relative inclusion/ordering not guaranteed");
    }
    if e.scenario == Scenario::HypotheticalPostPending {
        missing.push("pending swap state is hypothetical, not confirmed");
    }
    if missing.is_empty() {
        (
            Verdict::ModelOnly,
            vec!["model tests passed; an independent deployment gate is still required"],
        )
    } else {
        (Verdict::Unassessed, missing)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn r() -> V2Route {
        V2Route {
            first_in: 1_000_000u64.into(),
            first_out: 2_000_000u64.into(),
            second_in: 1_000_000u64.into(),
            second_out: 1_000_000u64.into(),
        }
    }
    fn evidence(scenario: Scenario) -> Evidence {
        Evidence {
            scenario,
            pre_gas: SignedDelta::between(110.into(), 100.into()),
            flash_fee_verified: true,
            gas_token_cost_verified: true,
            actual_loan_liquidity_verified: true,
            allowed_tokens_verified: true,
            same_state_full_eth_call_passed: true,
            execution_order_verified: true,
        }
    }
    #[test]
    fn rejects_nonpositive_before_any_rpc_gate() {
        let mut e = evidence(Scenario::ConfirmedSnapshot);
        e.pre_gas = SignedDelta::between(99.into(), 100.into());
        assert_eq!(assess(e).0, Verdict::Reject);
    }
    #[test]
    fn does_not_certify_hypothetical_order_even_with_other_evidence() {
        let (v, reasons) = assess(evidence(Scenario::HypotheticalPostPending));
        assert_eq!(v, Verdict::Unassessed);
        assert!(reasons.iter().any(|x| x.contains("hypothetical")));
    }
    #[test]
    fn missing_contract_simulation_fails_closed() {
        let mut e = evidence(Scenario::ConfirmedSnapshot);
        e.same_state_full_eth_call_passed = false;
        let (v, reasons) = assess(e);
        assert_eq!(v, Verdict::Unassessed);
        assert!(reasons.iter().any(|x| x.contains("eth_call")));
    }
    #[test]
    fn all_passing_model_evidence_never_authorizes_execution() {
        assert_eq!(
            assess(evidence(Scenario::ConfirmedSnapshot)).0,
            Verdict::ModelOnly
        );
    }
    #[test]
    fn draft_limits_are_conservative_and_raw() {
        let d = draft_limits(r(), 1000u64.into(), 10, 100, 20).unwrap();
        assert!(d.min_out_a <= d.expected_first && d.min_out_b <= d.expected_second);
        assert!(d.maximum_flash_fee >= U256::one());
        assert!(d.minimum_profit >= U256::one());
        assert_eq!(d.principal, U256::from(1000u64));
    }
    #[test]
    fn zero_or_invalid_inputs_rejected() {
        assert!(draft_limits(r(), U256::zero(), 0, 100, 20).is_none());
        assert!(draft_limits(r(), U256::one(), 0, 0, 0).is_none());
        assert!(draft_limits(r(), 1000u64.into(), 10_001, 0, 20).is_none());
        assert!(draft_limits(r(), 1000u64.into(), 0, 5_001, 20).is_none());
    }
}
