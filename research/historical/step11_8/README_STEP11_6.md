# Step 11.6 — Active-Liquidity Discovery and a Strategy Go/No-Go Gate

**Polygon PoS mainnet (chain 137), strictly read-only; October 2026 research package.**

The inherited Rust searcher and Solidity flash-loan executor are unchanged from Step 11.5 / Step 11.4; the executor **cannot** execute V3 routes. This module adds two Python programs, `scripts/step11_6_discover.py` and `scripts/step11_6_assess.py`, plus a regression test suite. No signer, private key, deployment, mempool submission, relay or broadcast code is added.

## Why this iteration is different

Previous snapshots of DAI/WETH, USDC.e/WETH and DAI/USDC.e were gross-negative at sampled historical states. More tests were validating rejection, **not** an economically viable opportunity. Step 11.6 expands the **research-only** token universe to Polygon PoS USDT (6 decimals) and WBTC (8 decimals) and derives the corresponding QuickSwap V2, QuickSwap Algebra V3 and Uniswap V3 pools from the verified factories. SushiSwap is excluded from new ranking but remains in legacy code/tests. Existing DAI/WETH, USDC.e/WETH, DAI/USDC.e are still optionally available as controls.

Research pairs: `DAI_WETH`, `USDCe_WETH`, `DAI_USDCe`, `USDT_WETH`, `USDT_WBTC`, `USDCe_WBTC`, `USDCe_USDT`, `DAI_USDT`, `DAI_WBTC`.

The script verifies on-chain token decimals, nonempty bytecode, pool token0/token1 identities, Uniswap pool fee tiers, Polygon chain ID, historical block hashes, recognized Swap event signatures and log ordering, and Vault liquidity. It picks at most two active historical blocks from one bounded 400-block window. It reports **swap log counts for selected blocks only**, not 24-hour volume or market depth; V3 `liquidity()` is not comparable TVL. A quote for 1/5 stablecoin units is a price sample, **not** proof of deep executable liquidity.

Read-only routes compare `quick` ↔ `algebra` ↔ `uniswap_500` and optionally `uniswap_3000`, both directions. All two-leg quotes are sequential independent `eth_call`s at the same end-of-block state. They are **not** atomic on-chain flash-loan simulations and not intra-block replay. The gas budget is an operator-supplied stress scenario using a one-wrapped-native QuickSwap V2 conversion proxy, **not real measured gas nor reliable price discovery**. Missing historical gas FX produces `UNASSESSED_GAS_FX`, not a positive verdict. A `MODEL_POSITIVE_REPLAY_REQUIRED` quote may be worth investigation but must never authorize execution.

New token addresses:
- Polygon PoS USDT: `0xc2132d05d31c914a87c6611c10748aeb04b58e8f` (6 decimals)
- Polygon PoS WBTC: `0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6` (8 decimals)

Deployment references:
- QuickSwap contracts: https://docs.quickswap.exchange/overview/contracts-and-addresses
- Uniswap V3 Polygon contracts: https://developers.uniswap.org/docs/protocols/v3/deployments/v3-polygon-deployments
- WBTC PolygonScan contract: https://polygonscan.com/address/0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6

**Allowlisting these assets is for discovery only:** asset transfer behavior, versions, liquidity, flash-borrowability and executor suitability are not approved for live operations.

## VPS setup

Do **not** share your Alchemy key. In a new SSH session, configure the read-only RPC **privately**:

```bash
unset POLYGON_RPC_URL
IFS= read -r -s -p "Polygon HTTPS read-RPC URL: " POLYGON_RPC_URL; echo
POLYGON_RPC_URL="${POLYGON_RPC_URL%$'\r'}"
export POLYGON_RPC_URL
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"  # must return 137
```

Test the package:

```bash
cd /root/polygon_step11_6/step11_6_polygon_active_liquidity
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -q
cd rust && cargo test && cargo build --release && cd ..
```

**First scan (up to 12 quotes on one active pair per selected block):**

```bash
python3 scripts/step11_6_discover.py \
  --from-block 96179458 --to-block 96179857 \
  --pairs USDT_WETH --tiers 500 --sizes 1 5 --max-blocks 1 \
  --output outputs/step11_6_usdt_weth.json
python3 scripts/step11_6_assess.py outputs/step11_6_usdt_weth.json
```

If that succeeds, **second scan** with additional pair (up to 48 route-size attempts if both pairs are active at two blocks):

```bash
python3 scripts/step11_6_discover.py \
  --from-block 96179458 --to-block 96179857 \
  --pairs USDT_WETH USDCe_WBTC \
  --tiers 500 --sizes 1 5 --max-blocks 2 \
  --output outputs/step11_6_two_pairs.json
python3 scripts/step11_6_assess.py outputs/step11_6_two_pairs.json
```

The second scan overlaps the first, so **do not aggregate these two files** as independent evidence. `step11_6_assess.py` intentionally rejects overlapping intervals, even when selected blocks differ.

For economically conservative defaults: `--gas-units 750000 --gas-gwei 100 --gas-buffer-bps 12500 --min-net-units 1 --min-net-bps 20`. These are assumptions, not measured costs. Start small to avoid provider rate limits. If historical pools are missing, absence is reported rather than fabricating data.

## Viability decision (not investment advice)

`step11_6_assess.py` independently checks schema, event/quote counts, pool activity matching, decimal arithmetic, flash fees, historical-block ordering, configured gas cost and model verdicts. It also accepts multiple reports from **distinct, non-overlapping** historical windows:

```bash
python3 scripts/step11_6_assess.py outputs/window_*.json \
  --required-windows 20 --required-pairs 3
```

- `INSUFFICIENT_COVERAGE_FOR_GO_NO_GO`: fewer than 20 adequately covered windows (at least one selected Swap block and one quoted route each).
- `INSUFFICIENT_PAIR_DIVERSITY_FOR_GO_NO_GO`: 20+ adequate windows but fewer than three **different evaluated pairs**.
- `PAUSE_STATIC_TWO_VENUE_STRATEGY_NO_EDGE_IN_STUDIED_WINDOWS`: 20+ adequate non-overlapping windows and 3+ evaluated pairs, but no model-positive quote. This is a research recommendation to pause the **studied route families**, not proof all arbitrage is impossible.
- `RESEARCH_CANDIDATE_REQUIRES_FULL_FORK_REPLAY`: at least one model-positive quote (not verified net profitability).

Offline integrity does not independently confirm the RPC provider really supplied the included data. It does not establish intra-block pricing, fees paid, price impact after first actual swap, executable atomicity, transaction inclusion or mainnet profits.

## Resource policy / inherited code

- Per scan: up to 400 blocks, 3 requested pairs, 2 Uniswap tiers, 3 whole-unit sizes, 2 selected blocks, and **144 maximum two-leg quote attempts**.
- One RPC provider is used for an entire research run; an optional fallback restarts the **whole** failed scan rather than combining inconsistent providers. Avoid placing API keys in shell history.
- The bundled legacy Rust package previously passed 87 tests on the user's VPS. This release does **not** modify it; Rust compilation must be confirmed after upload.
- The bundled Solidity executor is V2-only; it is not safe or authorized for V3 transactions.
- Known Solidity static-analysis findings (external-call reentrancy, strict equality, timestamps) still need separate security review.

**Research go/no-go ≠ production authorization. NO SIGNING / NO BROADCAST / NO RELAY SUBMISSION.**
