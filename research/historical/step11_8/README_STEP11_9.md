# Polygon Step 11.9 — bounded full-block state-diff experiment

Research-only overlay for `step11_8_polygon_tx_boundary_probe`.

## Background

The previous Geth `debug_traceCall` with `txIndex` was rejected by the user's Alchemy Polygon endpoint with JSON-RPC -32602. Unlike this rejected API option, `debug_traceBlockByNumber` is documented as replaying an entire block. `prestateTracer` with `diffMode` can report changed accounts per transaction in execution order.

## Strict study constraints

- Pinned historical Polygon block **96179661**, target transaction index **34**, pool identities and receipt-derived event values taken from `outputs/step11_8_boundary.json`.
- One `debug_traceBlockByNumber` request per run, at most **9 RPC requests overall**, up to **12 MB per RPC response**, and **120 seconds total wall-clock budget** (individual calls limited to 30 s; block trace 75 s).
- Reuse verified Step 11.8 anchor; check chain 137, block hash, target transaction position, and last Algebra Swap event independently.
- Require a full, ordered block diff with a hash for every transaction. Examine **all transactions after index 34** for any state changes to either pool.
- If either pool changes after index 34, do **not** infer that end-of-block getter values equal the immediate post-transaction state.
- If neither pool changes after index 34, read both sets of pool getters at the pinned block. Require the Algebra getter price, tick and active liquidity to match its last receipt Swap event.
- Even a matching result is **conditional same-boundary getter equivalence only**. It does not reconstruct tick bitmaps, attest cryptographic proof, provide trade-size quotations, or prove economic feasibility.
- Missing trace method, unsupported diff options, huge response, chain mismatch, incorrect transaction sequence, or any other uncertainty must fail closed.

## Install into the existing project

After uploading this ZIP to `/root/`:

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_9_bounded_block_diff_patch.zip
python3 -m unittest discover -s scripts -p 'test_step11_9_block_diff_probe.py' -v
```

Your HTTPS `POLYGON_RPC_URL` must be exported in the same shell; never paste it into chat or store it in logs.

```bash
python3 scripts/step11_9_block_diff_probe.py \
  --input outputs/step11_8_boundary.json \
  --output outputs/step11_9_block_diff.json
python3 scripts/step11_9_assess.py outputs/step11_9_block_diff.json
```

## Outcomes

- `CONDITIONAL_SAME_BOUNDARY_GETTER_EQUIVALENCE`: A full ordered diff shows no later changes to either pool; both end-of-block getters can be used as conditional post-tx getter values. No proof of V3 swap quotation or transaction execution.
- `INTRABLOCK_EQUIVALENCE_UNPROVEN` + `RPC_REJECTED`, `RESPONSE_SIZE_CAP_EXCEEDED`, etc.: Stop. No success claim.
- `INTRABLOCK_EQUIVALENCE_UNPROVEN` + `LATER_POOL_ACCOUNT_CHANGES_DETECTED`: Full replay shows at least one later pool change. End-block shortcut cannot be used.

If block tracing isn't supported on your RPC tier, do **not** upgrade or buy a node just for this experiment. Preserve the evidence and consider collaborator-provided infrastructure.
