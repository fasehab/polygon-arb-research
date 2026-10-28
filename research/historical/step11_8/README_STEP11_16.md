# Step 11.16 — Low-Fee Depth and Activity Screening

Purpose: screen already confirmed historical Uniswap V3 100-fee pools (USDT/WETH and USDC.e/WETH) against their 500-fee counterparts at Polygon block 96179799. Selectors `slot0()` and `liquidity()` are used for both tiers; Swap logs are counted for 100-fee pools over 96179600–96179799. It checks the known block hash and chain ID.

```
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_16_low_fee_depth_patch.zip
python3 -m unittest discover -s scripts -p 'test_step11_16_low_fee_depth.py' -v
python3 scripts/step11_16_low_fee_depth.py --output outputs/step11_16_depth_activity.json
```

Limits: maximum 12 requests; 0 traces; 2 MB response cap; per-request 20-second timeout; 115-second overall budget. Does not inspect tick bitmap or reconstruct any intrablock state; estimated stablecoin input for a 1-bp upward stablecoin-per-WETH price movement is **local** and only applies absent tick crossings. Spot spreads between 100 and 500 fee pools are **end-of-block** comparisons, NOT synchronous intrablock arb opportunities. Trading is always disabled. No secrets saved.

Dependency: Python 3.9+ standard library; `POLYGON_RPC_URL` HTTPS, Polygon chain 137.

Token ordering caveat: USDT/WETH has WETH token0 and USDT token1, while USDC.e/WETH has USDC.e token0 and WETH token1. The probe explicitly uses the correct inverse price/depth formulas for the second pair, based on the canonical Uniswap token ordering established by the previously verified factory pools. It does not repeat token-identity calls in this 12-request pass. Comparing the two fee tiers at end-of-block does not indicate any executability.
