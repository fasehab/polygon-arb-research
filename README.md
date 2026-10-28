# Polygon Atomic Arbitrage — Open Research Framework

**Status: research / read-only. No verified repeatable net-profitable strategy, no reviewed live executor, no authorized signing or broadcasting.**

A GitHub-ready staging repository for an open Polygon DEX arbitrage project. The intention is that contributors can bring their own **RPC/node access, data providers, private orderflow integrations, transaction simulators, key custody, and competitive execution infrastructure**. No secrets, private mempool access, block ordering guarantees, or profitability claims are included.

## What is actually present in THIS release kit

| Component | Location | Actual state |
|---|---|---|
| 30-day historical cycle SQL | `research/discovery/01_polygon_closed_cycles.sql` | Supplied query, **not run against Dune here** |
| Historical route ranker | `research/discovery/02_rank_routes.py` | Runnable Python (stdlib) |
| Read-only adapter boundaries | `adapters/` | Interfaces and fail-closed example, **no working live execution** |
| Rust bot source | `runtime/rust/` | **Import from owner's VPS**; not included in this kit |
| Balancer Solidity contract | `contracts/balancer/` | **Import from owner's VPS**; not included in this kit |
| Historical replay/research source | `research/historical/` | **Import from owner's VPS**; not included in this kit |
| Public release guard | `tools/release_guard.py` | Heuristic fail-closed secret/file scanner; human review still mandatory |
| VPS source importer | `tools/import_vps_sources.py` | Copies allowlisted source files from known VPS locations; does not push to GitHub |

The latest historical diagnostic reported `UNPROVEN / POOL_TOKEN_IDENTITY_MISMATCH`. Pool state matching passed; an atomic Polygon net-profitable execution is **not** proven. Earlier project milestones were tested in isolation, but those logs do not establish production readiness.

## Run the research tool (no RPC or wallet required)

1. Open https://dune.com/queries/new and run `research/discovery/01_polygon_closed_cycles.sql` (query cost/account requirements may apply).
2. Export CSV and run:

```bash
python3 research/discovery/02_rank_routes.py polygon_cycles.csv --out outputs/polygon_30d_rankings.json
```

The output is preliminary candidate ranking, **not reconciled transaction-level wallet P&L**.

## Recover the real bot source from the original VPS

The owner must execute these steps **on the VPS holding `/root/polygon_sniper` etc.** (or provide the actual source archives). The importer does not connect to a VPS remotely.

```bash
python3 tools/import_vps_sources.py
# Review selected files before copying. Then:
python3 tools/import_vps_sources.py --apply
python3 tools/release_guard.py
python3 -m unittest discover -s tests -v
```

See `docs/PUBLISH.md` and `docs/SOURCE_INVENTORY.md`. **If a source is missing, this kit does not invent it.** Importing files does not demonstrate that the combined project compiles, nor that Solidity was audited.

## Operator integration model

Provide custom implementations of read-only state and quote providers, exact-state simulator, and *separately reviewed* order-submission backend using the interfaces documented in `adapters/`. Never insert credentials directly in this source tree. Use host environment/secret manager. The adapter has no default broadcaster or signer.

## Important limitations

- Historical spread ≠ attainable execution ≠ settled P&L. Historical state, pool fees, gas, flash-loan obligations, transaction ordering, slippage and capture competition must be reconciled.
- Private RPC/private orderflow access is neither bundled nor guaranteed by a public GitHub repository.
- `research/discovery/` detects simple two-/three-leg closed loops only; indexing omissions, inventory, and unmatched transfers can create false positives. The SQL's `LIMIT 10000` makes it a candidate export rather than a census.
- The source-import scanner is a safety aid, not proof files contain no secrets. **Manually inspect everything before public release; rotate any exposed credentials.**
- Project is supplied as-is, for research. No trade execution should be attempted solely on its results.

## Roadmap, ranked by value (not artificial step counts)

1. **Market viability:** find recurring economically interesting Polygon routes from real transactions and verify transfer-level P&L.
2. **Actual source publication:** import tested Rust/Solidity/replay code from original VPS, verify module-level builds and licenses.
3. **Repeatable execution proof:** reconstruct independent fork transactions (without synthetic donated liquidity), full flash-loan repayment, correct fees and gas.
4. **Operator-supplied competitive edge:** benchmark signals, private route access and inclusion, then independently audit the live path before enabling controlled execution.

See `CONTRIBUTING.md`, `SECURITY.md`, and `docs/ARCHITECTURE.md`.

## License

MIT for the authored release kit. **Owner must check rights and licenses of any subsequently imported modules before sharing them publicly.**
