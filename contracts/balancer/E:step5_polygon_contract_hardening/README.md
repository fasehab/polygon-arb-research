# Step 5 — Balancer V2 Flash-Arbitrage Contract Hardening

**Status: source implementation and mock tests provided; UNCOMPILED / UNTESTED against Solidity toolchain in the creator environment, NOT AUDITED, NOT DEPLOYED.**

This folder intentionally **does not replace the working Step 4 Rust bot**. The Rust monitor stays read-only. Run tests locally before integrating any execution capability. Do NOT fund or deploy to Polygon with real assets at this stage.

## What this changes relative to original `sol.txt` / Step 1 contract

1. **Authenticated flash-loan callback:** require actual configured Vault caller, one live request, exact expected callback payload hash, exactly one matching token and amount, and one-time completion; reject unsolicited/replayed callbacks.
2. **Existing balances isolated:** record tokenIn balance before borrowing; validate exact loan receipt, and require `after two swaps >= startingBalance + principal + actualFee + minProfit`. Check again after full repayment. Preexisting tokenIn deposits cannot paper over an unprofitable new arbitrage.
3. **Actual fee repayment:** use `feeAmounts[0]` returned by the Vault, require `feeAmounts[0] <= maxFlashFee`, and repay `principal + actualFee`. Do not assume Balancer fees are zero.
4. **Per-leg slippage:** caller provides `minOutA`, `minOutB` and `deadline`, and contract checks actual token balance deltas alongside V2 router outputs.
5. **Token and approval safety:** owner must explicitly allowlist tokens; reject non-contract token addresses; use zero-first, checked ERC20 approvals with zero remaining router allowance; check repayment and withdrawal transfers. Reject atypical fee-on-transfer behaviour.
6. **Both market directions:** allow only QuickSwap V2 -> SushiSwap V2 OR SushiSwap V2 -> QuickSwap V2, never identical routers. Polygon (chainId 137) constructor pins to the verified Balancer/QuickSwap/SushiSwap addresses. Local mocks are injectable for tests.
7. **Reentrancy and operator controls:** private state machine, `onlyOwner`, optional pause and owner-only withdraw while idle; emits parameter and result events.

The self-contained `SafeTokenLite` is deliberately small and **not a substitute for pinned, audited OpenZeppelin SafeERC20** during the production security review. OpenZeppelin documents zero-first `forceApprove` for tokens requiring allowance reset.

## WARNING: ABI breaking change

Older entry point:

```solidity
executeFlashArbitrage(address tokenIn, address tokenOut, uint256 flashAmount,
                      address routerA, address routerB, uint256 minProfit)
```

New entry point (a single Solidity tuple):

```solidity
struct TradeParams {
    address tokenIn;
    address tokenOut;
    uint256 flashAmount;
    address routerA;
    address routerB;
    uint256 minOutA;
    uint256 minOutB;
    uint256 minProfit;
    uint256 maxFlashFee;
    uint256 deadline;
}
function executeFlashArbitrage(TradeParams calldata p) external onlyOwner;
```

**Do not reuse the old Rust executor ABI** from Step 1. The Step 4 Rust implementation is read-only and unaffected. A future trading adapter must encode this exact tuple and derive minimum outputs/loan cap from independent, timely checks. Current Step 4 optimizer is NOT an execution authorization system.

## Project contents

- `src/BalancerMEVSniper.sol` — hardened experimental contract
- `test/BalancerMEVSniper.t.sol` — self-contained mocked Balancer Vault, V2 routers and ERC-20s, plus 17 `test*` cases
- `foundry.toml` — minimal Foundry test configuration
- `legacy/Step1_DO_NOT_DEPLOY.sol` — previous source retained for review only
- `scripts/check_structure.py` — lightweight consistency check (NOT compilation or a security audit)

## Test on your Ubuntu VPS

### 1. Keep Rust as-is

Do NOT overwrite `/root/polygon_sniper/src/main.rs`, Cargo.toml or its read-only binary. Upload `step5_polygon_contract_hardening.zip` to your home directory and extract into **a separate Solidity folder**:

```bash
mkdir -p /root/polygon_step5_contract
unzip -o /root/step5_polygon_contract_hardening.zip -d /root/polygon_step5_contract
cd /root/polygon_step5_contract/step5_polygon_contract_hardening
python3 scripts/check_structure.py
```

If the ZIP lives in another directory, adjust only its path. Inspect the ZIP contents before extracting.

### 2. Install or check Foundry

```bash
forge --version
```

If this says `command not found`, use the official installation instructions at https://getfoundry.sh/getting-started/installation rather than running unknown installers. A recent `forge` is needed. Foundry will need network access to obtain the pinned Solidity compiler if it is not cached.

### 3. Compile and run local tests

```bash
forge build
forge test -vvv
```

The tests deploy mock Vault, router and ERC-20 contracts on a local dev chain. They do **not** prove that live Polygon protocols behave identically. Test both expected successes and expected reverts, including old-balance preservation and fee cap rejection.

### 4. Capture a concise test report

```bash
forge test --summary
forge test --gas-report
```

Do not mistake local gas estimates for a mainnet execution receipt. Record compilation errors verbatim; do not guess at edits if a compiler failure arises.

## Specific local test scenarios

- Profitable trade and full principal repayment
- Nonzero flash fee: repayment increases and realized profit decreases
- Fee above caller's maximum: revert
- Losing trade with 500 token units already in the contract: revert without dipping into old balance
- First-leg and second-leg output-floor failures: revert
- Unsupported route, unapproved token or expired deadline: revert
- Non-owner initiation or forged callback: revert
- Vault callback payload tampering and missing callback: revert
- Router/token reports more output than balance actually received: revert
- Pause/unpause behaviour; reverse Sushi -> Quick routing; owner withdrawal

## Remaining critical limitations

1. **No Polygon-fork simulation** yet (needs test-specific harness and a real archive/history capable Polygon RPC).
2. **No audited token policy**. Token allowlisting is a *control*, not a guarantee that the allowed token is safe or tax-free.
3. **No actual P&L / gas accounting**. `realizedProfit` is token-denominated new profit **before gas**. Transaction failures can still incur gas.
4. **No transaction inclusion ordering guarantee**. No claim is made about target pending swap succeeding, sequencing, relays or atomic bundles.
5. **No exact router/pool quote verification**. Rust Step 4's 0.3% fee assumptions and bounded search still need validation per venue and candidate pair.
6. **No externally assessed security review**. This is a development prototype, not production custody-grade code.
7. **No direct withdrawal of native POL** because the contract handles ERC20 token balances only; gas must be paid by the external transaction sender.
8. **Dependency configuration**: chainId 137 pins addresses, but local/dev chains allow injected mocks. Do not deploy production on arbitrary networks using these flags.
9. **Deployment is a separate step**: deploying a new source contract does not upgrade the previously deployed one automatically, and changing its ABI breaks old execution callers.
10. **Mock tests may need fixing after actual compile**; no `solc`/`forge` compiler was available in the source-creation environment, so no Solidity test pass is claimed.

## Additional acceptance gates before funding or broadcast

- `forge build` and all `forge test` cases pass reproducibly on the VPS.
- Run relevant static analyzers (Slither if available), coverage/fuzz/property tests, and independent review.
- Add Polygon mainnet-fork integration with real Balancer Vault, real token approvals and real V2 routers.
- Verify Balancer Vault flash-loan liquidity and actual fee return on intended network at execution time.
- Implement Step 6-9 sender nonce control, simulation, execution ordering and receipt-based net P&L.
- Require explicit owner allowlist, pause state review and test of safe withdrawals.

## Official references

- Balancer Vault interface: https://github.com/balancer/balancer-v2-monorepo/blob/master/pkg/interfaces/contracts/vault/IVault.sol
- Balancer flash-loan implementation: https://github.com/balancer/balancer-v2-monorepo/blob/master/pkg/vault/contracts/FlashLoans.sol
- OpenZeppelin SafeERC20 docs: https://docs.openzeppelin.com/contracts/5.x/api/token/erc20
- QuickSwap V2 deployment info: https://docs.quickswap.exchange/overview/contracts-and-addresses
- Foundry setup: https://getfoundry.sh/getting-started/installation
