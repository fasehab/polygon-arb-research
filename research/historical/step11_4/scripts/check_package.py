#!/usr/bin/env python3
"""Step 11 offline file and policy checks. Not a Rust/Solidity compiler or RPC test."""
import hashlib
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parent.parent
manifest=ROOT/'SHA256SUMS.txt'
assert manifest.is_file(), 'missing SHA256 manifest'
records=manifest.read_text().splitlines()
expected=set()
for line in records:
    digest,name=line.split('  ',1)
    assert name not in expected and not name.startswith('/') and '..' not in name
    expected.add(name)
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest, f'SHA mismatch: {name}'
actual={p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*') if p.is_file()
        and '__pycache__' not in p.parts and p.name!='SHA256SUMS.txt'
        and not p.name.endswith('.pyc')}
assert actual==expected, f'missing/extra package sources: {actual ^ expected}'
checks={
 'rust/src/main.rs':['mod step11;', 'mod step11_2;', '--step11-2-report','--step11-report','--step10-report','--step9-report'],
 'rust/src/step11.rs':['UNASSESSED_NEEDS_UNMODIFIED_FORK_GAS_AND_INCLUSION','fn assess(','NO PRIVATE KEY'],
 'rust/src/step10.rs':['REJECT_GROSS_UNPROFITABLE'],
 'rust/src/step11_2.rs':['fn assess(','executor_supports_v3','NO SIGNING'],
 'scripts/step11_2_v3_collect.py':['eth_chainId','algebra_then_uniswap','uniswap_then_algebra'],
 'rust/src/token_policy.rs':['ObservedFeeOnTransfer'],
 'scripts/step11_collect.py':['historical_v2_multi_pair_quotes_no_execution','POLYGON_RPC_FALLBACK_URL', 'get_hash', 'eth_getBlockByNumber'],
 'scripts/test_step11_collect.py':['Step11CollectorTests','test_provider_fallback_restarts_entire_block'],
 'solidity/src/BalancerMEVSniper.sol':['contract BalancerMEVSniper'],
 'README.md':['STEP 11.3','read-only'],
 'rust/src/step11_3.rs':['fn assess(','executor_supports_mixed_routes','NO PRIVATE KEY'],
 'scripts/step11_3_event_mixed_collect.py':['eth_getLogs','SKIP_NO_POOL_ACTIVITY','ALWAYS BLOCK'],
 'scripts/step11_4_swap_collect.py':['select_swap_blocks','transaction_indices_verified','ALWAYS BLOCK'],
 'rust/src/step11_4.rs':['print_report','individual_transaction_state_reconstructed','NO PRIVATE KEY'],
 'scripts/test_step11_4_swap_collect.py':['SwapTests','test_rank_cross_venue_first'],
}
for name,markers in checks.items():
    data=(ROOT/name).read_text()
    assert all(x in data for x in markers),f'Missing marker {name}'
assert len(re.findall(r'#\[test\]\s*fn\s+', (ROOT/'rust/src/step11.rs').read_text()))==10
assert len(re.findall(r'#\[test\]\s*fn\s+', (ROOT/'rust/src/step11_2.rs').read_text()))==10
assert len(re.findall(r'#\[test\]\s*fn\s+', (ROOT/'rust/src/step11_3.rs').read_text()))==10
assert len(re.findall(r'#\[test\]\s*fn\s+', (ROOT/'rust/src/step11_4.rs').read_text()))==6
# Step 11 additions may NEVER contain signing, broadcasting or privileged state mutators.
new_sources='\n'.join((ROOT/f).read_text() for f in ['scripts/step11_collect.py','rust/src/step11.rs','scripts/step11_2_v3_collect.py','rust/src/step11_2.rs','scripts/step11_3_event_mixed_collect.py','rust/src/step11_3.rs',
 'scripts/step11_4_swap_collect.py','rust/src/step11_4.rs'])
for forbidden in ['eth_sendRawTransaction','eth_sendTransaction','eth_sendBundle','vm.prank','vm.store','Wallet<','SignerMiddleware']:
    assert forbidden not in new_sources,f'Prohibited Step 11 source behavior: {forbidden}'
print(f'PASS Step 11.4 package integrity: {len(expected)} hashed files, 36 Step 11+11.2+11.3+11.4 Rust tests declared, no broadcaster')
print('DISCLAIMER: Source integrity != compiled Rust or verified RPC evidence')
