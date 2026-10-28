# Step 11.14 — Second qualified Algebra swap, one bounded trace

**Research-only. No signing, broadcasting, deployment, real V3 quotes, or profit claims.**

## Context and purpose

The VPS previously screened Polygon blocks `96179600..96179799` with a 4-bps Algebra USDT/WETH event-to-event impact threshold: 21 swap logs, 2 qualified, highest-ranked candidate at block 96179705 tx index 1. Its reconstructed same-boundary two-pool spot spread was 4.68083948 bps and the one-leg Uniswap 500 fee-only ceiling was -0.32150094 bps. The first candidate is rejected.

This patch asks one new question: **What happens when we explicitly test ranked candidate #2 from the same window?**

The existing Step 11.13 scanner always selected index 0 (`passed[0]`). This patch adds a **separate** `step11_14_ranked_probe.py` preserving the original scanner and pins `--candidate-rank 2` by default. It reports the top two qualified candidate identifiers and rejects absent rank 2 without tracing. The same trace-based Uniswap slot0/slot4 rewind, token-order and fee checks, receipt verification, and hard RPC ceilings remain in force.

## Installation on the existing VPS

Save ZIP to `E:\step11_14_second_candidate_patch.zip`, then PowerShell:

```powershell
scp "E:\step11_14_second_candidate_patch.zip" root@YOUR_VPS_IP:/root/
```

On VPS:

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_14_second_candidate_patch.zip
python3 -m unittest discover -s scripts -p 'test_step11_14_ranked_probe.py' -v
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

Expected test result: `Ran 9 tests ... OK`; chain id `137`.

## Second candidate live historical probe — read-only

```bash
python3 scripts/step11_14_ranked_probe.py \
  --from-block 96179600 \
  --to-block 96179799 \
  --min-impact-bps 4 \
  --candidate-rank 2 \
  --output outputs/step11_14_second_candidate.json

python3 scripts/step11_14_assess.py outputs/step11_14_second_candidate.json
```

**Budget for this invocation:** max 20 RPC method requests (including one full-block `debug_traceBlockByNumber`), 12 MB max response, 120 seconds total, and 400 contiguous blocks max. This protocol is read-only and performs no transaction signing or broadcast. Block tracing may consume multiple provider compute units even when counted as one method call.

## Verdicts

- `REJECT_UNISWAP_FEE_FLOOR`: reconstructed spot ceiling negative even *before* Algebra fee, slippage, flash-loan and gas costs. Do not execute.
- `ABOVE_ONE_LEG_FEE_ONLY_FULL_REPLAY_REQUIRED`: preliminary **spot** ceiling above only known Uniswap fee; no actual arbitrage P&L established. Requires independent reconstruction and tick-aware replay.
- `UNPROVEN`: RPC or chain evidence inadequate/inconsistent. Stop, inspect `reason`.
- `CANDIDATE_RANK_UNAVAILABLE`: fewer than 2 candidates passed threshold. No trace.
- `NO_HIGH_IMPACT_ALGEBRA_SWAP`: zero qualifying events. No trace.

`step11_14_assess.py` checks report consistency only, *not independent on-chain verification*.

## Existing code

Depends on `scripts/step11_9_block_diff_probe.py` and the saved original `outputs/step11_8_boundary.json`. Requires Python 3.9+. Uses stdlib only. Does **not** overwrite existing Step 11.13 files. Offline tests mock RPC responses; real-chain results come solely from your VPS execution.

Technical reference: Geth `prestateTracer` diffMode omits unchanged storage slots and returns pre/post changed values (https://geth.ethereum.org/docs/developers/evm-tracing/built-in-tracers). Uniswap V3 true executable quotation requires complete initialized tick information (https://developers.uniswap.org/docs/sdks/v3/guides/pool-data).
