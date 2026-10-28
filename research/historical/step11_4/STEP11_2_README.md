# STEP 11.2 — Polygon V3 historical read-only discovery

## Why not simply replace the V2 routers?

QuickSwap V3 on Polygon PoS uses **Algebra** concentrated liquidity, with a single pool per token pair and adaptive fee. **Uniswap V3** uses fee-tier pools, distinct `getPool(tokenA,tokenB,fee)` lookups and a different QuoterV2 ABI. Neither protocol can be modeled accurately with V2 `x*y=k` router quotes. Existing BalancerMEVSniper.sol **ONLY SUPPORTS V2** and is intentionally unchanged.

This incremental package preserves all Step 11 V2 code, Rust tests, Step 10/11 reports and Solidity code. It adds isolated **read-only** historical V3 pool discovery and quoter experiments for these venues:

- **QuickSwap Algebra V3** factory `0x411b0fAcc3489691f28ad58c47006AF5E3Ab3A28`, Quoter `0xa15F0D7377B2A0C0c10db057f641beD21028FC89`.
- **Uniswap V3 Polygon** factory `0x1F98431c8aD98523631AE4a59f267346ea31F984`, QuoterV2 `0x61fFE014bA17989E743c5F6cB21bF9697530B21e`.

Sources (verify current support before later execution):
- https://docs.quickswap.exchange/overview/contracts-and-addresses
- https://docs.quickswap.exchange/technical-reference/guides/smart-contract-integration/v3-pool-addresses
- https://developers.uniswap.org/docs/protocols/v3/deployments/v3-polygon-deployments
- https://github.com/Uniswap/v3-periphery/blob/main/contracts/interfaces/IQuoterV2.sol
- https://docs.algebra.finance/algebra-integral-documentation/algebra-v1-technical-reference/contracts/api-reference-v1.0/v1.0-periphery/iquoter

These are **research-only assets**, not approved trading tokens: DAI/WETH, bridged USDC.e/WETH, DAI/USDC.e, borrowing only the DAI or USDC.e side. Try historical blocks 96179857 and 96174857, trade sizes 1/5, and Uniswap fee tiers 500 (0.05%) and 3000 (0.30%). We do not assert there is active liquidity at those historical blocks; missing pools/reverts become nontradable skips, not fabricated quotes. All steps work with a standard **HTTPS Polygon archive-capable RPC** on an 8 GB VPS, no dedicated node.

## VPS validation

```bash
cd /root/polygon_step11_2/step11_2_polygon_v3_discovery
python3 scripts/check_package.py
python3 -m unittest discover -s scripts -p 'test_*.py' -v
cd rust
cargo test
cargo build --release
cd ..
```

Expect **71 Rust unit tests declared**: the prior 61 plus ten new Step 11.2 tests (requires VPS compilation; not locally compiled when package was authored). Python includes the original 23 tests plus 16 new V3 tests = **39 offline tests**, run successfully during packaging. The old Solidity tests remain included and unmodified; they validate V2 only.

## HTTPS RPC configuration

Each SSH session must have `POLYGON_RPC_URL` set privately. Do not paste your key into chat, scripts or reports.

```bash
read -rsp 'Paste Polygon HTTPS read RPC URL: ' POLYGON_RPC_URL; echo
export POLYGON_RPC_URL
/root/.foundry/bin/cast chain-id --rpc-url "$POLYGON_RPC_URL"  # expect 137
```

Optional fallback: set `POLYGON_RPC_FALLBACK_URL` to a second HTTPS archive-capable endpoint. If a provider fails, a complete block is retried on fallback; calls from two providers are never interleaved within one block.

## Initial V3 experiment

```bash
cd /root/polygon_step11_2/step11_2_polygon_v3_discovery
python3 scripts/step11_2_v3_collect.py \
  --blocks 96179857 96174857 \
  --sizes 1 5 \
  --tiers 500 3000 \
  --output outputs/step11_2_v3_quotes.json
./rust/target/release/polygon_sniper \
  --step11-2-report outputs/step11_2_v3_quotes.json
```

The output distinguishes `REJECT_GROSS_UNPROFITABLE`, `REJECT_VAULT_LIQUIDITY`, `SKIP_MISSING_POOL`, `SKIP_ZERO_ACTIVE_LIQUIDITY`, `SKIP_QUOTER_REVERT`, and `gross_positive_unassessed`. The QuoterV2 `gasEstimate` is a **quoter-internal hint**, not the actual 2-leg flash-loan transaction gas. The Algebra quoter may return a dynamically calculated pool fee, which must not be replaced with a fixed V2 fee.

## Non-negotiable safety and limitations

- **No keys, wallet, signer, private relay, sendRawTransaction, state override, fork mutations, approvals or broadcasting in V3 collector.** RPC allowlist is `eth_chainId`, `eth_blockNumber`, `eth_getBlockByNumber`, `eth_getCode`, `eth_call`.
- Historical pool addresses, ERC20 decimals, pool token order, active pool liquidity, fee tiers, and loan availability are queried and checked at each exact block; pinned hashes checked before/after. Missing data is a rejection.
- This is *not* an event-driven scanner, tick-replay engine, real-time mempool monitor, executable V3 flash-loan route or profitable-trade guarantee. Those require further independent stages.
- A gross-positive quote is **unassessed**, not a signal to trade. Must implement V3-specific executor, use unmodified-state fork simulation, verify token behavior, flash-loan repayment, actual gas and inclusion risk, and resolve Solidity security warnings **before** any pilot.
- The V2 executor included for regression cannot execute any quoted V3 route; it is unmodified and should not be deployed for V3.
- This JSON is an offline RPC-derived record and not authenticated on-chain evidence.
