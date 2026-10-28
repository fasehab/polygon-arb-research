# Step 11.4 test matrix

| Coverage | Test |
|---|---|
| V2 and V3 Swap topic separation | Rejects topic/protocol mismatch |
| Event authenticity checks | Enforces known pool addresses, canonical block hash, tx/log indices and event data length |
| Transaction order | Stable sort and reject duplicate log identity or inconsistent tx index |
| Research limits | 400 blocks, at most two blocks selected, vetted pools/sizes/tiers |
| Historical quote limitations | End-of-block only; `individual_transaction_state_reconstructed = false` |
| RPC | Read-only method allowlist; fallback restarts all snapshot collection |
| Rust | Previous Step11.3 full quote checks plus 6 event metadata unit tests |
| No live risk | No signer, private key, nonce, deploy, relay, or broadcasting in new components |

**Validation statement:** 74 offline Python tests passed in the packaging runtime. Rust/Solidity were not compiled in this runtime. The 87 Rust test count is an expectation, not observed evidence. Mainnet RPC correctness remains to be tested by the operator on their own Polygon HTTPS RPC.
