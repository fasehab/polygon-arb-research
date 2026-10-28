# Step 11.18 — Depth-First Historical Uniswap V3 Screening

**Purpose:** Stop pursuing the $0.000047-USDT positive quote in a thin 0.01%-fee pool; identify **larger historical Uniswap V3 pools** that may support $100–$1,000 stablecoin trades with modest slippage.

## Scope
- Polygon mainnet chain 137, historical end of block **96179799** only (expected hash `0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4`).
- Pairs: USDT/WETH, USDC.e/WETH.
- Uniswap V3 tiers: 500 (0.05%), 3000 (0.30%). The 100-tier was previously shown to have insignificant economically tradeable depth.
- Verifies addresses against Uniswap V3 factory, token ordering, fee and active liquidity; reads 200-block Swap event count.
- QuoterV2 `quoteExactInputSingle` at 10, 100, 1000 stable units for **stablecoin -> WETH only**.
- Computes each quote's deterioration versus the **fee-adjusted starting spot price**, properly handling reversed token order.
- No full-block traces, signature operations, wallet keys, gas cost conclusions or broadcast.
- Budget: **44 RPC requests** maximum, **180 seconds** monotonic global deadline, **200 KB** response cap.

## Install

Extract this ZIP into the existing project directory:

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_18_depth_first_patch.zip
sha256sum -c SHA256SUMS_STEP11_18
python3 -m unittest discover -s scripts -p 'test_step11_18_depth_first.py' -v
```

**Never paste your RPC API key into chat.** The current shell must have `POLYGON_RPC_URL` set to your own Polygon HTTPS read RPC.

Run:

```bash
python3 scripts/step11_18_depth_first.py
```

Evidence: `outputs/step11_18_depth_first.json`.

## Interpretation

`deterioration_100_bps` and `deterioration_1000_bps` are quote losses versus the fee-adjusted initial spot (not net arbitrage P&L). QuoterV2 includes pool swap pricing/fees and crosses initialized ticks, but the reported `quoter_gas_estimate` is **not** a complete flash-loan transaction cost.

`PROMISING_DEPTH_ONLY` means the historical quote deteriorated <=10 bps at 100 stable units and <=30 bps at 1000 stable units. These are **arbitrary screening thresholds**, not trading signals. A negative or unavailable quote remains unproven. Cross-venue spread, simultaneous intrablock states, slippage for a two-leg atomic route, MEV inclusion, gas and flash-loan costs remain untested.

The historical block was already used for earlier QuoterV2 checks. This is a new **depth selection** screen, not repetition of the losing 100/500 route.
