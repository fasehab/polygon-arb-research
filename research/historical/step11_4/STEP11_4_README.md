# Step 11.4 — Swap-Event-Only Historical Screen and Strategy Viability Gate

**Purpose:** replace Step 11.3's activity signal (any pool log, including liquidity updates) with *actual Swap events* on verified Polygon V2/V3 pools. Log ordering by `transactionIndex` and `logIndex` is preserved and independently verified by the new Rust offline report mode. Historical `eth_call` quotations are still **end-of-block** snapshots, not after-transaction state or a claim of possible transaction insertion.

## Strategic finding and stop rule

Step 11.3's 152 completed, gross-unprofitable routes were **not** 152 independent price opportunities. Continuing to add test counts without finding a positive quote is insufficient evidence of economic viability. Step 11.4 is a **measurement gate**; it is not a trade-authorized module.

Pre-commit to a research budget (e.g. 8–12 non-overlapping 400-block windows, one/two selected swap blocks per window, and only vetted pairs). If none yield a reproducible **gross-positive** quote, *pause this specific two-venue flash-arbitrage strategy* and pivot to a different hypothesis (additional active venues, different liquidity venues or live shadow measurement). If gross-positive quotes appear, they must be reproduced on an unmodified fork with a V3-compatible executor, actual fee/gas calculations and transaction-order/inclusion constraints. Any positive quote remains `UNASSESSED`.

We do **not** claim those 8–12 windows give statistical proof about global profitability; the gate is a disciplined research stop condition.

## Hardware / RPC

Works on existing 8 GB RAM / 100 GB VPS; uses Polygon HTTPS archive-capable RPC such as the currently working Alchemy endpoint. No dedicated node or paid tracing method required. Bounded at 400 blocks, two selected blocks, three vetted pairs, three whole-unit sizes, and Uniswap V3 fee tiers 500 and 3000. Read-only RPC allowlist (`eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`, `eth_getLogs`, `eth_getCode`, `eth_call`). No private keys, deploy, transaction simulation overrides, transaction broadcast or Marlin submission.

Standard V2 `Swap(address,uint256,uint256,uint256,uint256,address)` and standard V3-compatible `Swap(address,address,int256,int256,uint160,uint128,int24)` topics only. Other Algebra versions with different Swap topics are out of scope; if the pool does not emit the recognized signature, the scanner does not count its swaps. Test token shortlist unchanged: DAI, USDC.e, WETH. V2 contract remains unchanged and V2-only.

## Install on VPS

```bash
mkdir -p /root/polygon_step11_4
cd /root/polygon_step11_4
unzip -o step11_4_polygon_swap_viability.zip
cd step11_4_polygon_swap_viability
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
cd rust && cargo test && cargo build --release && cd ..
```

Expect **74 Python tests** locally; Rust **87 tests** anticipated (previous 81 plus 6), subject to *VPS compilation*. This package was not locally compiled with Rust.

Set `POLYGON_RPC_URL` in your current shell; ensure `cast chain-id` returns 137. Do not paste URLs/keys into chats or logs.

## First real historical scan: small RPC budget

```bash
python3 scripts/step11_4_swap_collect.py \
  --from-block 96179600 --to-block 96179799 \
  --pairs DAI_WETH USDCe_WETH \
  --sizes 1 --tiers 500 --max-events 2 \
  --output outputs/step11_4_swap_events.json

./rust/target/release/polygon_sniper \
  --step11-4-report outputs/step11_4_swap_events.json
```

The 200-block window is illustrative; zero swaps / no profitable quotes are valid outcomes. For a second non-overlapping window, adjust `--from-block`/`--to-block` to another 200–400 historical blocks, maintaining the limits. `max-events` caps *selected event-containing blocks*, not total logs. RPC errors fail closed, and a fallback RPC restarts the entire scan without mixing data providers.

## Scope limitations

- `eth_getLogs` lists confirmed Swap events with transaction order. **It does not provide intermediate EVM storage** after each transaction.
- `eth_call` at block N evaluates pool state at the end of block N, potentially *after all of its swaps*. It cannot prove the profitability of inserting a transaction between historical transactions.
- V3 quotes may revert; these are skips, not opportunities. Pool activity is not itself price divergence.
- Historical data comes from an RPC provider and is not signed cryptographic proof.
- Flash-loan fees are quoted; *gas, slippage under competing transactions, private relay inclusion, V3-compatible execution and contract security are not proven.*
- Successful research is not a reason to deploy live or invest capital. Budget capital for transaction gas and failure risk only if an independent pilot study justifies it.
