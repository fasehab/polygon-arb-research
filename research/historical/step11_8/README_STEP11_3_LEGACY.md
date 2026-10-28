# STEP 11.3 — Activity-triggered mixed V2/V3 historical research

## Status and scope

Step 11.2 **passed on the user's VPS**: 71 Rust tests, optimized release build and 48 historical V3 quote records, all gross-negative. This package retains that working code and adds an isolated **read-only** historical pool-activity scanner and Rust validator. It does not replace the V2-only Solidity executor.

New paths:

- `scripts/step11_3_event_mixed_collect.py`: bounded `eth_getLogs` activity scan, historical pool verification, QuickSwap/SushiSwap V2 router quotes paired with QuickSwap Algebra/Uniswap V3 quoter quotes in both directions.
- `rust/src/step11_3.rs`: offline fail-closed JSON validation, summary, tests.
- `scripts/test_step11_3_event_mixed_collect.py`: offline tests for log selection, RPC allowlisting, duplicate log handling, quote classification, unsupported pairs, provider-fallback isolation and never-authorize-execution policy.
- `STEP11_2_README.md`: previous stage's documentation.

**Events are pool activity, not necessarily swaps or price changes.** The scanner does not classify event topics as swaps. It selects a small number of historical blocks with verified pool logs to avoid repeatedly examining random unchanged reserves. It does not establish the historical transaction ordering that would have been available to a searcher at execution time.

**Exchanges:** QuickSwap V2 and SushiSwap V2 versus QuickSwap Algebra V3 and Uniswap V3 (0.05% or 0.30% tiers). Only the previously shortlisted DAI, WETH, USDC.e are permitted as research assets. Neither a V3 quote nor a V2/V3 mixed route is executable by the included V2-only Solidity contract.

## Infrastructure and cost discipline

- Your existing **8 GB RAM / 100 GB disk VPS** can run the Rust evaluator and this lightweight Python collector. No dedicated Polygon node is required.
- Use an external **Polygon archive-capable HTTPS read RPC**. Requires historical `eth_getLogs`, `eth_call`, `eth_getCode`, `eth_getBlockByNumber`, `eth_blockNumber`, `eth_chainId`.
- The scan window is capped at **400 blocks**, selected blocks at **2**, research sizes at **3**, and logs at **4500**. Shrink the window if your RPC restricts log queries. Run only a small initial scan, then widen if useful.
- Optional `POLYGON_RPC_FALLBACK_URL` retries the **entire scan** from the secondary provider, without mixing responses from different providers.
- **No WS subscription required for this historical experiment.** Polygon Private Mempool and Marlin relay are not used here and cannot replace archive read RPC.

## VPS installation

From Windows PowerShell, upload the saved ZIP after creating `/root/polygon_step11_3` on VPS:

```powershell
scp "E:\step11_3_polygon_mixed_event_discovery.zip" root@YOUR_VPS_IP:/root/polygon_step11_3/
```

On the VPS:

```bash
cd /root/polygon_step11_3
unzip -o step11_3_polygon_mixed_event_discovery.zip
cd /root/polygon_step11_3/step11_3_polygon_mixed_event_discovery
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
cd rust
cargo test
cargo build --release
cd ..
```

**Offline packaging result:** 54 Python tests passing; 81 Rust tests **declared** (71 prior + 10 new) and NOT compiled in packaging environment. A `cargo test` pass on the VPS is required before treating the Rust source as validated. Solidity fork tests from earlier steps remain bundled, unchanged, and do not prove mixed V2/V3 compatibility.

## Configure read-RPC privately in your current SSH session

```bash
IFS= read -r -s -p 'Paste Polygon HTTPS RPC URL (only URL): ' POLYGON_RPC_URL; echo
export POLYGON_RPC_URL
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

The expected chain ID is **137**. Paste only the URL into the hidden prompt, never an SSH command. Never publish the provider API key.

## Initial event-driven experiment

```bash
cd /root/polygon_step11_3/step11_3_polygon_mixed_event_discovery
python3 scripts/step11_3_event_mixed_collect.py \
  --from-block 96179700 --to-block 96179857 \
  --pairs DAI_WETH --sizes 1 --tiers 500 --max-events 2 \
  --output outputs/step11_3_mixed_events.json
./rust/target/release/polygon_sniper \
  --step11-3-report outputs/step11_3_mixed_events.json
```

This first scan selects at most two event-containing blocks from a 158-block window. It generates **up to 16 quotes** for one pair: two V2 venues × two V3 venue variants × two directions × one size × two historical blocks. An absence of events or missing pools is a legitimate, clearly marked nontrading result.

After validating the small scan you can add `--pairs DAI_WETH USDCe_WETH DAI_USDCe` or `--sizes 1 5` to widen research, subject to RPC limits and fee/slippage concerns. No need to do so if zero spread persists.

## Non-negotiable guardrails

- No account, signer, approval, on-chain submission, `eth_sendRawTransaction`, private relay or state override in the new collector. The Rust CLI strictly reads an offline JSON report.
- Pool addresses are discovered from the verified factories at the window anchor and rechecked at every selected historical block. Exact historical block hashes must match the pool log block hashes and remain unchanged through the read.
- Missing pool, zero active V3 liquidity, no activity, and V3 quoter reversion are explicit **skips**, never profitable signals. Unrelated provider errors fail closed or restart the full scan through fallback.
- Gross-positive quotations are always reported as **UNASSESSED**, pending actual mixed-route Solidity support, complete unmodified fork execution, gas and liquidity checks, security review, and inclusion analysis.
- Historical RPC JSON remains off-chain evidence, not signed or cryptographically authenticated proof. Quoted prices could have been transient or unavailable for actual transaction inclusion.

## Known limitations and future work

This stage only supports QuickSwap/SushiSwap V2 ↔ QuickSwap Algebra/Uniswap V3 combinations. It does not implement live event subscriptions, V3 callbacks, atomic mixed-route execution, on-chain gas accounting, tick-state independent replay, arb opportunity guarantees, or general token permissioning. Older V2 research features and reports remain available.

## Step 11.4 addendum

See STEP11_4_README.md for the swap-only selection logic, validation and stop/go gate. Existing Step 11.3 research modes are preserved.
