# Step 11.2 V3 safety matrix

* 39 offline Python tests (previous 23 + 16 V3 tests) run during authoring.
* Ten Rust offline Step 11.2 unit tests added, for 71 total declared. **Rust not compiled locally**. Verify using VPS `cargo test` before trusting build.
* Non-V3 Solidity left untouched; its passing V2 tests do not prove V3 compatibility.
* Tests cover ABI selectors, strict four-word Uniswap QuoterV2 output, two-word Algebra quoter output, wrong fee tier, missing contract code, RPC allowlisting, fallback-block restart, invalid sizes/tiers, skipped-route consistency, duplicate quotes, changed pool identity, positive quotes never authorizing V3 execution, flash-fee calculation and fail-closed report validation.
* Live historical quoter behavior, archive support, pool existence, venue fee versions and actual net profitability are **not verified during packaging**; VPS RPC will test these.
