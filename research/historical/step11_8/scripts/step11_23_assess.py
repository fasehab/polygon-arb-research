#!/usr/bin/env python3
"""Offline report consistency validation. Not independent chain verification."""
import json
import sys
from pathlib import Path
from step11_23_usdce_wbtc_event_spread import assess, Halt


def main():
    src = sys.argv[1] if len(sys.argv)==2 else 'outputs/step11_23_usdce_wbtc_event_spread.json'
    try:
        report=json.loads(Path(src).read_text())
        assess(report)
        print('STEP 11.23 REPORT CONSISTENCY — PASS (NOT INDEPENDENT RPC PROOF)')
        print('verdict:',report['verdict'])
        print('max_heuristic_spread_bps:',report['max_event_heuristic_spread_bps'])
        print('rpc_calls:',report['rpc_calls'],'block_traces:',report['block_traces'])
        print('NO FULL TICK REPLAY / NO GAS-ADJUSTED PROFIT / NO SIGNING OR BROADCAST')
        return 0
    except (Halt, KeyError, ValueError, OSError, TypeError) as e:
        print('ASSESSMENT FAILED:', getattr(e,'reason',type(e).__name__), file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
