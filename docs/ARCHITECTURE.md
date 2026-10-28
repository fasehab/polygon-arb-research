# Architecture and threat boundaries

```
DEX event history / RPC node       Dune export / historical swaps
             |                              |
          Decoders                    Discovery ranker
             |                              |
        Pool snapshots / quotes  <----- Candidate routing
             |                              |
        Optimizer / cost filters / exact-state verifier
                             |
             Fail-closed decision + reproducible evidence
                             |
                  [No live execution path]
```

The owner’s historical implementation includes Rust ethers-rs/Tokio observer and optimizer, Python RPC diagnostics and a separately tested Solidity Balancer flash-loan executor. Those actual source trees **must be imported from the VPS**; this kit only supplies the verified available discovery artifact and integration boundaries.

**Operator integration points** (no preset credentials):

- `StateProvider`: fetches exact-block pool state, token metadata, liquidity and block identity.
- `QuoteProvider`: produces exact-input venue quotes including fees and state identifiers.
- `ExecutionSimulator`: full transaction-level replay against immutable snapshot with gas and obligation accounting.
- `SubmissionBackend`: optional production lane to be implemented and reviewed outside the default read-only repo, with private orderflow and ordering protection where verified.

Reject unknown tokens, stale states, decimal mismatches, fee uncertainty, simulation discrepancy, insufficient repayment, unverified inclusion model, or negative after-cost P&L. No public code can grant transaction priority or predictable profit merely by connecting to a better RPC.
