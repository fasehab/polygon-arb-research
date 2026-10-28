# Step 11.20 — Event-triggered cross-pool spread discovery (research-only)

**NO signing, execution, trading, or profit claim.** Python standard library only, in addition to existing Step 11.9 and 11.13 scripts.

## Why this experiment

Step 11.19 produced six negative end-of-block cross-venue roundtrip quotes. This new step queries **Swap logs from BOTH QuickSwap Algebra V3 and Uniswap V3 fee-500 USDT/WETH pools** and ranks transactions by the *event-inferred cross-pool discrepancy* rather than one venue's swap impact. At most ONE top-ranked candidate is investigated through transaction-ordered historical `debug_traceBlockByNumber` with `prestateTracer` diff mode. No `txIndex` option is used.

Source pool identities and initial contract linkage are loaded and validated using `outputs/step11_8_boundary.json`. The exact pool addresses are not silently replaced.

## Install (in the existing Step 11.8 directory)

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_20_fee_aware_event_probe.zip
sha256sum -c SHA256SUMS_STEP11_20
python3 -m unittest discover -s scripts -p 'test_step11_20_event_spread.py' -v
```

Expected: **25 tests**. For prior regression (optional):

```bash
python3 -m unittest discover -s scripts -p 'test_step11_13_impact_probe.py' -q
```

## Run bounded first experiment

Ensure `POLYGON_RPC_URL` is already set to your own Polygon HTTPS read endpoint (never share it). Verify `/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"` returns `137`.

```bash
python3 scripts/step11_20_event_spread.py \
  --from-block 96179600 \
  --to-block 96179799 \
  --min-spread-bps 12 \
  --output outputs/step11_20_event_spread.json
```

Limits: 200 blocks; at most 23 RPC requests; one full-block trace; each provider response at most 12 MB through existing Step 11.9 RPC helper; 120-second wall-time budget; at most 1,000 Swap logs, and 500 transactions in traced block. No automatic retry, signer, wallet, broadcast, deployment, or private key.

## Meaning of output

- `NO_EVENT_HEURISTIC_ABOVE_THRESHOLD`: No candidate crossed the 12 bp *event-inferred* threshold; no trace requested. This does **not** prove opportunities were absent.
- `UNPROVEN`: Required chain evidence mismatched, the trace was incomplete, target pool changed after the transaction, ABI unsupported, or a resource constraint was hit. Never substitute an end-block quote for failed reconstruction.
- `REJECT_KNOWN_UNISWAP_FEE_FLOOR`: Reconstructed mathematical spot edge is below even the **known** Uniswap 5 bp fee, assuming best trade direction, no price impact.
- `BELOW_INDICATIVE_TWO_LEG_FEES_UNPROVEN`: Passes known Uniswap fee but fails a two-leg screen using an Algebra `globalState` *fee hint*. Dynamic or directional Algebra fees could differ; this is **not** a hard rejection.
- `ABOVE_REPORTED_FEES_NEEDS_TICK_REPLAY`: Spot edge clears indicative fees only. Not a quote, not profitable execution, not a $100 liquidity result.

The `min-spread-bps 12` cutoff is a **research ranking filter**, not a comprehensive profitability boundary. When a swap in both pools occurs within the same transaction, that candidate is skipped conservatively. Only swaps in the two selected pools are scanned; no global Polygon opportunity claim is made. The scan checks transaction-indexed **Swap events**; it is not a full sequential replay for every pool mutation. Full-block tracing is performed only on the selected candidate.

The Algebra `globalState()` ABI varies by version: 6 words (Integral/lastFee), 7 words (ambiguous older versus directional variants), and 8 words (directional). A reported last fee is not proof of the fee applied on a hypothetical swap. The program separately computes the known-Uniswap-fee-only optimistic ceiling and the fee-hint ceiling, explicitly without declaring an effective Algebra fee verified.

Post-transaction boundary reconstruction uses the last Swap event in the selected transaction, full trace hash ordering, no later Algebra account changes, and rewinding Uniswap slot0 and liquidity slot through later transaction diffs with consistency checks. It does not provide a full initialized-tick state, the right to submit in another actor's transaction boundary, or any atomic fork transaction.

## Technical dependencies

- Installed `scripts/step11_9_block_diff_probe.py`.
- Installed `scripts/step11_13_impact_probe.py`.
- Saved `outputs/step11_8_boundary.json` (your original, untouched).
- Python 3.10+ and Python stdlib.

Offline tests use mocked RPC responses; **real endpoint behavior and independent proof remain to be established** by the VPS run.

## After a result

Share the four console lines beginning `verdict:`, `activity:`, `heuristic_candidates:`, and `verified_boundary:` plus `rpc_calls:`. Do not share your RPC URL/API key.
