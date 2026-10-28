# Security Policy

Do not commit API tokens, node URLs embedding credentials, signing secrets, wallet mnemonics, private orderflow credentials, wallets or operator logs. Use environment variables and a host secret manager. Run `python3 tools/release_guard.py` before each push, then inspect the staged diff. GitHub secret scanning and push protection are additional layers, not substitutes.

**This repository has no production approval.** Before anyone enables a submission backend, require independent review of Solidity and allowance/callback logic, signer isolation, on-chain bytecode/chain ID checks, exact-state replay, receipt-based accounting, hard gas and capital loss caps, nonce policy, kill switch and documented rollback. `adapters/` intentionally fails closed.

If a credential has been committed to Git, immediately revoke/rotate it. Deleting a file in a later commit does not clean past commit history or forks. Report potential vulnerabilities privately to the repository maintainers rather than filing exploits publicly.
