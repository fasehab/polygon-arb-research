#!/usr/bin/env python3
"""Offline Step 11.5 viability report self-consistency checks; cannot authenticate RPC.

Pair with Rust --step11-4-report on the same JSON to validate predecessor's
historical metadata. Neither validator is chain proof or execution permission.
"""
import argparse
import json
from pathlib import Path
from step11_collect import EvidenceError, flash_fee, TOKENS
from step11_5_viability import ceildiv, gas_budget, route_pairs, GAS_FX


def integer(raw, allow_negative=False):
    if not isinstance(raw,str) or not raw or raw == '-0':
        raise EvidenceError('invalid integer string')
    digits = raw[1:] if allow_negative and raw.startswith('-') else raw
    if not digits.isascii() or not digits.isdigit() or (len(digits)>1 and digits[0]=='0') or len(digits)>82:
        raise EvidenceError('malformed integer')
    return int(raw)


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def validate_report(obj):
    require(isinstance(obj,dict) and obj.get('chain_id')==137 and obj.get('safety',{}).get('writes_or_broadcasts_transactions') is False,
            'invalid chain / write flags')
    v = obj.get('step11_5')
    require(isinstance(v,dict) and v.get('schema_version')==1 and
            v.get('mode')=='V3_FIRST_SAME_BLOCK_END_STATE_VIABILITY_SCREEN_ONLY' and
            v.get('v3_first') is True and v.get('executor_can_trade_these_routes') is False and
            v.get('signed_or_submitted_transaction') is False and
            v.get('gas_model_is_actual_transaction_measurement') is False, 'unsafe report mode')
    a = v.get('cost_assumptions')
    require(isinstance(a,dict) and a.get('native_fx_source')==GAS_FX and a.get('fx_is_spot_approximation') is True,
            'missing gas conversion source')
    gas_units=a.get('gas_units');gas_gwei=a.get('gas_gwei');buffer=a.get('gas_buffer_bps')
    min_units=a.get('min_net_units');min_bps=a.get('min_net_bps')
    require(all(type(n) is int for n in (gas_units,gas_gwei,buffer,min_units,min_bps)) and
            300000<=gas_units<=2000000 and 1<=gas_gwei<=1000 and 10000<=buffer<=30000 and
            0<=min_units<=100 and 0<=min_bps<=10000,'bad scenario bounds')
    tiers=obj.get('tiers')
    require(isinstance(tiers,list) and tiers and len(set(tiers))==len(tiers) and set(tiers)<={500,3000},'bad tiers')
    allowed=set()
    for x,y,f in route_pairs(tiers):
        allowed.add((x,y,f));allowed.add((y,x,f))
    blocks=obj.get('selected_blocks')
    require(isinstance(blocks,list) and len(blocks)<=2,'invalid block list')
    pair_entries={}
    for block in blocks:
        h=block.get('number')
        require(type(h) is int and h>0 and type(block.get('fee_rate_wad')) is str,'invalid block')
        active=block.get('active_pairs')
        require(isinstance(active,list),'missing activity list')
        for p in block.get('pairs',[]):
            if p['pair_id'] in active:
                key=(h,p['pair_id'])
                require(key not in pair_entries,'duplicate pair in block')
                pair_entries[key]=p
    fx_map={}
    for e in v.get('gas_fx',[]):
        require(type(e.get('block')) is int and isinstance(e.get('pair_id'),str),'bad gas FX identity')
        key=(e['block'],e['pair_id'])
        require(key in pair_entries and key not in fx_map, 'invalid gas FX reference')
        raw=e.get('quote_1_native_raw')
        actual=None if raw is None else integer(raw)
        require(actual is None or actual>0,'zero gas FX')
        expected=gas_budget(actual,gas_units,gas_gwei,buffer)
        require(e.get('gas_budget_raw')==(None if expected is None else str(expected)), 'gas conversion arithmetic mismatch')
        fx_map[key]=expected
    require(set(fx_map)==set(pair_entries),'missing gas FX evidence for active pair')
    counts={};seen=set();positions={};positive_count=0
    findings=v.get('findings')
    require(isinstance(findings,list) and len(findings)<=300,'invalid quote count')
    for f in findings:
        key=(f.get('block'),f.get('pair_id'))
        require(key in pair_entries, 'quote not tied to active pair')
        entry=pair_entries[key]
        left,right,family=f.get('venue_first'),f.get('venue_second'),f.get('family')
        require((left,right,family) in allowed and left!=right, 'unknown route')
        size=f.get('size_units');iteration=f.get('iteration')
        require(type(size) is int and 1<=size<=100 and iteration in ('coarse','refinement'), 'invalid trade size')
        label=(key,left,right,size)
        require(label not in seen,'duplicate quote')
        seen.add(label)
        positions.setdefault((key,left,right),[]).append(f)
        decimals=TOKENS[entry['borrow_symbol']][1]
        principal=integer(f.get('principal_raw'))
        fee=integer(f.get('flash_fee_raw'))
        require(f.get('borrow_symbol')==entry['borrow_symbol'] and principal==size*10**decimals,
                'forged token principal')
        block = next(b for b in blocks if b['number']==key[0])
        require(fee==flash_fee(principal,int(block['fee_rate_wad'])),'forged flash fee')
        required=max(min_units*10**decimals,ceildiv(principal*min_bps,10000))
        require(integer(f.get('required_floor_raw'))==required,'invalid min net floor')
        expected_gas=fx_map[key]
        require(f.get('gas_budget_raw')==(None if expected_gas is None else str(expected_gas)), 'invalid gas budget')
        status=f.get('status')
        counts[status]=counts.get(status,0)+1
        pools=entry['v2'] | entry['v3']
        a_pool,b_pool=pools.get(left),pools.get(right)
        missing=a_pool is None or b_pool is None
        zero_liq=any(p is not None and int(p.get('liquidity_raw','1'))==0
                     for p in (a_pool,b_pool) if p is not None and p in entry['v3'].values())
        low_vault=int(entry['vault_balance_raw'])<principal+fee
        if status.startswith('SKIP_'):
            require(f['returned_raw'] is None and f['gross_raw'] is None and
                    f['first_out_raw'] is None and f['modeled_net_raw'] is None,'skipped quote has amounts')
            require((status=='SKIP_MISSING_POOL' and missing) or
                    (status=='SKIP_ZERO_V3_LIQUIDITY' and not missing and zero_liq) or
                    (status=='SKIP_INSUFFICIENT_VAULT_BALANCE' and not missing and not zero_liq and low_vault) or
                    (status=='SKIP_QUOTER_REVERT' and not missing and not zero_liq and not low_vault),
                    'unsupported skip reason')
            continue
        require(not missing and not zero_liq and not low_vault,'completed quote contradicts pool state')
        require(status in ('REJECT_GROSS_UNPROFITABLE','REJECT_MODELED_NET','UNASSESSED_GAS_FX',
                           'MODEL_POSITIVE_REPLAY_REQUIRED'),'unrecognized verdict')
        first=integer(f.get('first_out_raw'))
        returned=integer(f.get('returned_raw'))
        gross=returned-principal-fee
        require(first>0 and returned>0 and f.get('gross_raw')==str(gross),'forged gross quote')
        if gross<=0:
            require(status=='REJECT_GROSS_UNPROFITABLE' and f['modeled_net_raw'] is None,'impossible gross rejection')
        elif expected_gas is None:
            require(status=='UNASSESSED_GAS_FX' and f['modeled_net_raw'] is None,'missing FX mistaken for profit')
        else:
            net=gross-expected_gas
            require(f.get('modeled_net_raw')==str(net),'net arithmetic mismatch')
            desired='MODEL_POSITIVE_REPLAY_REQUIRED' if net>=required else 'REJECT_MODELED_NET'
            require(status==desired,'incorrect model verdict')
            if desired=='MODEL_POSITIVE_REPLAY_REQUIRED': positive_count+=1
    seed_sizes=obj.get('sizes_whole_units')
    # Existing historic report lists coarse sizes, so refinement must be a midpoint
    # adjacent to one of the gross-positive coarse seeds for that same route.
    if seed_sizes is not None:
        require(isinstance(seed_sizes,list) and seed_sizes==sorted(seed_sizes) and
                1<=len(seed_sizes)<=3 and len(seed_sizes)==len(set(seed_sizes)), 'bad coarse size list')
        for group, rows in positions.items():
            coarse=[r for r in rows if r['iteration']=='coarse']
            require({r['size_units'] for r in coarse}==set(seed_sizes),'incomplete coarse size grid')
            positives=[r for r in coarse if r['gross_raw'] is not None and int(r['gross_raw'])>0]
            expected=[]
            if positives:
                best=max(positives,key=lambda r:int(r['gross_raw']))['size_units']
                idx=seed_sizes.index(best)
                for j in (idx-1,idx+1):
                    if 0<=j<len(seed_sizes):
                        mid=(best+seed_sizes[j])//2
                        if mid not in seed_sizes and mid>0 and mid not in expected:expected.append(mid)
            actual={r['size_units'] for r in rows if r['iteration']=='refinement'}
            require(actual==set(expected),'invalid adaptive refinement')
    require(v.get('verdict_counts')==counts and v.get('model_positive_count')==positive_count,
            'forged report totals')
    require(v.get('conclusion')==('REPLAY_RESEARCH_ONLY' if positive_count else 'NO_MODEL_POSITIVE_EDGE_IN_SAMPLED_STATES'),
            'invalid research conclusion')
    return {'evaluated':len(findings),'selected_blocks':len(blocks),'model_positive_replay_only':positive_count,
            'counts':counts}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('report')
    args=p.parse_args()
    try:
        result=validate_report(json.loads(Path(args.report).read_text()))
        print('STEP 11.5 OFFLINE VIABILITY CONSISTENCY — PASS, NOT A CHAIN PROOF')
        print(' '.join(f'{k}={v}' for k,v in result.items() if k!='counts'))
        for k,v in sorted(result['counts'].items()):print(f'{k}={v}')
        print('ALWAYS BLOCK: no signing, execution, gas measurement, or inclusion certification')
    except (EvidenceError,ValueError,KeyError,TypeError,IndexError) as e:
        print('STEP 11.5 FAIL-CLOSED:',type(e).__name__,str(e),file=__import__('sys').stderr)
        raise SystemExit(2)

if __name__=='__main__': main()
