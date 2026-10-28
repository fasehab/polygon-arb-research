# Step 11.21B — USDC.e/WETH Fee-Aware Event and Intrablock Screening

**Read-only historical Polygon research. No signing, broadcasting, execution, or profit claim.**

This is an additive patch to the existing `/root/polygon_step11_8/step11_8_polygon_tx_boundary_probe` installation. It does not modify the Step 11.20 scanner. It requires these previously installed modules in the same `scripts/` directory:

- `step11_9_block_diff_probe.py`
- `step11_13_impact_probe.py`

## Verified historical pool configuration (from user RPC output)

- Polygon POS chain 137
- Target window: block `96179600` through block `96179799` (200 blocks)
- QuickSwap Algebra: `0x55CAaBB0d2b704FD0eF8192A7E35D8837e678207`
- Uniswap V3 500: `0x45dda9cb7c25131df268515131f647d726f50608`
- token0 USDC.e `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
- token1 WETH `0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619`
- Both pools returned positive in-range `liquidity()` at block 96179799, which does **not** prove executable depth.

This patch independently reconfirms token ordering and Uniswap fee on-chain at the window end before screening.

## Install

From the existing project directory:

```bash
unzip -o /root/step11_21b_usdce_event_probe.zip
sha256sum -c SHA256SUMS_STEP11_21B
python3 -m unittest discover -s scripts -p 'test_step11_21b_usdce_event_spread.py' -v
```

## One bounded live RPC probe

Confirm the existing `POLYGON_RPC_URL` is an HTTPS Polygon endpoint (do not share credentials). Then:

```bash
python3 scripts/step11_21b_usdce_event_spread.py \
  --from-block 96179600 \
  --to-block 96179799 \
  --min-spread-bps 9 \
  --output outputs/step11_21b_usdce_event_spread.json
python3 scripts/step11_21b_assess.py outputs/step11_21b_usdce_event_spread.json
```

Expected console fields: `verdict`, `activity`, `heuristic_candidates`, `maximum_heuristic_spread_bps`, `top_five_events`, `selected`, `verified_boundary`, `rpc_calls`, `block_traces`.

## Safety and validity limits

- 200 blocks max per run; 30 RPC calls max; **one** whole-block prestateTracer difference request max; 120-second overall runtime; 12-MB per-request response cap.
- No retries, no signing, no broadcasting, no trading capital, no wallet/private-key access.
- Ranking comes from historical *Swap event-derived state*, not independent per-event two-pool state reads; candidate selection is a **heuristic**.
- Only the highest-ranked qualifying candidate is subjected to historical transaction-ordered trace recovery. The trace must be complete, in order, and account storage-diff consistent. Dynamic Algebra swap fees may differ from `globalState.lastFee`. The two-leg fee calculation is an **indicative screen**, not a hard negative proof.
- The spot spread alone proves no route is profitable. Positive preliminary fee spread still requires historical tick-aware quotes, 100+ USDC.e depth, flash loan, gas, and a realistic inclusion path; V3 Solidity execution remains unbuilt.
- `step11_21b_assess.py` checks output consistency only; it is **not** independent blockchain verification.

If no candidate passes, report still displays the maximum event-derived spread and top five events to inform the next research decision. If a trace cannot be independently verified, result is `UNPROVEN`.

## Offline validation

30/30 Step 11.21B offline tests passed. With 22 Step 11.13 and 25 Step 11.20 regression tests, 77/77 pass in the clean local test environment. **No live Polygon RPC calls were made during package creation**.
