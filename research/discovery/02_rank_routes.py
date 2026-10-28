#!/usr/bin/env python3
"""Rank 30-day Polygon closed-loop arbitrage candidates from Dune CSV.

This is hypothesis ranking, NOT wallet-PnL reconciliation and NOT live readiness.
No keys, network access, signing or broadcasting.
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import median


def money(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('csv_file', type=Path, help='CSV exported from Dune query')
    p.add_argument('--out', type=Path, default=Path('outputs/polygon_30d_route_rankings.json'))
    p.add_argument('--min-net', type=float, default=5.0,
                   help='Minimum preliminary after-gas USD per candidate [default: $5]')
    p.add_argument('--min-events', type=int, default=3,
                   help='Events required for recurring route flag [default: 3]')
    args = p.parse_args()

    by_route = defaultdict(list)
    total_rows = 0
    skipped_fee = 0
    skipped_threshold = 0
    marked_outliers = 0
    all_positive = []
    with args.csv_file.open(newline='', encoding='utf-8-sig') as fp:
        reader = csv.DictReader(fp)
        required = {'tx_hash', 'block_time', 'venue_route', 'pool_route',
                    'token_route', 'initial_usd', 'approximate_gross_usd', 'gas_usd'}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f'CSV is missing required columns: {sorted(missing)}')
        for row in reader:
            total_rows += 1
            gross = money(row['approximate_gross_usd'])
            gas = money(row['gas_usd'])
            stake = money(row['initial_usd'])
            if gas is None or gross is None or stake is None or stake <= 0:
                skipped_fee += 1
                continue
            net = gross - gas
            if net < args.min_net:
                skipped_threshold += 1
                continue
            # Large apparent returns could be fabricated prices, inventory reuse,
            # fee-on-transfer tokens or incomplete indexing. Do not quietly trust.
            suspicious = net > stake * 0.20
            if suspicious:
                marked_outliers += 1
            item = {
                'tx_hash': row['tx_hash'],
                'block_time': row['block_time'],
                'block_number': row.get('block_number'),
                'tx_to': row.get('tx_to'),
                'stake_usd': round(stake, 4),
                'approx_gross_usd': round(gross, 4),
                'gas_usd': round(gas, 4),
                'preliminary_after_gas_usd': round(net, 4),
                'suspicious_high_return': suspicious,
                'venue_route': row['venue_route'],
                'pool_route': row['pool_route'],
                'token_route': row['token_route'],
            }
            all_positive.append(item)
            # Do not mix same-venue cycles across different pool addresses.
            key = (row['pool_route'], row['token_route'])
            by_route[key].append(item)

    ranks = []
    for (pool_route, token_route), items in by_route.items():
        safe = [i for i in items if not i['suspicious_high_return']]
        if not safe:
            continue
        profits = [i['preliminary_after_gas_usd'] for i in safe]
        ranks.append({
            'venue_route': safe[0]['venue_route'],
            'token_route': token_route,
            'pool_route': pool_route,
            'distinct_transactions': len({i['tx_hash'] for i in safe}),
            'distinct_utc_days': len({i['block_time'][:10] for i in safe}),
            'total_preliminary_after_gas_usd': round(sum(profits), 4),
            'median_preliminary_after_gas_usd': round(median(profits), 4),
            'largest_preliminary_after_gas_usd': round(max(profits), 4),
            'recurring': len(safe) >= args.min_events
                and len({i['block_time'][:10] for i in safe}) >= 2,
            'top_example_txs': [i['tx_hash'] for i in sorted(
                safe, key=lambda x: x['preliminary_after_gas_usd'], reverse=True)[:3]],
        })
    ranks.sort(key=lambda x: (x['total_preliminary_after_gas_usd'],
                              x['distinct_transactions']), reverse=True)
    all_positive.sort(key=lambda x: x['preliminary_after_gas_usd'], reverse=True)
    report = {
        'status': 'DISCOVERY_CANDIDATES_NOT_EXECUTION_PROOF',
        'coverage_warning': 'CSV capped at SQL LIMIT 10000 and only complete 2/3-leg tx loops',
        'scope': 'Polygon Sep 28 - Oct 27 2026 UTC',
        'min_preliminary_after_gas_usd': args.min_net,
        'input_rows': total_rows,
        'fee_missing_or_invalid': skipped_fee,
        'below_threshold': skipped_threshold,
        'suspicious_high_return_flagged_not_promoted': marked_outliers,
        'qualified_candidates_including_outliers': len(all_positive),
        'ranked_routes_excluding_outliers': ranks[:100],
        'top_candidates': all_positive[:100],
        'decision': 'NO_VALIDATED_EDGE_YET',
        'next_required': 'Independently reconcile wallet transfers/flash-loan obligations, check pool identity and access, replay pre-tx exact state and model capture competitiveness.',
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Scanned CSV rows: {total_rows}')
    print(f'Preliminary >= ${args.min_net:g} after gas: {len(all_positive)}')
    print(f'Rankable routes (outliers excluded): {len(ranks)}')
    print(f'Missing gas/invalid rows: {skipped_fee}; >20% stake profit flagged: {marked_outliers}')
    print(f'Wrote: {args.out}')
    for r in ranks[:10]:
        print(f"  {r['distinct_transactions']:>4} tx / {r['distinct_utc_days']:>2} days "
              f"${r['total_preliminary_after_gas_usd']:>9,.2f} | "
              f"{r['venue_route']} | {r['token_route']}")
    if not ranks:
        print('No rankable routes. This is NOT evidence that Polygon has no arbitrage; inspect coverage/thresholds.')


if __name__ == '__main__':
    main()
