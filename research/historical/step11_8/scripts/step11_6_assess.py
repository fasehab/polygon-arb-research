#!/usr/bin/env python3
"""Offline consistency and multi-window go/no-go gate for Step 11.6.

This NEVER independently certifies chain data or executable profits. Files can
be manufactured; verification concerns schema, arithmetic and declared provenance.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from step11_6_discover import RESEARCH_PAIRS, RESEARCH_TOKENS, venues_for, routes, flash_fee, gas_budget
from step11_collect import EvidenceError, blockhash

SKIPS = {'SKIP_MISSING_OR_INACTIVE_POOL','SKIP_INSUFFICIENT_VAULT_LIQUIDITY','SKIP_QUOTER_REVERT'}
VERDICTS = SKIPS | {'REJECT_GROSS_UNPROFITABLE','REJECT_MODELED_NET',
                    'UNASSESSED_GAS_FX','MODEL_POSITIVE_REPLAY_REQUIRED'}


def integer(raw, label, *, signed=False):
    if isinstance(raw,bool) or not isinstance(raw,(str,int)):
        raise EvidenceError('invalid integer field: '+label)
    text=str(raw)
    if not text or text[0]=='+' or any(c.isspace() for c in text):
        raise EvidenceError('invalid numeric representation: '+label)
    if len(text)>80 or (text.startswith('0') and len(text)>1) or (text.startswith('-0')):
        raise EvidenceError('noncanonical numeric value: '+label)
    try: val=int(text)
    except ValueError: raise EvidenceError('non-numeric field: '+label) from None
    if str(val)!=text or abs(val)>=2**256 or (not signed and val<0):
        raise EvidenceError('overflow/noncanonical value: '+label)
    return val


def verify(data):
    if not isinstance(data,dict) or data.get('schema_version')!=1 or data.get('chain_id')!=137 or data.get('evidence_kind')!='polygon_active_liquidity_bounded_read_only':
        raise EvidenceError('unexpected Step 11.6 schema/chain')
    if data.get('provider_role') not in ('primary','fallback'):
        raise EvidenceError('bad provider identity')
    window=data.get('window')
    if not isinstance(window,dict):raise EvidenceError('missing window')
    lo=integer(window.get('from_block'),'from_block')
    hi=integer(window.get('to_block'),'to_block')
    if lo<=0 or hi<lo or hi-lo+1>400:raise EvidenceError('invalid historical window')
    blockhash(window.get('anchor_hash'))
    safety=data.get('safety')
    if not isinstance(safety,dict) or set(safety)!={'signing','broadcast','private_relay_submission',
            'executor_supports_v3','transaction_level_reconstruction','gas_estimates_are_measured',
            'profitable_execution_proven'} or any(x is not False for x in safety.values()):
        raise EvidenceError('unsafe or missing report flags')
    model=data.get('gas_stress_model')
    if not isinstance(model,dict) or model.get('actual_execution_gas_measured') is not False:
        raise EvidenceError('report misrepresents gas measurement')
    gu=integer(model.get('gas_units'),'gas_units'); gp=integer(model.get('gas_gwei'),'gas_gwei')
    buffer=integer(model.get('buffer_bps'),'buffer_bps')
    min_unit=integer(model.get('min_net_units'),'min_net_units')
    min_bps=integer(model.get('min_net_bps'),'min_net_bps')
    if not(300000<=gu<=2000000 and 1<=gp<=1000 and 10000<=buffer<=30000 and 0<=min_unit<=100 and 0<=min_bps<=10000):
        raise EvidenceError('gas assumption out of bounds')
    universe=data.get('research_pair_universe')
    if not isinstance(universe,list) or not 1<=len(universe)<=3:raise EvidenceError('bad pair list')
    ids=[]
    for p in universe:
        pid=p.get('pair_id') if isinstance(p,dict) else None
        if pid not in RESEARCH_PAIRS or (p.get('borrow_symbol'),p.get('mid_symbol')) != RESEARCH_PAIRS[pid]:
            raise EvidenceError('unknown pair/token')
        ids.append(pid)
    if len(set(ids))!=len(ids):raise EvidenceError('duplicate pairs')
    tiers=data.get('tier_ids');sizes=data.get('sizes_whole_units')
    if not isinstance(tiers,list) or not tiers or len(tiers)>2 or len(set(tiers))!=len(tiers) or any(type(t)!=int or t not in (500,3000) for t in tiers):
        raise EvidenceError('bad fee tiers')
    if not isinstance(sizes,list) or not sizes or len(sizes)>3 or any(type(x)!=int or not 1<=x<=100 for x in sizes) or sizes!=sorted(set(sizes)):
        raise EvidenceError('bad input sizes')
    events=data.get('selected_blocks')
    if not isinstance(events,list) or len(events)>2:raise EvidenceError('too many selected blocks')
    seen_blocks=set(); event_ids=set(); activity_count=Counter();logs=0
    pairs_by_block={};fee_wad_by_block={}
    for b in events:
        n=integer(b.get('number'),'block_number')
        bh=blockhash(b.get('hash'))
        if not lo<=n<=hi or n in seen_blocks:raise EvidenceError('repeated/out-of-range block')
        seen_blocks.add(n)
        evs=b.get('swap_events')
        if not isinstance(evs,list) or not evs or integer(b.get('swap_event_count'),'swap count')!=len(evs):
            raise EvidenceError('missing or inconsistent swap logs')
        active=set()
        txs=set()
        prev_idx=(-1,-1)
        for ev in evs:
            if ev.get('pair_id') not in ids or ev.get('block_hash')!=bh:
                raise EvidenceError('swap log foreign pair or hash')
            if ev.get('venue') not in venues_for(tiers):
                raise EvidenceError('foreign swap venue')
            tx=ev.get('tx_hash'); pool=ev.get('pool')
            if not isinstance(tx,str) or len(tx)!=66 or not tx.startswith('0x') or not isinstance(pool,str) or len(pool)!=42 or not pool.startswith('0x'):
                raise EvidenceError('bad swap address/hash')
            try:int(tx[2:],16);int(pool[2:],16)
            except ValueError:raise EvidenceError('nonhex swap') from None
            i=integer(ev.get('transaction_index'),'transaction_index')
            j=integer(ev.get('log_index'),'log_index')
            if (i,j)<=prev_idx:raise EvidenceError('swap log ordering invalid')
            prev_idx=(i,j)
            key=(bh,j)
            if key in event_ids:raise EvidenceError('duplicate swap event')
            event_ids.add(key);txs.add(tx);active.add(ev['pair_id'])
            activity_count[(ev['pair_id'],ev['venue'],pool)]+=1
        if b.get('distinct_swap_txs')!=len(txs) or b.get('active_pairs')!=sorted(active):
            raise EvidenceError('incorrect swap/active pairs summary')
        logs+=len(evs)
        pairs_by_block[n]=active
        fee_wad=integer(b.get('fee_rate_wad'),'fee_rate_wad')
        if fee_wad>10**16:raise EvidenceError('flash loan fee outside cap')
        fee_wad_by_block[n]=fee_wad
    if integer(data.get('selected_swap_logs'),'selected_swap_logs')!=logs:
        raise EvidenceError('wrong swap count')
    activities=data.get('pool_activity')
    if not isinstance(activities,list):raise EvidenceError('missing pool discovery')
    activity_keys=set()
    for a in activities:
        if a.get('pair_id') not in ids or a.get('venue') not in venues_for(tiers):
            raise EvidenceError('unexpected discovered pool')
        key=(a['pair_id'],a['venue'])
        if key in activity_keys:raise EvidenceError('duplicate discovered pool')
        activity_keys.add(key)
        p=a.get('pool'); c=integer(a.get('swap_logs_selected'),'pool swap count')
        if p is None:
            if c or a.get('status')!='MISSING_POOL':raise EvidenceError('fake missing pool')
        else:
            if not isinstance(p,str) or len(p)!=42 or not p.startswith('0x'):
                raise EvidenceError('invalid pool address')
            try:int(p[2:],16)
            except ValueError:raise EvidenceError('invalid pool address hex') from None
            if c!=activity_count.get((a['pair_id'],a['venue'],p),0) or a.get('status') not in ('ACTIVE_LIQUIDITY_NOT_QUANTIFIED','ZERO_ACTIVE_V3_LIQUIDITY'):
                raise EvidenceError('false pool activity count/status')
    expected_keys={(pid,v) for pid in ids for v in venues_for(tiers)}
    if activity_keys!=expected_keys:raise EvidenceError('missing pool discovery records')
    if sum(integer(x['swap_logs_selected'],'count') for x in activities)!=logs:
        raise EvidenceError('swap logs not assigned to discovered pools')
    fx_records=data.get('gas_fx_records')
    if not isinstance(fx_records,list):raise EvidenceError('missing gas conversion records')
    fx_map={}
    for r in fx_records:
        key=(integer(r.get('block'),'fx block'),r.get('pair_id'))
        if key in fx_map or key[0] not in pairs_by_block or key[1] not in pairs_by_block[key[0]]:
            raise EvidenceError('duplicate/unrelated gas conversion')
        raw=r.get('fx_one_native_raw')
        actual=None if raw is None else integer(raw,'fx_one_native_raw')
        if actual==0:raise EvidenceError('zero native FX')
        expected=None if actual is None else gas_budget(actual,gu,gp,buffer)
        observed=r.get('gas_budget_raw')
        if observed!=(str(expected) if expected is not None else None):
            raise EvidenceError('fabricated gas budget')
        fx_map[key]=observed
    if set(fx_map)!={(b,p) for b,active in pairs_by_block.items() for p in active}:
        raise EvidenceError('missing gas conversion')
    quote_list=data.get('quotes')
    if not isinstance(quote_list,list):raise EvidenceError('missing quotes')
    seen=set();counts=Counter();complete=0
    legal_routes=set(routes(venues_for(tiers)))
    expected_quote_keys={(b,p,a,z,s) for b,active in pairs_by_block.items() for p in active
        for a,z in legal_routes for s in sizes}
    for q in quote_list:
        b=integer(q.get('block'),'quote block');pid=q.get('pair_id')
        if b not in pairs_by_block or pid not in pairs_by_block[b]:raise EvidenceError('inactive-pair quote')
        if q.get('borrow_symbol')!=RESEARCH_PAIRS[pid][0]:raise EvidenceError('incorrect quote token')
        a,z=q.get('venue_first'),q.get('venue_second')
        if (a,z) not in legal_routes:raise EvidenceError('invalid venue route')
        sz=integer(q.get('size_units'),'size_units')
        if sz not in sizes:raise EvidenceError('unrequested size')
        key=(b,pid,a,z,sz)
        if key in seen:raise EvidenceError('duplicate quote')
        seen.add(key)
        d=RESEARCH_TOKENS[RESEARCH_PAIRS[pid][0]][1]
        principal=integer(q.get('principal_raw'),'principal_raw')
        fee=integer(q.get('flash_fee_raw'),'flash_fee_raw')
        floor=integer(q.get('required_floor_raw'),'required_floor_raw')
        if principal!=sz*10**d or floor!=max(min_unit*10**d,(principal*min_bps+9999)//10000):
            raise EvidenceError('size/floor arithmetic mismatch')
        if fee!=flash_fee(principal,fee_wad_by_block[b]):raise EvidenceError('forged flash fee')
        if q.get('gas_budget_raw')!=fx_map[(b,pid)]:
            raise EvidenceError('quote gas does not match block conversion')
        status=q.get('status')
        if status not in VERDICTS:raise EvidenceError('unexpected quote verdict')
        counts[status]+=1
        returned=q.get('returned_raw');gross=q.get('gross_raw')
        gas=q.get('gas_budget_raw');net=q.get('modeled_net_raw')
        out=q.get('first_out_raw')
        if status in SKIPS:
            if any(x is not None for x in (out,returned,gross,net)):
                raise EvidenceError('skipped quote has invented output')
            if gas is not None:integer(gas,'skipped gas')
            continue
        first_val=integer(out,'first_out');return_val=integer(returned,'returned_raw')
        gross_val=integer(gross,'gross_raw',signed=True)
        if first_val<=0 or return_val<=0 or gross_val != return_val-principal-fee:
            raise EvidenceError('forged quoted gross profit')
        complete+=1
        if gross_val<=0:
            if status!='REJECT_GROSS_UNPROFITABLE' or net is not None:
                raise EvidenceError('invalid loss classification')
        elif gas is None:
            if status!='UNASSESSED_GAS_FX' or net is not None:
                raise EvidenceError('gas missing but marked assessed')
        else:
            g=integer(gas,'gas_budget_raw');nv=integer(net,'modeled_net_raw',signed=True)
            if nv!=gross_val-g or (status=='MODEL_POSITIVE_REPLAY_REQUIRED')!=(nv>=floor):
                raise EvidenceError('forged modeled profit verdict')
            if status not in ('MODEL_POSITIVE_REPLAY_REQUIRED','REJECT_MODELED_NET'):
                raise EvidenceError('wrong economic verdict')
    if seen!=expected_quote_keys:raise EvidenceError('missing or extra quotes')
    if data.get('status_counts')!=dict(counts) or data.get('complete_quotes')!=complete or data.get('model_positive_count')!=counts['MODEL_POSITIVE_REPLAY_REQUIRED']:
        raise EvidenceError('incorrect rollup totals')
    c=data.get('coverage')
    if not isinstance(c,dict) or c.get('selected_blocks')!=len(events) or c.get('completed_quotes')!=complete or c.get('adequate_window_for_study') is not bool(events and complete):
        raise EvidenceError('misstated coverage')
    evaluated_pairs=sorted({q['pair_id'] for q in quote_list if q['gross_raw'] is not None})
    return {'window':(lo,hi),'selected_blocks':len(events),'complete_quotes':complete,
            'active_evaluated_pairs':evaluated_pairs,
            'quotes':len(quote_list),'model_positive':counts['MODEL_POSITIVE_REPLAY_REQUIRED'],
            'adequate':bool(events and complete),'pair_ids':ids,
            'skips':sum(counts[x] for x in SKIPS)}


def assess(files, required_windows=20, required_pairs=3):
    if type(required_windows)!=int or not 2<=required_windows<=100:
        raise EvidenceError('invalid go/no-go window target')
    if type(required_pairs)!=int or not 1<=required_pairs<=9:
        raise EvidenceError('invalid pair diversity threshold')
    if not files or len(files)>100:raise EvidenceError('1..100 distinct reports required')
    summaries=[]
    for f in files:
        p=Path(f)
        if p.stat().st_size>12_000_000:raise EvidenceError('report too large')
        summaries.append(verify(json.loads(p.read_text())))
    spans=sorted([r['window'] for r in summaries]);prev_last=-1
    for first,last in spans:
        if first<=prev_last:raise EvidenceError('overlapping/repeated history windows')
        prev_last=last
    adequate=sum(r['adequate'] for r in summaries)
    distinct_pairs=sorted({p for r in summaries for p in r['active_evaluated_pairs']})
    positives=sum(r['model_positive'] for r in summaries)
    if positives:
        verdict='RESEARCH_CANDIDATE_REQUIRES_FULL_FORK_REPLAY'
    elif adequate>=required_windows and len(distinct_pairs)>=required_pairs:
        verdict='PAUSE_STATIC_TWO_VENUE_STRATEGY_NO_EDGE_IN_STUDIED_WINDOWS'
    elif adequate>=required_windows:
        verdict='INSUFFICIENT_PAIR_DIVERSITY_FOR_GO_NO_GO'
    else:
        verdict='INSUFFICIENT_COVERAGE_FOR_GO_NO_GO'
    return {'files':len(summaries),'adequate_windows':adequate,
            'distinct_evaluated_pairs':','.join(distinct_pairs),
            'total_completed_quotes':sum(r['complete_quotes'] for r in summaries),
            'modeled_positive_unverified':positives,'verdict':verdict}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('reports',nargs='+',help='one or more independently collected Step 11.6 JSON reports')
    ap.add_argument('--required-windows',type=int,default=20)
    ap.add_argument('--required-pairs',type=int,default=3)
    args=ap.parse_args()
    try:
        result=assess(args.reports,args.required_windows,args.required_pairs)
        print('STEP 11.6 OFFLINE GO/NO-GO CONSISTENCY — PASS, NOT ON-CHAIN PROOF')
        for k,v in result.items():print(f'{k}={v}')
        print('ALWAYS BLOCK: historical evidence is not intrablock replay, measured gas or executable profit.')
    except (EvidenceError,ValueError,KeyError,TypeError,OSError,OverflowError) as e:
        print('STEP 11.6 FAIL-CLOSED:',type(e).__name__,str(e) if isinstance(e,EvidenceError) else 'invalid report',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':main()
