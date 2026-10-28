# Step 11.8 — START HERE: README_STEP11_8.md

This ZIP retains the original project sources, archived milestone documentation, 87 inherited Rust tests and prior Python suites. The new read-only transaction-boundary getter probe is documented in `README_STEP11_8.md`.

---

# Step 11.6 addendum — start with README_STEP11_6.md

This bundle preserves prior Step 11.5 functionality.

# Polygon arbitrage research — Step 11.5 V3-first viability study

**October 2026 | Research-only | Polygon PoS chain ID 137**

This release **preserves** the Step 11.4 Rust and Solidity code and adds a Python
V3-first, bounded historical research pipeline. Nothing deploys, signs, sends,
simulates contract execution, calls a relay, or attempts an on-chain transaction.

## Why this release exists

Steps 11.2–11.4 verified historical V2/V3 quoting, transaction-ordered canonical
Swap-event selection, fail-closed RPC evidence handling, and safe rejection of
loss-making quotes. The user's VPS reported **87/87 Rust tests passing** at
Step 11.4 and **0 gross-positive quotes** across the sampled scenarios. These
results demonstrate working safety logic, **not a profitable trading strategy**.

Step 11.5 asks whether a **V3-first route** can clear an explicit *assumed*
gas stress budget and an absolute/relative minimum return threshold. If it
cannot, we will stop repeatedly extending the same static two-pool hypothesis.

Official contracts: QuickSwap Polygon PoS V3 Algebra Factory
`0x411b0fAcC3489691f28ad58c47006AF5E3Ab3A28` and Quoter
`0xa15F0D7377B2A0c0c10db057f641beD21028FC89`; Uniswap V3 Polygon
Factory `0x1F98431c8aD98523631AE4a59f267346ea31F984` and QuoterV2
`0x61fFE014bA17989E743c5F6cB21bF9697530B21e`. See official URLs:
https://docs.quickswap.exchange/overview/contracts-and-addresses and
https://developers.uniswap.org/docs/protocols/v3/deployments/v3-polygon-deployments.
The code also verifies identities and contract code at historical blocks.

## What's new

- `scripts/step11_5_viability.py` — canonical Swap-event-selected block
  evidence using Step 11.4, plus V3/V3 and QuickSwap V2/V3 round-trip
  quotations. **Uniswap fee tiers 500 and 3000** are available. SushiSwap
  remains in the inherited base evidence but is **excluded from Step 11.5's
  primary viability rankings**.
- Seed sizes are ascending whole stablecoins, e.g. `1 5 25`. When a
  route has a **gross-positive coarse quote**, the script tests up to two
  neighboring midpoint sizes. These are heuristic probes, not an optimizer
  proving a global maximum. The search is capped to protect your 8 GB VPS
  and the RPC subscription.
- On-chain historical gas conversion proxy: verified QuickSwap V2
  wrapped-native / borrowed stablecoin reserves, one wrapped-native unit,
  canonical 0.3%-fee constant-product calculation. If the direct pool does
  not exist, the **net result is UNASSESSED**; prices are never invented.
- **Gas stress is not a measurement:** user-provided gas units and gas gwei,
  plus a buffer. No claim about actual transaction gas, inclusion or
  execution is possible without a new executor and full transaction replay.
- `scripts/step11_5_assess.py` independently recomputes amounts, Vault fees,
  derived gross / gas-budget / modeled-net arithmetic, venue whitelist,
  duplicates, minimum-return floor, and verdicts. Run the prior Rust
  `--step11-4-report` on the **same** JSON first to check the predecessor's
  log and historical snapshot structure.
- `scripts/test_step11_5_viability.py` has **27** new tests. Across all
  inherited suites **101 Python tests passed locally** in packaging.
  **Rust toolchain was unavailable for local compilation**; the Rust and
  Solidity sources are unchanged from Step 11.4. Confirm inherited Rust
  baseline via `cargo test` on your VPS.

## Infrastructure

Your VPS with approximately **8 GB RAM and a 100 GB disk** is appropriate for
this bounded remote-RPC study; **not** for a self-hosted Polygon archive node.
The required endpoint is an archive-capable Polygon HTTPS *read* RPC. Neither
Polygon Private Mempool nor Marlin Relay is a replacement for historical RPC.
Accepted RPC actions: historical `eth_call`, `eth_getCode`, `eth_getLogs`,
`eth_getBlockByNumber`, `eth_blockNumber`, `eth_chainId` only.
An optional fallback HTTPS RPC fully re-starts a failed study and never mixes
the providers within one output file. Never send your RPC credentials in chat.

Limits: at most **400 historical blocks per request**, **2 selected event blocks**,
**3 pair IDs**, **3 coarse sizes**, and **300 potential overlay quote records**
(including bounded refinements); combinations above the cap fail before RPC.
The inherited Step 11.4 collector may use additional RPC reads.

## Deploy ZIP to VPS

Windows PowerShell, **after saving this ZIP on `E:`**:

```powershell
scp "E:\step11_5_polygon_v3_first_viability.zip" root@YOUR_VPS_IP:/root/
```

Ubuntu SSH terminal:

```bash
mkdir -p /root/polygon_step11_5
cd /root/polygon_step11_5
unzip -o /root/step11_5_polygon_v3_first_viability.zip
cd /root/polygon_step11_5/step11_5_polygon_v3_first_viability
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
cd rust && cargo test && cargo build --release && cd ..
```

### Configure the read RPC if needed

Only run the following `read` line *by itself*, paste ONLY your existing
Polygon HTTPS URL at the invisible prompt, and press Enter:

```bash
IFS= read -r -s -p "Polygon HTTPS read RPC URL: " POLYGON_RPC_URL; echo
export POLYGON_RPC_URL
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"
```

Expected chain ID: `137`. Do not echo the URL, especially the API key.

## Initial cost-controlled study

```bash
cd /root/polygon_step11_5/step11_5_polygon_v3_first_viability
python3 scripts/step11_5_viability.py \
  --from-block 96179600 --to-block 96179799 \
  --pairs USDCe_WETH --tiers 500 --sizes 1 5 25 \
  --max-events 1 --gas-units 750000 --gas-gwei 100 \
  --gas-buffer-bps 12500 --min-net-units 1 --min-net-bps 20 \
  --output outputs/step11_5_first.json
```

Then validate **both the original historical evidence and new study arithmetic**:

```bash
./rust/target/release/polygon_sniper --step11-4-report outputs/step11_5_first.json
python3 scripts/step11_5_assess.py outputs/step11_5_first.json
```

Only if this works, broaden to both main pairs / both Uniswap fee tiers
while preserving the 300-quote cap:

```bash
python3 scripts/step11_5_viability.py \
  --from-block 96179458 --to-block 96179857 \
  --pairs DAI_WETH USDCe_WETH --tiers 500 3000 --sizes 1 5 25 \
  --max-events 2 --gas-units 750000 --gas-gwei 100 \
  --gas-buffer-bps 12500 --min-net-units 1 --min-net-bps 20 \
  --output outputs/step11_5_broader.json
./rust/target/release/polygon_sniper --step11-4-report outputs/step11_5_broader.json
python3 scripts/step11_5_assess.py outputs/step11_5_broader.json
```

## Verdict meaning

| Verdict | Meaning |
|---|---|
| `SKIP_*` | A required pool, Vault liquidity, or complete quote was unavailable. |
| `REJECT_GROSS_UNPROFITABLE` | Round-trip quote does not repay principal plus Vault flash fee. |
| `UNASSESSED_GAS_FX` | Gross positive, but no verified direct native-to-token conversion pool. |
| `REJECT_MODELED_NET` | Gross positive but below *assumed* gas budget plus net profit floor. |
| `MODEL_POSITIVE_REPLAY_REQUIRED` | Clears this stress model; **NOT executable, guaranteed, or approved**. |

`--gas-units` and `--gas-gwei` are scenario assumptions. Compare multiple
stress scenarios if the first sweep finds positives; do not call the outputs
measured gas or executable net profits.

### Research go/no-go criterion (heuristic, not statistical proof)

Use **nonoverlapping** swap-rich historical windows, track unique selected
block hashes, and record the proportion of complete quotes and unavailable FX
screens. If after approximately **20 windows / up to 40 selected historical
states** we still see no model-positive candidate *and* quotes are sufficiently
complete, stop developing this static two-pool approach and evaluate another
mechanism or venue mix. Those limits are operational heuristics, not evidence
that arbitrage is mathematically impossible. If the data are incomplete,
address the missing coverage before drawing a conclusion.

Even if a model-positive route is found, proceed to a **separate Step 12**:
independent on-chain state verification, compatible V3 Solidity executor,
unmodified fork with flash loan and final repayment, real `eth_call`/gas
measurement, slippage and inclusion stress analysis, and independent security
review. Do not invest or enable a signer based on a quote-only report.

## Read-only assurance and limitations

This stage has no private key, account creation, contract deploy, signer,
transaction submission, relay, state override, or ability to move funds.
It does **not** recreate intra-block transaction state. Historical `eth_call`
uses the block's final state; the Swap log's transaction index only selects
and documents the block. The program does **not** test mempool latency,
competition, bundle validity, or real cash profitability.

The preceding V2-only `solidity/src/BalancerMEVSniper.sol` remains untouched.
Full history and Step 11.4 details: `STEP11_4_README.md` and
`README_STEP11_3_LEGACY.md`.
