# Step 11.7 — Verified Polygon Swap Trace Capability

**October 2026 · research-only · chain 137 · no key/signing/broadcast**

## Why

Step 11.6 passed Python checks but found **0 preliminary positive routes** in 138 completed historical quotes over 3 separate windows. The previous debug API probe returned success for a historical arbitrary transaction, but this **does not** show that a verified pool swap has accessible *storage differences* and **does not reconstruct intra-block pool states**.

This version tests the next prerequisite for a bounded transaction-aware study, without buying a local node or making large unbounded RPC requests.

## What's new

* `scripts/step11_7_trace_probe.py` selects **one verified canonical Swap log** from a specified historical block and vetted pool; verifies block hash, transaction index against the canonical block list, and event presence in the successful transaction receipt.
* Runs a bounded `debug_traceTransaction` with `callTracer` and **separately** with `prestateTracer` `diffMode: true` against that same transaction. Reports whether it can observe a call tree and account/pool storage differences. An RPC rejection or malformed prestate result is explicitly labeled unavailable rather than confused with a zero-delta trace.
* Outputs only **trace metadata and SHA-256 summary** by default, not whole trace responses, account data, sensitive RPC URLs, or API keys.
* `scripts/step11_7_assess.py` checks structural consistency, canonical identifiers and false claims of execution readiness. It is **not** an independent trace replay or signed blockchain proof.
* `scripts/test_step11_7_trace_probe.py` contains **34 new tests**, for **168 Python tests** across the complete inherited project on this build machine.
* All prior Rust and Solidity project code remains present and unchanged; the most recent **87 Rust tests** were reported passing on the user's VPS in Step 11.4/11.5. Run `cargo test` locally to verify a fresh extraction.

## VPS requirements

Your existing 8 GB VPS; an HTTPS Polygon historical RPC that permits `eth_getBlockByNumber`, `eth_getTransactionReceipt`, `eth_getLogs`, `eth_call`, `eth_getCode`, and `debug_traceTransaction`. The endpoint previously returned chain ID **137** and a working basic `callTracer` response. **prestateTracer diff mode has not yet been demonstrated.**

No archive node or relay is required for this one-transaction capability test. The trace RPC response size is capped to 16 MB; oversized or unsupported traces are reported unavailable. RPC host/key are never printed.

## Install

Download the ZIP to Windows E: drive; in **Windows PowerShell**:

```powershell
scp "E:\step11_7_polygon_verified_swap_trace.zip" root@YOUR_VPS_IP:/root/
```

In Ubuntu SSH:

```bash
mkdir -p /root/polygon_step11_7
cd /root/polygon_step11_7
unzip -o /root/step11_7_polygon_verified_swap_trace.zip
cd step11_7_polygon_verified_swap_trace
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
cd rust && cargo test && cargo build --release && cd ..
```

If the environment variable was lost across SSH sessions, run this line **alone**, paste only the actual HTTPS endpoint at the prompt, press Enter, and export:

```bash
IFS= read -r -s -p "Polygon HTTPS RPC URL: " POLYGON_RPC_URL; echo
export POLYGON_RPC_URL
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

Expected chain ID: `137`. Do not share API keys or URLs.

## First targeted V3 Swap-trace probe

The user's Step 11.6 USDT/WETH scan identified an Algebra swap in historical block `96179661`. The script **independently discovers and verifies** the log and receipt rather than blindly trusting that old result.

```bash
python3 scripts/step11_7_trace_probe.py \
  --block 96179661 \
  --pair USDT_WETH \
  --venue algebra \
  --tier 500 \
  --output outputs/step11_7_trace_probe.json

python3 scripts/step11_7_assess.py outputs/step11_7_trace_probe.json
```

Expect to see `receipt=PASS`, `call_trace=SUPPORTED` (already observed on a different transaction), and either `prestate_diff=SUPPORTED` or `prestate_diff=UNAVAILABLE` on this **specific Swap transaction**. `changed_pool_storage_count` summarizes modified slots for all *verified pools* in the pair. The number is not a liquidity estimate or a profit value.

If no matching log exists for that exact block/pair/venue, it fails closed; do not change the venue or block blindly. If the prestate tracer is not available, a follow-up may probe `trace_replayTransaction` state diffs, which also requires capability verification. Do not infer support from a provider's generic documentation.

## Meaning of results

| Output | Meaning |
|---|---|
| `call_trace=SUPPORTED` | A call tree was returned for the selected pool-swap transaction. |
| `prestate_diff=SUPPORTED` | A syntactically valid per-tx `prestateTracer` diff was returned. |
| `changed_pool_storage_count>0` | At least one verified research pool appeared with storage fields in the returned diff. Not proof of reconstructed V3 state. |
| `prestate_diff=UNAVAILABLE` | Provider rejected or returned incompatible/incomplete diff-mode output, or response safety bounds were hit. Do not proceed with this method without a compatible RPC. |
| `PRESTATE_DIFF_AVAILABLE_RECONSTRUCTION_NOT_IMPLEMENTED` | Prerequisite available; next step is to actually reconstruct, decode and compare pool state after each tx. **Not** a trading opportunity. |

**Critical:** State differences at one transaction do not automatically provide complete storage snapshots for other pools, V3 ticks/position data, pre-transaction balances, or a callable intermediate state. A historical `eth_call` at block `N` still sees the **end of block N**, not the state immediately after tx index `k` in that block. Only independently validated state reconstruction and a compatible V3 executor could advance that research. No capital allocation or live signing is supported.

## Trace/API references

- [Geth built-in tracers — prestateTracer diffMode](https://geth.ethereum.org/docs/developers/evm-tracing/built-in-tracers)
- [Alchemy debug_traceTransaction](https://www.alchemy.com/docs/reference/debug_traceTransaction)
- [Alchemy Polygon Bor migration and trace methods](https://www.alchemy.com/docs/reference/polygon-pos-erigon-deprecation-notice)
