#!/usr/bin/env python3
"""Step 11.5: V3-first, swap-selected Polygon historical viability screening.

Always read-only. Inherits verified swap events and historical pool snapshots from
Step 11.4, adds Algebra <-> Uniswap V3 and QuickSwap V2 <-> V3 quotes, bounded
refinement around gross-positive sizes, and explicit *assumption-based* gas
stress costs. NO intra-block replay, contract execution, or trading capability.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from step11_collect import EvidenceError, ProviderError, TOKENS, get_hash, flash_fee, read_pair, QUICK_FACTORY
from step11_2_v3_collect import QuoteRevert
from step11_3_event_mixed_collect import quote_leg
from step11_4_swap_collect import EventRpc, collect_one

# Polygon wrapped native coin, historically WMATIC; native unit now POL.
WRAPPED_NATIVE = '0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270'
ROUTE_FAMILIES = ('V3_V3', 'QUICK_V2_V3')
GAS_FX = 'QUICKSWAP_V2_WRAPPED_NATIVE_TO_BORROW_RESERVE_ESTIMATE_ONE_UNIT'
MAX_QUOTE_ATTEMPTS = 300


def ceildiv(a, b):
    if type(a) is not int or type(b) is not int or a < 0 or b <= 0:
        raise EvidenceError('unsafe cost arithmetic')
    return (a + b - 1) // b


def route_pairs(tiers):
    v3 = ['algebra'] + ['uniswap_' + str(t) for t in tiers]
    return [(v3[i], v3[j], 'V3_V3') for i in range(len(v3)) for j in range(i + 1, len(v3))] + [
        ('quick', v, 'QUICK_V2_V3') for v in v3]


def available(snapshot, venue):
    pool = snapshot['v2'].get(venue) if venue == 'quick' else snapshot['v3'].get(venue)
    if pool is None:
        return False, 'SKIP_MISSING_POOL'
    if venue != 'quick' and int(pool['liquidity_raw']) == 0:
        return False, 'SKIP_ZERO_V3_LIQUIDITY'
    return True, 'OK'


def get_native_fx(rpc, borrowed, block):
    """Verified QuickSwap V2 wrapped-native/borrow spot conversion proxy.

    A missing direct pair produces UNASSESSED_GAS_FX, not a made-up price.
    Compute constant-product 0.30%-fee one-native quote from verified reserves;
    the gas-stress model additionally applies a conservative explicit buffer.
    """
    pool = read_pair(rpc, QUICK_FACTORY, WRAPPED_NATIVE, TOKENS[borrowed][0], block)
    if pool is None:
        return None
    if pool['token0'].lower() == WRAPPED_NATIVE:
        reserve_in, reserve_out = int(pool['reserve0_raw']), int(pool['reserve1_raw'])
    else:
        reserve_in, reserve_out = int(pool['reserve1_raw']), int(pool['reserve0_raw'])
    amount_in_fee = 10**18 * 997
    out = amount_in_fee * reserve_out // (reserve_in * 1000 + amount_in_fee)
    return out if out > 0 else None


def gas_budget(fx_raw, gas_units, gas_gwei, gas_buffer_bps):
    if fx_raw is None:
        return None
    # gas units * gwei * 1e9 = native wei. FX raw is borrowed units per 1e18 wei.
    return ceildiv(gas_units * gas_gwei * 10**9 * fx_raw * gas_buffer_bps,
                   10**18 * 10000)


def evaluate_quote(rpc, *, entry, block, route, size, fee_wad,
                   gas_raw, floor_units, floor_bps, iteration):
    borrow = entry['borrow_symbol']
    mid = entry['mid_symbol']
    _, decimals = TOKENS[borrow]
    principal = size * 10**decimals
    fee = flash_fee(principal, fee_wad)
    left, right, family = route
    record = {
        'block': block, 'pair_id': entry['pair_id'], 'borrow_symbol': borrow,
        'venue_first': left, 'venue_second': right, 'family': family,
        'size_units': size, 'iteration': iteration,
        'principal_raw': str(principal), 'flash_fee_raw': str(fee),
        'first_out_raw': None, 'returned_raw': None, 'gross_raw': None,
        'gas_budget_raw': str(gas_raw) if gas_raw is not None else None,
        'modeled_net_raw': None, 'required_floor_raw': str(max(floor_units * 10**decimals,
                                    ceildiv(principal * floor_bps, 10000))),
        'status': None
    }
    a, b = TOKENS[borrow][0], TOKENS[mid][0]
    one, reason = available(entry, left)
    two, reason2 = available(entry, right)
    if not one or not two:
        record['status'] = reason if not one else reason2
        return record
    if int(entry['vault_balance_raw']) < principal + fee:
        record['status'] = 'SKIP_INSUFFICIENT_VAULT_BALANCE'
        return record
    try:
        first, _ = quote_leg(rpc, left, a, b, principal,
                             int(left.split('_')[1]) if left.startswith('uniswap_') else 0, block)
        returned, _ = quote_leg(rpc, right, b, a, first,
                                int(right.split('_')[1]) if right.startswith('uniswap_') else 0, block)
    except QuoteRevert:
        record['status'] = 'SKIP_QUOTER_REVERT'
        return record
    if type(first) is not int or type(returned) is not int or not (0 < first < 2**256 and 0 < returned < 2**256):
        raise EvidenceError('invalid quote amount')
    gross = returned - principal - fee
    record.update(first_out_raw=str(first), returned_raw=str(returned), gross_raw=str(gross))
    if gross <= 0:
        record['status'] = 'REJECT_GROSS_UNPROFITABLE'
    elif gas_raw is None:
        record['status'] = 'UNASSESSED_GAS_FX'
    else:
        modeled = gross - gas_raw
        record['modeled_net_raw'] = str(modeled)
        record['status'] = ('MODEL_POSITIVE_REPLAY_REQUIRED'
                            if modeled >= int(record['required_floor_raw'])
                            else 'REJECT_MODELED_NET')
    return record


def select_refinement_sizes(records, base_sizes):
    """Add integer midpoint probes next to best gross-positive seed, max two."""
    positive = [r for r in records if r['gross_raw'] is not None and int(r['gross_raw']) > 0]
    if not positive:
        return []
    best = max(positive, key=lambda r: int(r['gross_raw']))['size_units']
    index = base_sizes.index(best)
    result = []
    for i in (index - 1, index + 1):
        if 0 <= i < len(base_sizes):
            midpoint = (base_sizes[i] + best) // 2
            if midpoint > 0 and midpoint not in base_sizes and midpoint not in result:
                result.append(midpoint)
    return result


def validate_params(first, last, pair_ids, tiers, sizes, max_events,
                    gas_units, gas_gwei, buffer_bps, min_units, min_bps):
    if not (type(first) is int and type(last) is int and 0 < first <= last and last-first+1 <= 400):
        raise EvidenceError('historical window must contain 1..400 blocks')
    allowed_pairs = {'DAI_WETH', 'USDCe_WETH', 'DAI_USDCe'}
    if not pair_ids or len(pair_ids) > 3 or len(set(pair_ids)) != len(pair_ids) or not set(pair_ids) <= allowed_pairs:
        raise EvidenceError('unvetted pair selection')
    if not tiers or len(tiers) > 2 or len(set(tiers)) != len(tiers) or not set(tiers) <= {500, 3000}:
        raise EvidenceError('unvetted Uniswap fee tiers')
    if not sizes or len(sizes) > 3 or len(set(sizes)) != len(sizes) or any(type(s) is not int or s < 1 or s > 100 for s in sizes) or sorted(sizes) != sizes:
        raise EvidenceError('sizes must be 1..3 distinct ascending whole stablecoin units')
    if type(max_events) is not int or not 1 <= max_events <= 2:
        raise EvidenceError('limit selected events to 1 or 2 blocks')
    if (type(gas_units) is not int or not 300000 <= gas_units <= 2000000 or
            type(gas_gwei) is not int or not 1 <= gas_gwei <= 1000 or
            type(buffer_bps) is not int or not 10000 <= buffer_bps <= 30000):
        raise EvidenceError('gas stress assumptions outside allowed range')
    if (type(min_units) is not int or not 0 <= min_units <= 100 or
            type(min_bps) is not int or not 0 <= min_bps <= 10000):
        raise EvidenceError('invalid profitability floor')
    possible = max_events * len(pair_ids) * len(route_pairs(tiers)) * 2 * (len(sizes) + 2)
    if possible > MAX_QUOTE_ATTEMPTS:
        raise EvidenceError('quote budget exceeded; reduce pairs, tiers or sizes')


def run_one(rpc, first, last, pair_ids, tiers, sizes, max_events,
            gas_units, gas_gwei, buffer_bps, min_units, min_bps):
    # Step 11.4 independently validates historical pool identities, block hashes,
    # canonical Swap logs, and baseline mixed quotes. No state overrides.
    base = collect_one(rpc, first, last, sizes, tiers, pair_ids, max_events)
    findings = []
    fx = []
    selected = base['selected_blocks']
    for block in selected:
        h = block['number']
        if get_hash(rpc, h) != block['hash']:
            raise EvidenceError('block changed before V3 overlay')
        fee_wad = int(block['fee_rate_wad'])
        active = set(block['active_pairs'])
        for entry in block['pairs']:
            if entry['pair_id'] not in active:
                continue
            borrowed = entry['borrow_symbol']
            fx_raw = get_native_fx(rpc, borrowed, h)
            gas_raw = gas_budget(fx_raw, gas_units, gas_gwei, buffer_bps)
            fx.append({'block':h,'pair_id':entry['pair_id'], 'quote_1_native_raw':str(fx_raw) if fx_raw is not None else None,
                       'gas_budget_raw':str(gas_raw) if gas_raw is not None else None})
            for left, right, family in route_pairs(tiers):
                for a, b in ((left, right), (right, left)):
                    route = (a, b, family)
                    coarse = [evaluate_quote(rpc,entry=entry,block=h,route=route,size=s,fee_wad=fee_wad,
                              gas_raw=gas_raw,floor_units=min_units,floor_bps=min_bps,iteration='coarse') for s in sizes]
                    findings.extend(coarse)
                    for s in select_refinement_sizes(coarse,sizes):
                        findings.append(evaluate_quote(rpc,entry=entry,block=h,route=route,size=s,fee_wad=fee_wad,
                             gas_raw=gas_raw,floor_units=min_units,floor_bps=min_bps,iteration='refinement'))
        if get_hash(rpc, h) != block['hash']:
            raise EvidenceError('block changed after V3 overlay')
    if get_hash(rpc, last) != base['window']['anchor_hash']:
        raise EvidenceError('historical anchor changed during V3 study')
    counts = {}
    for r in findings:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    candidates = [r for r in findings if r['status'] == 'MODEL_POSITIVE_REPLAY_REQUIRED']
    base['step11_5'] = {
        'schema_version': 1,
        'mode': 'V3_FIRST_SAME_BLOCK_END_STATE_VIABILITY_SCREEN_ONLY',
        'v3_first': True, 'executor_can_trade_these_routes': False,
        'signed_or_submitted_transaction': False,
        'gas_model_is_actual_transaction_measurement': False,
        'cost_assumptions': {'gas_units':gas_units, 'gas_gwei':gas_gwei,
            'gas_buffer_bps':buffer_bps, 'min_net_units':min_units, 'min_net_bps':min_bps,
            'native_fx_source':GAS_FX, 'fx_is_spot_approximation':True},
        'gas_fx': fx, 'findings': findings,
        'verdict_counts': counts, 'model_positive_count':len(candidates),
        'conclusion': 'REPLAY_RESEARCH_ONLY' if candidates else 'NO_MODEL_POSITIVE_EDGE_IN_SAMPLED_STATES',
        'limitations': [
            'Historical end-of-block quotes are not transaction-level reconstruction',
            'Two sequential eth_call quotes do not execute swaps or a flash loan',
            'Gas units and gas gwei are operator-assumed stress inputs, not measured execution gas',
            'Wrapped native conversion uses one-unit QuickSwap V2 historical quote and may not represent executable gas FX',
            'A positive model is never evidence of live profitability, inclusion or permission to broadcast'
        ]}
    return base


def collect(primary, fallback, first, last, pair_ids, tiers, sizes, max_events,
            gas_units, gas_gwei, buffer_bps, min_units, min_bps):
    validate_params(first,last,pair_ids,tiers,sizes,max_events,gas_units,gas_gwei,buffer_bps,min_units,min_bps)
    args=(first,last,pair_ids,tiers,sizes,max_events,gas_units,gas_gwei,buffer_bps,min_units,min_bps)
    try:
        result=run_one(primary,*args)
        result['provider_role']='primary'
        return result
    except (ProviderError, EvidenceError):
        if fallback is None:
            raise
        # Discard ALL primary evidence and restart from anchor with fallback.
        result=run_one(fallback,*args)
        result['provider_role']='fallback'
        return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--from-block',type=int,default=96179458)
    ap.add_argument('--to-block',type=int,default=96179857)
    ap.add_argument('--pairs',nargs='+',default=['DAI_WETH','USDCe_WETH'])
    ap.add_argument('--tiers',nargs='+',type=int,default=[500,3000])
    ap.add_argument('--sizes',nargs='+',type=int,default=[1,5,25])
    ap.add_argument('--max-events',type=int,default=2)
    ap.add_argument('--gas-units',type=int,default=750000)
    ap.add_argument('--gas-gwei',type=int,default=100)
    ap.add_argument('--gas-buffer-bps',type=int,default=12500)
    ap.add_argument('--min-net-units',type=int,default=1)
    ap.add_argument('--min-net-bps',type=int,default=20)
    ap.add_argument('--output',default='outputs/step11_5_viability.json')
    args=ap.parse_args()
    try:
        primary=EventRpc(os.environ.get('POLYGON_RPC_URL',''))
        fallback=EventRpc(os.environ['POLYGON_RPC_FALLBACK_URL']) if os.environ.get('POLYGON_RPC_FALLBACK_URL') else None
        result=collect(primary,fallback,args.from_block,args.to_block,args.pairs,args.tiers,args.sizes,
                       args.max_events,args.gas_units,args.gas_gwei,args.gas_buffer_bps,args.min_net_units,args.min_net_bps)
        path=Path(args.output)
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix(path.suffix+'.tmp')
        tmp.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        tmp.replace(path)
        step=result['step11_5']
        print('STEP 11.5 — V3-FIRST POLYGON STRATEGY VIABILITY, READ-ONLY')
        print(f'provider={result["provider_role"]} selected_blocks={len(result["selected_blocks"])} swaps={result["selected_log_count"]} evaluated={len(step["findings"])} model_positive_replay_only={step["model_positive_count"]}')
        for status, count in sorted(step['verdict_counts'].items()):print(f'{status}={count}')
        for r in sorted((x for x in step['findings'] if x['gross_raw'] is not None),
                        key=lambda x:int(x['gross_raw']),reverse=True)[:5]:
            print(f'  candidate_screen block={r["block"]} pair={r["pair_id"]} route={r["venue_first"]}_then_{r["venue_second"]} amount={r["size_units"]} gross_raw={r["gross_raw"]} verdict={r["status"]}')
        print('CONCLUSION:',step['conclusion'])
        print('EVIDENCE SAVED:',path)
        print('ALWAYS BLOCK: Assumed gas, end-of-block quotes, no V3 executor, no broadcast.')
    except (EvidenceError,ProviderError,ValueError,KeyError,TypeError) as error:
        print(f'STEP 11.5 FAIL-CLOSED: {type(error).__name__}: {error if type(error) is EvidenceError else "invalid provider or evidence (credentials hidden)"}',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':main()
