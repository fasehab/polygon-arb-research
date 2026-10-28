#!/usr/bin/env python3
"""Offline Step 11.8 consistency validator. NOT an independent RPC or chain proof."""
import argparse
import json
import sys
from pathlib import Path
from step11_collect import EvidenceError
from step11_4_swap_collect import strict_hex
from step11_8_boundary_probe import compare_event

SAFETY={'read_only':True,'signing':False,'broadcast':False,
        'relay_submission':False,'state_overrides':False,'v3_executor_ready':False}

def evaluate(obj):
    if not isinstance(obj,dict) or obj.get('schema_version')!=1 or \
        obj.get('evidence_kind')!='polygon_tx_boundary_getter_probe_research_only' or \
        obj.get('chain_id')!=137:
        raise EvidenceError('unexpected report schema')
    b=obj.get('block');tr=obj.get('transaction'); pools=obj.get('pools');sd=obj.get('storage_diff');
    x=obj.get('txindex_tracecall'); caps=obj.get('capabilities'); safe=obj.get('safety')
    if not all(isinstance(v,dict) for v in (b,tr,pools,sd,x,caps,safe)):
        raise EvidenceError('report object missing')
    if safe!=SAFETY:raise EvidenceError('unsafe report flag')
    if caps.get('intrablock_quotations_performed') is not False or \
       caps.get('complete_tick_state_reconstructed') is not False or \
       caps.get('real_profit_proven') is not False:
        raise EvidenceError('false profit or replay certification')
    if not all(type(v) is int for v in [b.get('number'),b.get('transaction_count'),tr.get('index')]) or \
       b['number']!=96179661 or b['transaction_count']>100000 or \
       not 0<=tr['index']<b['transaction_count']:
        raise EvidenceError('wrong pinned block/index')
    strict_hex(b.get('hash'),66);strict_hex(tr.get('hash'),66)
    alg=strict_hex(pools.get('algebra'),42)
    uni=pools.get('uniswap_500')
    if uni is not None:
        uni=strict_hex(uni,42)
        if alg==uni:raise EvidenceError('duplicate pool')
    if tr.get('verified_swap_events_for_target_pool') not in (1,2,3,4):
        raise EvidenceError('invalid event count')
    evt=tr.get('final_pool_swap_event')
    if not isinstance(evt,dict) or set(evt)!={'sqrt_price_x96','liquidity_raw','tick'}:
        raise EvidenceError('missing canonical event values')
    check_values(evt)
    if sd.get('status') not in {'POOL_STORAGE_CHANGED','NO_POOL_STORAGE_DELTA'} and \
       not str(sd.get('status','')).startswith('UNAVAILABLE_'):
        raise EvidenceError('invalid storage diff result')
    if sd.get('has_target_pool_delta') is not (sd['status']=='POOL_STORAGE_CHANGED'):
        raise EvidenceError('storage flag mismatch')
    hash_=sd.get('sha256')
    if hash_ is not None:
        if not isinstance(hash_,str) or len(hash_)!=64:
            raise EvidenceError('invalid diff hash')
        try:int(hash_,16)
        except ValueError:raise EvidenceError('invalid diff hash') from None
    if x.get('status') not in {'UNPROVEN','VERIFIED_TWO_POOL_TRANSACTION_BOUNDARY'}:
        raise EvidenceError('unknown boundary status')
    for key in ['event_matches_after','before_differs_from_after','same_boundary_competitor_read']:
        if type(x.get(key)) is not bool:raise EvidenceError('malformed boundary boolean')
    states=[x.get(k) for k in ('before','after','competitor_after')]
    if any(z is not None for z in states):
        if not all(isinstance(z,dict) for z in states):
            raise EvidenceError('partial getter state')
        for state in states:check_values(state)
        if uni is None:raise EvidenceError('competitor state without pool')
        if x['same_boundary_competitor_read'] is not True:
            raise EvidenceError('competitor read inconsistency')
        if x['event_matches_after'] is not compare_event(states[1],evt) or \
           x['before_differs_from_after'] is compare_event(states[0],states[1]):
            raise EvidenceError('forged reconstruction comparison')
    elif x['same_boundary_competitor_read'] or x['event_matches_after'] or x['before_differs_from_after']:
        raise EvidenceError('getters missing but booleans asserted')
    verified=x['status']=='VERIFIED_TWO_POOL_TRANSACTION_BOUNDARY'
    if caps.get('intrablock_pool_state_confirmed') is not verified:
        raise EvidenceError('reconstruction capability mismatch')
    if verified:
        if x.get('reason') is not None or not sd['has_target_pool_delta'] or not all(states) or \
           not x['event_matches_after'] or not x['before_differs_from_after'] or \
           not x['same_boundary_competitor_read']:
            raise EvidenceError('invalid verified-boundary evidence')
        return 'TX_BOUNDARY_GETTERS_CONSISTENT_NOT_FULL_QUOTE'
    if not isinstance(x.get('reason'),str) or not x['reason']:
        raise EvidenceError('unproven state missing reason')
    return 'TX_BOUNDARY_UNPROVEN_NO_PROFIT_CLAIM'

def check_values(values):
    if set(values) != {'sqrt_price_x96','liquidity_raw','tick'}:
        raise EvidenceError('unexpected pool values')
    for field,top in [('sqrt_price_x96',1<<160),('liquidity_raw',1<<128)]:
        raw=values.get(field)
        if not isinstance(raw,str) or not raw.isascii() or not raw.isdecimal() or \
            str(int(raw))!=raw or not 0 < int(raw) < top:
            raise EvidenceError('invalid price/liquidity string')
    tick=values.get('tick')
    if type(tick) is not int or not -887272<=tick<=887272:
        raise EvidenceError('invalid tick')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report'); a=p.parse_args()
    try:
        obj=json.loads(Path(a.report).read_text(encoding='utf-8'))
        verdict=evaluate(obj)
        print('STEP 11.8 OFFLINE BOUNDARY CONSISTENCY — PASS, NOT RPC PROOF')
        print('verdict='+verdict)
        print('ALWAYS BLOCK: no complete tick state, actual round-trip quotation, measured gas or trade authorization')
    except (OSError,ValueError,KeyError,TypeError,EvidenceError):
        print('STEP 11.8 OFFLINE CONSISTENCY — FAIL-CLOSED',file=sys.stderr)
        sys.exit(2)
if __name__=='__main__':main()
