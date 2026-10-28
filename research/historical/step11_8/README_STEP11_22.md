# Step 11.22 — WBTC alternate-pair pool and activity triage

Run from your existing `/root/polygon_step11_8/step11_8_polygon_tx_boundary_probe` directory after extracting the zip.

```
sha256sum -c SHA256SUMS_STEP11_22
python3 -m unittest discover -s scripts -p 'test_step11_22_wbtc_triage.py' -v
python3 scripts/step11_22_wbtc_triage.py
```

The script uses your existing `POLYGON_RPC_URL` environment variable, not a key in source files. It checks Polygon chain 137 and the known block hash of block 96179799.

Tests QuickSwap Algebra and Uniswap V3 fee tiers 100 / 500 for **USDT/WBTC** and **USDC.e/WBTC**. Detects pool existence, exact token pair and ordering, fee tier, active liquidity, end-of-block sqrt price, and observed Swap logs in blocks 96179600–96179799. Performs an end-of-block (not intrablock) spot spread screen as a *triage heuristic*.

RPC calls at most 42, 140s deadline, 2 MB max per response, no tracing or transaction submission. Reports `outputs/step11_22_wbtc_triage.json`. Any missing/malformed evidence fails closed, and errors produce no success verdict. Raw active liquidity does not establish executable USD depth. No trade recommendation or proven arbitrage follows from positive spread.

**Decision after this run:** Prioritize only pairs with valid pools, observed swap activity and adequate on-chain local depth for subsequent $100-equivalent tick-aware quotation testing. A 100-fee pool might be extremely thin; do not infer depth from its raw liquidity integer.
