# Polygon Arbitrage Discovery Sprint 1 — bounded, read-only

## Aim
Discover historical **completed 2- or 3-swap cycles** on Polygon in the 30 complete UTC days
**2026-09-28 through 2026-10-27**. Rank by recurring pool routes and approximate after-gas USD.
Do **not** confuse closed-swap loops, indicative gross surplus or historical profits with
capturable, net-profitable future opportunities.

## Quick start (no VPS changes, no private keys)
1. Visit https://dune.com/queries/new and paste `01_polygon_closed_cycles.sql` into its SQL editor.
2. Execute and export resulting table as a CSV file. Dune may require an account, credits, or
   a smaller date window due to query cost. **The SQL has not been run against Dune here.**
3. Copy the downloaded CSV and `02_rank_routes.py` to your VPS (or run locally).
4. Run:

       python3 02_rank_routes.py polygon_cycles.csv --out outputs/polygon_30d_route_rankings.json

5. Inspect rank #1 and its 3 example tx hashes, but do not deploy. Verify real transfers,
   final repayment, flash-loan fees, pool addresses/code, token risk, and whether an independent
   competitor could have captured the same state.

## What this DOES and DOES NOT cover
- Includes **Uniswap-to-Uniswap between distinct pools**, cross-DEX, 2-hop and 3-hop routes.
- Requires all of a tx's DEX swaps to form one ordered 2/3-swap closed loop and rejects missing token amounts.
- Ignores paths >3 legs, mixed executions with extra swaps, unindexed protocol swaps, CEX-related
  legs, and cases that require netting several disjoint paths. Thus **not a full market census**.
- Pool addresses are event-emitting contracts from curated DEX data; verify that they are real
  swap pools and not routers. Dune's project naming may group distinct venues in surprising ways.
- `approximate_gross_usd`: difference between ending and starting token amounts, marked
  at the FIRST swap's implied USD rate. It is an estimate, not wallet-level PnL. Liquidity-pool
  swap fees are generally included in realized swap amounts, but transfers, refunds, rebates,
  flash-loan charges, other transfers, external funding, bot bribes and native token flows
  are NOT reconciled. Gas USD is reported by `gas.fees` where available.
- Extremely high implied profits (>20% of apparent stake) are **flagged** and excluded from
  route promotion; they can reflect bad prices, inventory or incomplete decoding.
- Ranking by summed after-gas profit can overstate routes dominated by a single anomalous event.
  The JSON includes recurrence by independent UTC days and transaction count.
- The query uses `LIMIT 10000`, so it is a **top-gross-candidate export**, not full frequency data
  if more than 10,000 rows qualify. Any apparent absence of a route is NOT conclusive.

## Decision gate (one bounded review)
- **Potential GO** for one targeted replay: repeatable multi-day candidate route with
  $5+ apparent after-gas profit per cycle, and verified transfer-level net surplus.
- **NO GO** for continued blind V3 implementation if profitable candidates disappear after
  reconciliation or only incumbents can capture them; redirect engineering effort instead.
- **Never send a live transaction or use funds** based solely on this discovery scan.
