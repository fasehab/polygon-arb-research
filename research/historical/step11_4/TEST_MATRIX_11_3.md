# Step 11.3 test matrix

- 54 Python unittest cases passed locally, including 15 new event/mixed-route tests and 39 prior tests.
- 10 new Rust unit tests declared; 81 total expected on VPS (not compiled locally).
- Event logs are treated only as pool activity. Duplicated, removed, out-of-range, wrong-address and missing-identity logs fail closed.
- Fallback retries the full scan (never interleaves two providers' RPC evidence).
- Independent Rust offline validation requires complete venue/route matrices, pool token-order identity, fee recomputation and non-positive/positive quote classification.
- Gross-positive quotes cannot authorize trading; the included Solidity executor remains V2-only.
- No RPC broadcast, signer, wallet, transaction or relay integration.
