#!/usr/bin/env python3
"""Offline consistency validator for Step 11.14; does not certify chain evidence."""
import json
import sys
from pathlib import Path
from step11_14_ranked_probe import assess, Halt

def main():
    if len(sys.argv)!=2:raise SystemExit('usage: python3 scripts/step11_14_assess.py outputs/step11_14_second_candidate.json')
    try:
        d=json.loads(Path(sys.argv[1]).read_text())
        assess(d)
        print('STEP 11.14 REPORT CONSISTENCY — PASS, NOT INDEPENDENT CHAIN PROOF')
        print('verdict='+d['verdict']+' fee_only_ceiling_bps='+str(d['fee_only_ceiling_bps']))
        print('ALWAYS BLOCK: NOT REAL EXECUTOR PROFIT OR TRADING AUTHORIZATION')
    except (OSError,ValueError,KeyError,TypeError,Halt) as e:
        print('STEP 11.14 REPORT REJECTED:',e.reason if isinstance(e,Halt) else type(e).__name__,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
