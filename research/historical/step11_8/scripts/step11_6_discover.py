#!/usr/bin/env python3
"""Step 11.6: bounded, read-only active-pool reconnaissance on Polygon PoS.

Adds *research-only* USDT and WBTC to the original vetted list. Factory-derived
pool addresses, historical code/token/fee/decimal checks, canonical Swap logs,
per-pool activity counts and sequential historical V2/V3 quotes. Quoted depth is
not TVL; no intra-block reconstruction, execution gas, signatures or broadcast.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from step11_collect import (EvidenceError, ProviderError, TOKENS as ORIGINAL_TOKENS,
    QUICK, QUICK_FACTORY, VAULT, BALANCE, DECIMALS, FACTORY,
    address_word, decode_address, call_at, uint_at, get_hash, read_pair, flash_fee)
from step11_2_v3_collect import (QuoteRevert, valid_code, ALG_FACTORY,
    ALG_QUOTER, UNI_FACTORY, UNI_QUOTER_V2, read_pool)
from step11_3_event_mixed_collect import (EventRpc, quote_leg, read_fee,
    valid_historical_tip)
from step11_4_swap_collect import (V2_SWAP, V3_SWAP, select_swap_blocks,
    build_pool_map)
from step11_5_viability import (WRAPPED_NATIVE, GAS_FX, gas_budget, ceildiv)

# Known Polygon PoS assets, RESEARCH ALLOWLIST ONLY; not permission to trade.
RESEARCH_TOKENS = dict(ORIGINAL_TOKENS)
RESEARCH_TOKENS.update({
    'USDT': ('0xc2132d05d31c914a87c6611c10748aeb04b58e8f', 6),
    'WBTC': ('0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6', 8),
})
# Always borrow the stable asset, avoiding misleading "whole WBTC" sizes.
RESEARCH_PAIRS = {
    'DAI_WETH': ('DAI', 'WETH'), 'USDCe_WETH': ('USDCe', 'WETH'),
    'DAI_USDCe': ('DAI', 'USDCe'), 'USDT_WETH': ('USDT', 'WETH'),
    'USDT_WBTC': ('USDT', 'WBTC'), 'USDCe_WBTC': ('USDCe', 'WBTC'),
    'USDCe_USDT': ('USDCe', 'USDT'), 'DAI_USDT': ('DAI', 'USDT'),
    'DAI_WBTC': ('DAI', 'WBTC'),
}
MAX_WINDOW = 400
MAX_PAIRS = 3
MAX_SELECTED_BLOCKS = 2
MAX_ATTEMPTS = 144
VENUES = {'quick', 'algebra', 'uniswap_500', 'uniswap_3000'}


def venues_for(tiers):
    return ['quick', 'algebra'] + ['uniswap_' + str(t) for t in tiers]


def routes(venues):
    return [(a, b) for a in venues for b in venues if a != b]


def check_args(first, last, pairs, tiers, sizes, max_blocks, gas_units,
               gas_gwei, buffer_bps, min_units, min_bps):
    if not all(type(x) is int for x in [first, last]) or not 0 < first <= last or last-first+1 > MAX_WINDOW:
        raise EvidenceError('invalid historical window: 1..400 blocks required')
    if not pairs or len(pairs) > MAX_PAIRS or len(set(pairs)) != len(pairs) or any(p not in RESEARCH_PAIRS for p in pairs):
        raise EvidenceError('unvetted, duplicate or excessive research pairs (max 3)')
    if not tiers or len(tiers) > 2 or len(set(tiers)) != len(tiers) or any(type(t) is not int or t not in (500,3000) for t in tiers):
        raise EvidenceError('unsupported or duplicate Uniswap tiers')
    if not sizes or len(sizes)>3 or any(type(s) is not int or not 1 <= s <= 100 for s in sizes) or sorted(set(sizes)) != sizes:
        raise EvidenceError('invalid whole-stablecoin trade sizes')
    if type(max_blocks) is not int or not 1 <= max_blocks <= MAX_SELECTED_BLOCKS:
        raise EvidenceError('max selected blocks must be 1..2')
    if (type(gas_units) is not int or not 300000 <= gas_units <= 2000000 or
        type(gas_gwei) is not int or not 1 <= gas_gwei <= 1000 or
        type(buffer_bps) is not int or not 10000 <= buffer_bps <= 30000 or
        type(min_units) is not int or not 0 <= min_units <= 100 or
        type(min_bps) is not int or not 0 <= min_bps <= 10000):
        raise EvidenceError('gas assumptions or minimum profit outside conservative bounds')
    if max_blocks * len(pairs) * len(sizes) * len(routes(venues_for(tiers))) > MAX_ATTEMPTS:
        raise EvidenceError('RPC quote budget exceeded; reduce blocks, pairs, tiers or sizes')


def snapshot_pair(rpc, pair_id, block, tiers):
    borrowed, mid = RESEARCH_PAIRS[pair_id]
    a, _ = RESEARCH_TOKENS[borrowed]
    b, _ = RESEARCH_TOKENS[mid]
    return {
        'pair_id': pair_id, 'borrow_symbol': borrowed, 'mid_symbol': mid,
        'vault_balance_raw': str(uint_at(rpc, a, BALANCE + address_word(VAULT), block)),
        'v2': {'quick': read_pair(rpc, QUICK_FACTORY, a, b, block)},
        'v3': {'algebra': read_pool(rpc, ALG_FACTORY, a, b, 0, block, 'algebra'),
               **{'uniswap_' + str(t): read_pool(rpc, UNI_FACTORY, a, b, t, block, 'uniswap') for t in tiers}},
    }


def pool_ready(entry, venue):
    pool = entry['v2']['quick'] if venue == 'quick' else entry['v3'][venue]
    if pool is None:
        return False
    return venue == 'quick' or int(pool['liquidity_raw']) > 0


def fx_one_native(rpc, symbol, block):
    """One wrapped-native V2 quote is a *model proxy*, not a gas price oracle."""
    a = RESEARCH_TOKENS[symbol][0]
    pool = read_pair(rpc, QUICK_FACTORY, WRAPPED_NATIVE, a, block)
    if pool is None:
        return None
    if pool['token0'] == WRAPPED_NATIVE:
        ri, ro = int(pool['reserve0_raw']), int(pool['reserve1_raw'])
    else:
        ri, ro = int(pool['reserve1_raw']), int(pool['reserve0_raw'])
    fee_adj = 10**18 * 997
    out = fee_adj * ro // (ri * 1000 + fee_adj)
    return out if out > 0 else None


def make_quote(rpc, entry, block, v1, v2, size, fee_wad, gas_cost,
               min_units, min_bps):
    borrowed, middle = entry['borrow_symbol'], entry['mid_symbol']
    token_in, decimals = RESEARCH_TOKENS[borrowed]
    token_mid, _ = RESEARCH_TOKENS[middle]
    principal = size * 10**decimals
    fee = flash_fee(principal, fee_wad)
    floor = max(min_units * 10**decimals, ceildiv(principal * min_bps, 10000))
    q = {'block': block, 'pair_id': entry['pair_id'], 'borrow_symbol': borrowed,
         'venue_first': v1, 'venue_second': v2, 'size_units': size,
         'principal_raw': str(principal), 'flash_fee_raw': str(fee),
         'first_out_raw': None, 'returned_raw': None, 'gross_raw': None,
         'gas_budget_raw': str(gas_cost) if gas_cost is not None else None,
         'modeled_net_raw': None, 'required_floor_raw': str(floor), 'status': None}
    if not pool_ready(entry, v1) or not pool_ready(entry, v2):
        q['status'] = 'SKIP_MISSING_OR_INACTIVE_POOL'
    elif int(entry['vault_balance_raw']) < principal + fee:
        q['status'] = 'SKIP_INSUFFICIENT_VAULT_LIQUIDITY'
    else:
        try:
            tier1 = int(v1.split('_')[1]) if v1.startswith('uniswap_') else 0
            tier2 = int(v2.split('_')[1]) if v2.startswith('uniswap_') else 0
            first, _ = quote_leg(rpc, v1, token_in, token_mid, principal, tier1, block)
            returned, _ = quote_leg(rpc, v2, token_mid, token_in, first, tier2, block)
        except QuoteRevert:
            q['status'] = 'SKIP_QUOTER_REVERT'
        else:
            if type(first) is not int or type(returned) is not int or not 0 < first < 2**256 or not 0 < returned < 2**256:
                raise EvidenceError('invalid quote amount')
            gross = returned-principal-fee
            q.update(first_out_raw=str(first), returned_raw=str(returned), gross_raw=str(gross))
            if gross <= 0:
                q['status'] = 'REJECT_GROSS_UNPROFITABLE'
            elif gas_cost is None:
                q['status'] = 'UNASSESSED_GAS_FX'
            else:
                net = gross-gas_cost
                q['modeled_net_raw'] = str(net)
                q['status'] = 'MODEL_POSITIVE_REPLAY_REQUIRED' if net >= floor else 'REJECT_MODELED_NET'
    return q


def analyze_one(rpc, first, last, pairs, tiers, sizes, max_blocks,
                gas_units, gas_gwei, buffer_bps, min_units, min_bps):
    if int(rpc.call('eth_chainId', []), 16) != 137:
        raise EvidenceError('RPC chain is not Polygon PoS')
    valid_historical_tip(rpc, last)
    if decode_address(call_at(rpc, QUICK, '0x' + FACTORY, last)) != QUICK_FACTORY:
        raise EvidenceError('QuickSwap router/factory mismatch')
    for contract in (QUICK, QUICK_FACTORY, ALG_FACTORY, ALG_QUOTER,
                     UNI_FACTORY, UNI_QUOTER_V2, VAULT):
        valid_code(rpc, contract, last)
    for symbol in sorted({s for p in pairs for s in RESEARCH_PAIRS[p]}):
        addr, dec = RESEARCH_TOKENS[symbol]
        valid_code(rpc, addr, last)
        if uint_at(rpc, addr, DECIMALS, last) != dec:
            raise EvidenceError('research token decimals mismatch: ' + symbol)
    anchor_hash = get_hash(rpc, last)
    anchor = {p: snapshot_pair(rpc, p, last, tiers) for p in pairs}
    pool_map = build_pool_map(anchor)
    # This call validates canonical V2/V3 Swap ABI, block/tx/log indices,
    # duplicate logs and pool identities. No synthetic events are accepted.
    selected, by_block = select_swap_blocks(rpc, first, last, pool_map, max_blocks)
    events_per_pool = Counter(e['pool'] for evs in by_block.values() for e in evs)
    # Discovery scores are per-pool Swap event counts, NOT comparable TVL.
    pool_activity = []
    for pid in pairs:
        ent = anchor[pid]
        for kind in ('v2','v3'):
            for venue, pool in ent[kind].items():
                if pool is None:
                    pool_activity.append({'pair_id': pid, 'venue':venue, 'pool':None,
                                          'swap_logs_selected':0, 'status':'MISSING_POOL'})
                    continue
                status = 'ACTIVE_LIQUIDITY_NOT_QUANTIFIED'
                if kind == 'v3' and int(pool['liquidity_raw']) == 0:
                    status = 'ZERO_ACTIVE_V3_LIQUIDITY'
                pool_activity.append({'pair_id':pid, 'venue':venue, 'pool':pool['address'],
                    'swap_logs_selected':events_per_pool[pool['address'].lower()], 'status':status})
    quotes=[]
    gas_fx_records=[]
    block_summaries=[]
    selected_logs=0
    for b in selected:
        h=get_hash(rpc,b)
        events=by_block[b]
        if any(e['block_hash']!=h for e in events):
            raise EvidenceError('swap log/block hash divergence')
        selected_logs += len(events)
        active = sorted({e['pair_id'] for e in events})
        fee_wad = read_fee(rpc,b)
        for pid in active:
            ent=snapshot_pair(rpc,pid,b,tiers)
            for kind in ('v2','v3'):
                for v,pool in ent[kind].items():
                    old = anchor[pid][kind][v]
                    if (pool or {}).get('address') != (old or {}).get('address'):
                        raise EvidenceError('historical pool identity changed')
            borrow=ent['borrow_symbol']
            fx = fx_one_native(rpc,borrow,b)
            gas = gas_budget(fx,gas_units,gas_gwei,buffer_bps)
            gas_fx_records.append({'block':b,'pair_id':pid,
                'fx_one_native_raw':str(fx) if fx is not None else None,
                'gas_budget_raw':str(gas) if gas is not None else None})
            for v1,v2 in routes(venues_for(tiers)):
                for s in sizes:
                    quotes.append(make_quote(rpc,ent,b,v1,v2,s,fee_wad,gas,min_units,min_bps))
        if get_hash(rpc,b)!=h:
            raise EvidenceError('historical selected block changed')
        block_summaries.append({'number':b,'hash':h,'swap_event_count':len(events),
            'distinct_swap_txs':len({e['tx_hash'] for e in events}),
            'active_pairs':active,'fee_rate_wad':str(fee_wad),'swap_events':events})
    if get_hash(rpc,last)!=anchor_hash:
        raise EvidenceError('historical anchor block changed')
    verdict=Counter(q['status'] for q in quotes)
    meaningful = sum(q['gross_raw'] is not None for q in quotes)
    model_pos = verdict['MODEL_POSITIVE_REPLAY_REQUIRED']
    return {
        'schema_version':1, 'evidence_kind':'polygon_active_liquidity_bounded_read_only',
        'chain_id':137, 'provider_role':'primary',
        'window':{'from_block':first,'to_block':last,'anchor_hash':anchor_hash},
        'research_pair_universe': [{'pair_id':p,'borrow_symbol':RESEARCH_PAIRS[p][0],
            'mid_symbol':RESEARCH_PAIRS[p][1]} for p in pairs],
        'tier_ids':tiers,'sizes_whole_units':sizes, 'pool_activity':pool_activity,
        'selected_blocks':block_summaries,'selected_swap_logs':selected_logs,
        'gas_stress_model':{'gas_units':gas_units,'gas_gwei':gas_gwei,'buffer_bps':buffer_bps,
            'min_net_units':min_units,'min_net_bps':min_bps,
            'fx_source':GAS_FX,'actual_execution_gas_measured':False},
        'gas_fx_records':gas_fx_records,
        'quotes':quotes, 'status_counts':dict(verdict),
        'complete_quotes':meaningful,'model_positive_count':model_pos,
        'coverage':{'selected_blocks':len(selected), 'completed_quotes':meaningful,
            'adequate_window_for_study':bool(selected and meaningful)},
        'safety':{'signing':False,'broadcast':False,'private_relay_submission':False,
            'executor_supports_v3':False,'transaction_level_reconstruction':False,
            'gas_estimates_are_measured':False,'profitable_execution_proven':False},
        'limitations':[
            'Pool activity counts consider only selected sampled blocks, not full period or 24h volume',
            'V3 liquidity() is not comparable TVL or executable depth across pools',
            'End-of-block sequential eth_call quoting does not simulate atomic flash-loan execution',
            'Gas budget is hypothetical and wrapped-native spot FX is an approximation',
            'Positive model results require full-contract same-state fork replay and inclusion validation',
            'Older or inactive pools may be missing; no execution authorization is generated'],
    }


def collect(primary, fallback, *args):
    check_args(*args)
    try:
        result=analyze_one(primary,*args)
        return result
    except (EvidenceError,ProviderError):
        if fallback is None:
            raise
        # Never mix historical evidence from different providers inside a run.
        result=analyze_one(fallback,*args)
        result['provider_role']='fallback'
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-block',type=int,default=96179458)
    p.add_argument('--to-block',type=int,default=96179857)
    p.add_argument('--pairs',nargs='+',default=['USDT_WETH','USDCe_WBTC'])
    p.add_argument('--tiers',nargs='+',type=int,default=[500])
    p.add_argument('--sizes',nargs='+',type=int,default=[1,5])
    p.add_argument('--max-blocks',type=int,default=1)
    p.add_argument('--gas-units',type=int,default=750000)
    p.add_argument('--gas-gwei',type=int,default=100)
    p.add_argument('--gas-buffer-bps',type=int,default=12500)
    p.add_argument('--min-net-units',type=int,default=1)
    p.add_argument('--min-net-bps',type=int,default=20)
    p.add_argument('--output',default='outputs/step11_6_discovery.json')
    a=p.parse_args()
    try:
        primary=EventRpc(os.getenv('POLYGON_RPC_URL',''))
        fallback=EventRpc(os.environ['POLYGON_RPC_FALLBACK_URL']) if os.getenv('POLYGON_RPC_FALLBACK_URL') else None
        result=collect(primary,fallback,a.from_block,a.to_block,a.pairs,a.tiers,a.sizes,a.max_blocks,
                       a.gas_units,a.gas_gwei,a.gas_buffer_bps,a.min_net_units,a.min_net_bps)
        path=Path(a.output);path.parent.mkdir(exist_ok=True,parents=True)
        tmp=path.with_suffix(path.suffix+'.tmp')
        tmp.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        tmp.replace(path)
        print('STEP 11.6 — POLYGON ACTIVE-POOL DISCOVERY (RESEARCH ONLY)')
        print(f'provider={result["provider_role"]} selected_blocks={len(result["selected_blocks"])} observed_swaps={result["selected_swap_logs"]} quoted={len(result["quotes"])} complete={result["complete_quotes"]} modeled_positive_unverified={result["model_positive_count"]}')
        for rec in sorted(result['pool_activity'],key=lambda x:(-x['swap_logs_selected'],x['pair_id'],x['venue'])):
            print(f'  pool pair={rec["pair_id"]} venue={rec["venue"]} selected_swap_logs={rec["swap_logs_selected"]} status={rec["status"]}')
        for status,n in sorted(result['status_counts'].items()):print(f'{status}={n}')
        for rec in sorted((q for q in result['quotes'] if q['gross_raw'] is not None),
                          key=lambda q:int(q['gross_raw'])/10**RESEARCH_TOKENS[q['borrow_symbol']][1],reverse=True)[:5]:
            print(f'  screen block={rec["block"]} pair={rec["pair_id"]} route={rec["venue_first"]}_then_{rec["venue_second"]} input={rec["size_units"]} gross_raw={rec["gross_raw"]} status={rec["status"]}')
        print('EVIDENCE SAVED:',path)
        print('ALWAYS BLOCK: no signer, no relay, no broadcast, no actual executor simulation.')
    except (EvidenceError,ProviderError,ValueError,TypeError,KeyError) as e:
        print('STEP 11.6 FAIL-CLOSED:',type(e).__name__,str(e) if type(e) is EvidenceError else 'invalid RPC or evidence (credentials hidden)',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__': main()
