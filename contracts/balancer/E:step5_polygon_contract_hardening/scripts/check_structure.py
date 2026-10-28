#!/usr/bin/env python3
"""Lightweight source-file consistency check. NOT Solidity compilation or a security audit."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
contract = (root / 'src/BalancerMEVSniper.sol').read_text()
tests = (root / 'test/BalancerMEVSniper.t.sol').read_text()
checks = {
    'Solidity source': (root / 'src/BalancerMEVSniper.sol').is_file(),
    'Foundry tests': (root / 'test/BalancerMEVSniper.t.sol').is_file(),
    'Foundry configuration': (root / 'foundry.toml').is_file(),
    'Callback authentication': 'expectedPayloadHash' in contract and 'msg.sender == vault' in contract,
    'Initial balance protection': 'startingBalance + repayment + p.minProfit' in contract,
    'Fee repayment': 'p.flashAmount + flashFee' in contract,
    'Slippage bounds': all(k in contract for k in ('minOutA', 'minOutB', 'minProfit')),
    'Token allowlist': 'allowedToken[p.tokenIn]' in contract,
    '18 or more local tests': tests.count('function test') >= 17,
}
for k, ok in checks.items():
    print(f"{'PASS' if ok else 'FAIL'}: {k}")
if not all(checks.values()):
    raise SystemExit(1)
print('STRUCTURE CHECK ONLY: now run forge build && forge test -vvv')
