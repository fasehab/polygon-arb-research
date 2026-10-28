//! STEP 8.2: Conservative and explicitly non-authorizing token compatibility gate.
//!
//! An ERC-20 balance/metadata check does not prove the absence of transfer taxes,
//! rebases, blacklists, dynamic fee updates, callbacks or admin changes.
//! The vetted-candidate shortlist is intentionally narrow and is NOT proof of safety.
//! Unknown assets and the historic fee-on-transfer token are blocked from *all*
//! opportunity pricing and hypothetical execution analysis, even in read-only mode.

use ethers::types::Address;
use std::str::FromStr;

const DAI: &str = "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063";
const WETH: &str = "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619";
const BRIDGED_USDC: &str = "0x2791bca1f2de4661ed88a30c99a7a9449aa84174";
const OBSERVED_TAXED: &str = "0xeb51d9a39ad5eef215dc0bf39a8821ff804a0f01";

#[derive(Debug, PartialEq, Eq)]
pub enum AssetPolicy {
    TestCandidate,
    ObservedFeeOnTransfer,
    Unreviewed,
}

fn pinned(hex: &str) -> Address {
    Address::from_str(hex).expect("pinned Polygon token address is invalid")
}

pub fn classify(token: Address) -> AssetPolicy {
    if token == pinned(OBSERVED_TAXED) {
        AssetPolicy::ObservedFeeOnTransfer
    } else if token == pinned(DAI) || token == pinned(WETH) || token == pinned(BRIDGED_USDC) {
        AssetPolicy::TestCandidate
    } else {
        AssetPolicy::Unreviewed
    }
}

/// Returns Ok only for two distinct shortlisted addresses. Ok means
/// *eligible for observation-only modeling*, not safe for a broadcast.
pub fn compatible_for_modeling(a: Address, b: Address) -> Result<(), &'static str> {
    if a == Address::zero() || b == Address::zero() || a == b {
        return Err("invalid or duplicate token addresses");
    }
    if classify(a) == AssetPolicy::ObservedFeeOnTransfer
        || classify(b) == AssetPolicy::ObservedFeeOnTransfer {
        return Err("observed 5% fee-on-transfer asset: V2 router reserve model is invalid");
    }
    if classify(a) != AssetPolicy::TestCandidate || classify(b) != AssetPolicy::TestCandidate {
        return Err("token not in conservative Step 8.2 shortlist; requires independent fork review");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_historical_taxed_token_in_any_direction() {
        assert_eq!(classify(pinned(OBSERVED_TAXED)), AssetPolicy::ObservedFeeOnTransfer);
        assert!(compatible_for_modeling(pinned(OBSERVED_TAXED), pinned(DAI)).is_err());
        assert!(compatible_for_modeling(pinned(WETH), pinned(OBSERVED_TAXED)).is_err());
    }

    #[test]
    fn rejects_unknown_tokens_even_with_normal_decimals() {
        let unknown = Address::from_low_u64_be(12345);
        assert_eq!(classify(unknown), AssetPolicy::Unreviewed);
        assert!(compatible_for_modeling(pinned(DAI), unknown).is_err());
    }

    #[test]
    fn accepts_only_shortlisted_distinct_test_candidates() {
        assert!(compatible_for_modeling(pinned(WETH), pinned(DAI)).is_ok());
        assert!(compatible_for_modeling(pinned(DAI), pinned(BRIDGED_USDC)).is_ok());
        assert!(compatible_for_modeling(pinned(WETH), pinned(BRIDGED_USDC)).is_ok());
    }

    #[test]
    fn rejects_zero_and_self_pairs() {
        assert!(compatible_for_modeling(Address::zero(), pinned(DAI)).is_err());
        assert!(compatible_for_modeling(pinned(DAI), pinned(DAI)).is_err());
    }
}
