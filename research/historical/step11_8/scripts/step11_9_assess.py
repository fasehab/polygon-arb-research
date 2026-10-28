#!/usr/bin/env python3
"""Offline consistency validation of Step 11.9 JSON, not independent RPC proof."""
import json
import sys
from pathlib import Path
from step11_9_block_diff_probe import assess,Halt

def main():
    path=Path(sys.argv[1] if len(sys.argv)>1 else 'outputs/step11_9_block_diff.json')
    try:
        doc=json.loads(path.read_text(encoding='utf-8'))
        assess(doc)
    except (Halt,OSError,ValueError) as exc:
        print('STEP 11.9 REPORT CHECK FAILED:',str(exc),file=sys.stderr)
        return 2
    print('STEP 11.9 REPORT CONSISTENCY — PASS (NOT RPC PROOF)')
    print('verdict='+doc['verdict'])
    print('rpc_calls='+str(doc['rpc_calls']))
    print('NO V3 QUOTES / NO GAS OR PROFIT PROOF / NO EXECUTION')
    return 0
if __name__=='__main__':sys.exit(main())
