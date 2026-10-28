# Step 11.13 — Algebra high-impact intrablock spot screening

**Read-only research patch** for the existing `/root/polygon_step11_8/step11_8_polygon_tx_boundary_probe` installation. No signer, wallet, contract execution, state overrides, transaction broadcasting, or relay submission.

## Why this is different from Step 11.6

We have already established, for Polygon block 96179661, that an Algebra V3 swap in transaction 34 precedes a Uniswap V3 pool storage change in transaction 39. A complete block `prestateTracer` diff allows the Uniswap post-transaction-34 price to be recovered by rewinding later slot changes. The recovered price spread was ~1.046 bps against an immutable 5 bps Uniswap fee. That state was **gross-negative even before Algebra's fee and gas**.

Step 11.13 searches **actual Algebra Swap events** for the vetted USDT/WETH Algebra pool, scores the event-to-event price movement, and, if any movement clears the configured threshold, examines only **one highest-ranked block/candidate** with a full-block trace. It replays the Uniswap pool's *slot 0* and *slot 4* values backwards from the end-of-block storage through transaction-ordered prestate diffs. This can establish a conditional synchronized **spot price**, not an exact-input V3 quote.

## Requirements

* Existing Step 11.8 installation, including `outputs/step11_8_boundary.json` generated on the VPS.
* Step 11.9 script `scripts/step11_9_block_diff_probe.py` already installed and working.
* Python 3.9+, no added third-party packages.
* An HTTPS Polygon Mainnet archive-capable RPC in `POLYGON_RPC_URL` with `eth_getLogs`, historical `eth_call`, historical `eth_getStorageAt` and `debug_traceBlockByNumber` (`prestateTracer` `diffMode`). Chain ID must equal `137`.

## Install patch

In Windows PowerShell, after saving ZIP to E:\:

```powershell
scp "E:\step11_13_polygon_high_impact_patch.zip" root@YOUR_VPS_IP:/root/
```

On VPS:

```bash
cd /root/polygon_step11_8/step11_8_polygon_tx_boundary_probe
unzip -o /root/step11_13_polygon_high_impact_patch.zip
python3 -m unittest discover -s scripts -p 'test_step11_13_impact_probe.py' -v
```

## One-window run

First verify your RPC without revealing it:

```bash
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

Expected `137`. Run a max-200-block scan, with at most **one** subsequent full-block trace:

```bash
python3 scripts/step11_13_impact_probe.py \
  --from-block 96179600 --to-block 96179799 \
  --min-impact-bps 10 \
  --output outputs/step11_13_impact.json
python3 scripts/step11_13_assess.py outputs/step11_13_impact.json
```

At `--min-impact-bps 10`, the script will skip tracing unless an Algebra swap has moved its pool price at least ten basis points relative to its previous observed **end-of-transaction** price. To test the *mechanism*, not to hunt economic candidates, you can set `--min-impact-bps 1` in a **separate run** (this may trigger a paid RPC trace, and the highest-ranked event may differ from transaction 34). Do not run open-ended loops.

## Output classifications

* `NO_HIGH_IMPACT_ALGEBRA_SWAP` — no event qualified; typically 4 ordinary RPC reads and **no block trace**. Does not prove no arbitrage.
* `UNPROVEN` — selected event cannot be fully validated at the boundary; `reason` explains why. Do not bypass.
* `REJECT_UNISWAP_FEE_FLOOR` — the **best-case instantaneous spot edge** cannot cover **Uniswap's** 500/1,000,000 fee, even before Algebra's dynamic fee, spread/slippage, flash-loan fee, inclusion costs and gas.
* `ABOVE_ONE_LEG_FEE_ONLY_FULL_REPLAY_REQUIRED` — fee-floor screen alone did not reject. This is **not an executable arbitrage quote** or proven profitable trade; the Algebra fee is excluded and a full state-correct, tick-aware fork replay would be required.

All result JSON is ordinary RPC evidence, not cryptographically authenticated proof of Polygon transaction execution. The `assess` command checks **internal consistency only**.

## Hard limits and scope

* At most 400 contiguous blocks per scan; at most 750 Swap logs; at most 500 transactions in selected block.
* At most 20 RPC requests, including **one** `debug_traceBlockByNumber`; 12 MB maximum RPC response, 120-second overall deadline (enforced by inherited Step 11.9 transport).
* Only **USDT/WETH Algebra V3** as swap target and the pinned Uniswap V3 0.05% pool as competitor. The historical token identities and fee are independently checked at the selected block.
* Does **not** reconstruct initialized tick liquidity, real swap price impact, dynamic Algebra fee, transaction-position access, net gas P&L, validator ordering, or private inclusion.
* Does **not** scan Uniswap-triggered swaps. Expanding safely to other pairs/venues requires another independently validated adapter.
* The single best price-moving candidate may be unprovable if its target Algebra account changes later in the same block. The script **does not** retry other candidates automatically or loosen constraints.
* Source files intentionally avoid all signing/broadcast code. Existing Solidity executor remains V2-only.

Technical basis: geth built-in prestateTracer diff semantics, `debug_traceBlockByNumber` ordered transaction results, Uniswap V3 `Swap` event ABI and pool slot layout. See Geth and Uniswap canonical documentation.
