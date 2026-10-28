//! Read-only V2 cycle optimization and cost arithmetic.
//! All values are raw token units; all economic decisions use integers.
//! This numerical search is a bounded heuristic, not a proof of global optimality.

use crate::get_amount_out;
use ethers::types::{U256, U512};
use std::cmp::{Ordering, min};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct SignedDelta {
    pub(crate) positive: bool,
    pub(crate) magnitude: U256,
}
impl SignedDelta {
    pub(crate) fn between(received: U256, spent: U256) -> Self {
        Self {
            positive: received >= spent,
            magnitude: if received >= spent {
                received - spent
            } else {
                spent - received
            },
        }
    }
    pub(crate) fn subtract(self, cost: U256) -> Self {
        if self.positive {
            Self::between(self.magnitude, cost)
        } else {
            Self {
                positive: false,
                magnitude: self.magnitude.saturating_add(cost),
            }
        }
    }
    pub(crate) fn strictly_positive(self) -> bool {
        self.positive && !self.magnitude.is_zero()
    }
    pub(crate) fn sign(self) -> &'static str {
        if self.positive { "+" } else { "-" }
    }
}
impl Ord for SignedDelta {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self.positive, other.positive) {
            (true, false) => Ordering::Greater,
            (false, true) => Ordering::Less,
            (true, true) => self.magnitude.cmp(&other.magnitude),
            (false, false) => other.magnitude.cmp(&self.magnitude),
        }
    }
}
impl PartialOrd for SignedDelta {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

fn narrow_u512(value: U512) -> Option<U256> {
    let mut buf = [0u8; 64];
    value.to_big_endian(&mut buf);
    if buf[..32].iter().any(|&b| b != 0) {
        return None;
    }
    Some(U256::from_big_endian(&buf[32..]))
}

pub(crate) fn mul_div_ceil(a: U256, b: U256, denominator: U256) -> Option<U256> {
    if denominator.is_zero() {
        return None;
    }
    let product = U512::from(a) * U512::from(b);
    let d = U512::from(denominator);
    let quotient = product / d;
    let remainder = product % d;
    narrow_u512(if remainder.is_zero() {
        quotient
    } else {
        quotient.checked_add(U512::one())?
    })
}

// POL (18 decimal native wei) to token raw units using an unsigned, spot-price
// U256 ratio. Spot conversion intentionally excludes swap fees and slippage;
// it will generally OVERestimate token proceeds versus selling the POL.
pub(crate) fn gas_cost_at_spot_ceil(
    native_wei: U256,
    wrapped_reserve: U256,
    token_reserve: U256,
) -> Option<U256> {
    if wrapped_reserve.is_zero() || token_reserve.is_zero() {
        return None;
    }
    mul_div_ceil(native_wei, token_reserve, wrapped_reserve)
}

// Input is gas units multiplied by estimated gas price in wei, then a safety
// multiplier. This is a budget ESTIMATE, not receipt-calibrated actual gas.
pub(crate) fn gas_budget_wei(
    gas_units: u64,
    gas_price_wei: U256,
    multiplier_bps: u64,
) -> Option<U256> {
    let base = narrow_u512(U512::from(gas_units) * U512::from(gas_price_wei))?;
    mul_div_ceil(base, U256::from(multiplier_bps), U256::from(10_000u64))
}

pub(crate) fn flash_fee_ceil(principal: U256, bps: u64) -> Option<U256> {
    mul_div_ceil(principal, U256::from(bps), U256::from(10_000u64))
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct V2Route {
    pub(crate) first_in: U256,
    pub(crate) first_out: U256,
    pub(crate) second_in: U256,
    pub(crate) second_out: U256,
}
impl V2Route {
    pub(crate) fn limit(self) -> U256 {
        // Cap borrowed input at 10% of the smaller effective start-token
        // reserve: a conservative *search bound*, NOT a flash-loan limit.
        min(self.first_in, self.second_out) / U256::from(10u64)
    }
    pub(crate) fn quote(self, input: U256) -> Option<U256> {
        let mid = get_amount_out(input, self.first_in, self.first_out)?;
        if mid.is_zero() {
            return None;
        }
        get_amount_out(mid, self.second_in, self.second_out)
    }
    pub(crate) fn candidate(self, input: U256, loan_fee_bps: u64) -> Option<Candidate> {
        let received = self.quote(input)?;
        let fee = flash_fee_ceil(input, loan_fee_bps)?;
        let spent = input.checked_add(fee)?;
        Some(Candidate {
            input,
            received,
            fee,
            before_gas: SignedDelta::between(received, spent),
        })
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Candidate {
    pub(crate) input: U256,
    pub(crate) received: U256,
    pub(crate) fee: U256,
    pub(crate) before_gas: SignedDelta,
}
fn superior(new: Candidate, old: Option<Candidate>) -> Option<Candidate> {
    match old {
        None => Some(new),
        Some(existing) if new.before_gas > existing.before_gas => Some(new),
        Some(existing) if new.before_gas == existing.before_gas && new.input < existing.input => {
            Some(new)
        }
        _ => old,
    }
}

pub(crate) fn optimize(route: V2Route, loan_fee_bps: u64) -> Option<Candidate> {
    let maximum = route.limit();
    if maximum.is_zero() {
        return None;
    }
    let mut best = None;
    // Test integer bounds and logarithmic grid (rounded V2 quotes have steps).
    for x in [U256::one(), maximum] {
        if let Some(c) = route.candidate(x, loan_fee_bps) {
            best = superior(c, best);
        }
    }
    let mut x = U256::one();
    while x < maximum {
        if let Some(c) = route.candidate(x, loan_fee_bps) {
            best = superior(c, best);
        }
        x = min(x.saturating_mul(U256::from(2u8)), maximum);
    }
    // Continuous V2 profit is unimodal; integer rounding can disrupt this,
    // so refine via ternary search and explicitly scan the final neighborhood.
    let mut lo = U256::one();
    let mut hi = maximum;
    for _ in 0..100 {
        let span = hi - lo;
        if span <= U256::from(256u64) {
            break;
        }
        let m1 = lo + span / U256::from(3u8);
        let m2 = hi - span / U256::from(3u8);
        let a = route.candidate(m1, loan_fee_bps);
        let b = route.candidate(m2, loan_fee_bps);
        if let Some(c) = a {
            best = superior(c, best);
        }
        if let Some(c) = b {
            best = superior(c, best);
        }
        match (a, b) {
            (Some(left), Some(right)) if left.before_gas < right.before_gas => lo = m1,
            (Some(_), Some(_)) => hi = m2,
            (None, Some(_)) => lo = m1,
            _ => hi = m2,
        }
    }
    // Narrow interval is at most 256 units unless iteration budget hit.
    let mut at = lo;
    for _ in 0..257 {
        if at > hi {
            break;
        }
        if let Some(c) = route.candidate(at, loan_fee_bps) {
            best = superior(c, best);
        }
        if at == hi {
            break;
        }
        at += U256::one();
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spot_conversion_and_rounding() {
        assert_eq!(
            gas_cost_at_spot_ceil(3.into(), 2.into(), 5.into()),
            Some(8.into())
        );
        assert_eq!(gas_cost_at_spot_ceil(3.into(), 0.into(), 5.into()), None);
        assert_eq!(flash_fee_ceil(101.into(), 100), Some(2.into()));
        assert_eq!(flash_fee_ceil(100.into(), 0), Some(0.into()));
    }
    #[test]
    fn signed_cost_and_ordering() {
        assert_eq!(
            SignedDelta::between(100.into(), 105.into()).subtract(3.into()),
            SignedDelta {
                positive: false,
                magnitude: 8.into()
            }
        );
        assert_eq!(
            SignedDelta::between(109.into(), 105.into()).subtract(3.into()),
            SignedDelta {
                positive: true,
                magnitude: 1.into()
            }
        );
        assert!(
            SignedDelta::between(100.into(), 105.into())
                < SignedDelta::between(100.into(), 99.into())
        );
    }
    #[test]
    fn profitable_synthetic_route_can_be_optimized() {
        let r = V2Route {
            first_in: 1_000_000.into(),
            first_out: 2_000_000.into(),
            second_in: 1_000_000.into(),
            second_out: 1_000_000.into(),
        };
        let best = optimize(r, 0).unwrap();
        assert!(best.before_gas.strictly_positive());
        assert!(best.input <= r.limit());
        assert!(
            !best
                .before_gas
                .subtract(U256::max_value())
                .strictly_positive()
        );
    }
    #[test]
    fn identical_pools_never_profitable() {
        let r = V2Route {
            first_in: 1_000_000.into(),
            first_out: 1_000_000.into(),
            second_in: 1_000_000.into(),
            second_out: 1_000_000.into(),
        };
        assert!(!optimize(r, 0).unwrap().before_gas.strictly_positive());
    }
    #[test]
    fn gas_wei_budget_estimate() {
        // 600k * 40 gwei * 1.5 = 0.036 native tokens.
        assert_eq!(
            gas_budget_wei(600_000, 40_000_000_000u64.into(), 15_000),
            Some(U256::from(36_000_000_000_000_000u64))
        );
    }
    #[test]
    fn captured_live_snapshot_stays_unprofitable_for_all_routes() {
        let q_a = U256::from_dec_str("37817063318081442").unwrap();
        let q_b = U256::from_dec_str("10923782375561420463920448").unwrap();
        let s_a = U256::from_dec_str("534538600482").unwrap();
        let s_b = U256::from_dec_str("153713508343666003194").unwrap();
        for r in [
            V2Route {
                first_in: q_a,
                first_out: q_b,
                second_in: s_b,
                second_out: s_a,
            },
            V2Route {
                first_in: q_b,
                first_out: q_a,
                second_in: s_a,
                second_out: s_b,
            },
            V2Route {
                first_in: s_a,
                first_out: s_b,
                second_in: q_b,
                second_out: q_a,
            },
            V2Route {
                first_in: s_b,
                first_out: s_a,
                second_in: q_a,
                second_out: q_b,
            },
        ] {
            let best = optimize(r, 0).unwrap();
            assert!(!best.before_gas.strictly_positive());
        }
    }
}
