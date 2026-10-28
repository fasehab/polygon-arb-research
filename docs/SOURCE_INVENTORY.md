# What belongs to the original VPS (not bundled)

These paths come from prior user-supplied project commands/reports. Confirm their actual contents before importing:

- `/root/polygon_sniper` — original Rust `polygon_sniper` crate, read-only observer, optimizer, tests.
- `/root/polygon_step5_contract` — Foundry workspace with hardened local Balancer flash-loan executor tests (not independently audited).
- `/root/polygon_step11_8/step11_8_polygon_tx_boundary_probe` — latest known research/transaction-boundary source directory (includes Step 11.25 if saved there).
- `/root/polygon_step11_4/step11_4_polygon_swap_viability` — earlier historic-swap viability snapshot (optional, avoid confusing it with latest).

Script imports source and test files only, not ZIP archives, state logs, generated outputs or credential-bearing configuration. Use `--source PATH:DEST_SUBDIR` for any different actual location. This kit never retrieves source from a VPS by itself. If unavailable, public repo should clearly remain marked *PARTIAL SOURCE / RESEARCH ONLY*.

Recommended final layout after import:

```text
runtime/rust/                    # original Rust observer/optimizers
contracts/balancer/             # Foundry Solidity workspace
research/historical/step11_8/   # latest historical fork research
research/historical/step11_4/   # earlier reproducibility checkpoint
research/discovery/             # Dune SQL and Python route ranking
adapters/                       # integration types and safe defaults
```
