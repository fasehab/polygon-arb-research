# STEP 11.23 — USDC.e/WBTC Fee-Aware Intrablock Event Screening

**Read-only, offline and historical. Does not sign, broadcast or execute transactions.**

This additive patch adapts the tested Step 11.21B event-reconstruction methodology to the historical Polygon USDC.e/WBTC Algebra V3 ↔ Uniswap V3 500 pools verified in Step 11.22. It does **not** change the USDC.e/WETH scanner or existing Rust/Solidity code.

## Verified historical pool identifiers (case-insensitive)

| Venue | Address | Step 11.22 Swap logs in 200-block window |
| --- | --- | ---: |
| QuickSwap Algebra V3 | `0xa5cd8351cbf30b531c7b11b0d9d3ff38ea2e280f` | 10 |
| Uniswap V3 500 | `0xeef1a9507b3d505f0062f2be9453981255b503c8` | 20 |

Token0 is Polygon WBTC `0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6` (8 decimals); token1 is Polygon bridged USDC.e `0x2791bca1f2de4661ed88a30c99a7a9449aa84174` (6 decimals). The event-price heuristic uses raw sqrtPriceX96 ratios, so decimals cancel when comparing the *same* pair and same token order. Do **not** interpret it as a stablecoin-denominated quote.

## Prerequisite installation

Run from the existing Step 11.8 project directory:

```
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
```

The prior Step 11.9 and Step 11.13 scripts must still be installed in `scripts/` because the new scanner imports them. Existing Step 11.21B is optional. Set `POLYGON_RPC_URL` to your own Polygon HTTPS RPC URL; never paste the URL or key into chat.

## Installation

Upload `step11_23_usdce_wbtc_event_patch.zip` to `/root` and then:

```
unzip -o /root/step11_23_usdce_wbtc_event_patch.zip
sha256sum -c SHA256SUMS_STEP11_23
python3 -m unittest discover -s scripts -p 'test_step11_23_usdce_wbtc_event_spread.py' -v
```

**Expected from local fixture testing: 30 tests passed.** These tests do not establish live RPC capability or profits.

## One bounded historical run

```
python3 scripts/step11_23_usdce_wbtc_event_spread.py \
  --from-block 96179600 \
  --to-block 96179799 \
  --min-spread-bps 9 \
  --output outputs/step11_23_usdce_wbtc_event_spread.json

python3 scripts/step11_23_assess.py \
  outputs/step11_23_usdce_wbtc_event_spread.json
```

Key output: `verdict`, `activity`, `maximum_heuristic_spread_bps`, `top_five_events`, `selected`, `verified_boundary`, `rpc_calls` and `block_traces`.

## Limits and interpretation

- Maximum 200 contiguous historical blocks; at most 1 full-block trace; max 30 JSON-RPC requests per run; 12 MB maximum per RPC response; ~120s total deadline (base RPC client).
- No full-block trace if the strongest event-derived spread is below the discovery threshold.
- The on-chain token identities and Uniswap 500 tier are rechecked at the historical end block before treating any event as a candidate.
- If a candidate passes screening, transaction ordering and the selected swap receipt/event are checked against a full ordered prestateTracer block diff. Incomplete or inconsistent evidence fails closed.
- `NO_EVENT_HEURISTIC_ABOVE_THRESHOLD`: no event-derived discrepancy exceeded the threshold **in this window**; not a market-wide no-go.
- `REJECT_KNOWN_UNISWAP_FEE_FLOOR`: the reconstructed spot discrepancy cannot cover the known 5 bp Uniswap swap fee.
- `BELOW_INDICATIVE_TWO_LEG_FEES_UNPROVEN`: fee hints suggest the route loses, but dynamic/directional Algebra fee remains unproven.
- `ABOVE_REPORTED_FEES_NEEDS_TICK_REPLAY`: potentially worth exact two-leg $100+ tick-aware quotation and gas modeling; **not proof of profitable execution**.
- No slippage-correct quotation, flash-loan settlement, gas-adjusted or validator-inclusion economics. No live trading authorization.

The previous Step 11.22 USDT/WBTC large snapshot spreads involve near-empty Algebra liquidity (raw active liquidity 4516), and the 100-tier WBTC pools show zero observed swaps over the window. These are **not** being used as arbitrage evidence.
