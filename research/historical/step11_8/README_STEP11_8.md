# Step 11.8 — Historical V3 transaction-boundary getter validation

**Scope:** Research-only capability test for a single confirmed QuickSwap Algebra V3 USDT/WETH Swap at Polygon block **96179661** (previously observed at tx index **34**). No private key, signing, relay, state override, contract deployment, or transaction broadcast.

## What changed since Step 11.7

Step 11.7 independently verified a receipt, a call trace, and `prestateTracer`'s storage diff for a selected swap. That did **not** reconstruct the state between transactions.

Step 11.8 tries **`debug_traceCall` with `txIndex`**, where supported, to read `globalState()` and `liquidity()` on the target Algebra pool immediately **before** the selected transaction and **after** it. It also reads Uniswap V3's `slot0()` and `liquidity()` at the *same proposed after-boundary*. Importantly, it does not simply trust a successful JSON-RPC response:

1. It repeats the strict block, pool identity, transaction index, swap-log and receipt checks inherited from Step 11.7.
2. It decodes the **last** `Swap` event from that specific Algebra pool in the target transaction (price, active liquidity, tick).
3. It independently requires a `prestateTracer` storage delta from that target pool.
4. It tests whether `txIndex=transactionIndex+1` getter values agree with the final event, and whether `txIndex=transactionIndex` differs from those after-state values.
5. It checks the counterparty pool getter at the same proposed after-boundary.
6. If the provider rejects/ignores `txIndex` or getters and events disagree, it emits **`UNPROVEN`**, not an invented reconstruction or an executable trading candidate.

This verifies at most **public getter values** at a transaction boundary. A complete V3 quote also requires initialized ticks, the tick bitmap, fee logic, and correct swap simulation. **There are no arbitrary token quotes, gas profit claims, or live trade permissions in Step 11.8.**

Geth documents `txIndex` as an option of `debug_traceCall`, but compatibility with your particular Polygon RPC endpoint is an **empirical unknown** until tested. Geth's `prestateTracer` diff intentionally omits unchanged storage, which is why the probe uses getter calls instead of guessing that one changed slot constitutes an entire pool.

## Installation

Download `step11_8_polygon_tx_boundary_probe.zip` to Windows `E:\`, then from Windows PowerShell upload:

```powershell
scp "E:\step11_8_polygon_tx_boundary_probe.zip" root@YOUR_VPS_IP:/root/
```

On your Ubuntu VPS:

```bash
mkdir -p /root/polygon_step11_8
cd /root/polygon_step11_8
unzip -o /root/step11_8_polygon_tx_boundary_probe.zip
cd step11_8_polygon_tx_boundary_probe
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
```

Rust and Solidity source files are carried forward unchanged. To recheck Rust:

```bash
cd rust && cargo test && cargo build --release && cd ..
```

## Run the one-transaction probe

Keep `POLYGON_RPC_URL` configured in **the same SSH shell**. Never paste the full URL or API key into chat.

```bash
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

Expected Polygon PoS chain ID: **137**.

```bash
python3 scripts/step11_8_boundary_probe.py \
  --block 96179661 --pair USDT_WETH --tier 500 \
  --output outputs/step11_8_boundary.json
python3 scripts/step11_8_assess.py outputs/step11_8_boundary.json
```

### Outcomes

- `VERIFIED_TWO_POOL_TRANSACTION_BOUNDARY`: The observed before/after getter values pass the storage-diff and receipt-event consistency checks. Still not a complete concentrated-liquidity reconstruction and **NOT** a profitable arbitrage claim.
- `UNPROVEN` plus an `UNPROVEN_REASON`: The RPC returned insufficient, contradictory, or unsupported `txIndex`/getter evidence. No false-success fallback to end-of-block state.
- `FAIL-CLOSED`: Historical pool identity/receipt/block or structural validation failed. Inspect the error; do not loosen assertions.

The report contains only hashes, public contract addresses and public pool state. It does **not** write raw call traces or expose the RPC key.

**Stop point:** Only after the target and competitor getters can be corroborated at the same boundary should a later stage attempt *accurate* V3 quoting and fork-state validation. We cannot infer inclusion opportunity, flash-loan solvency, execution gas, or eventual profit here.
