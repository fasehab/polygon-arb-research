#!/usr/bin/env python3
"""Step 11.3 bounded pool-activity-triggered Polygon V2<->V3 research.

Verified V2 pools: QuickSwap/SushiSwap. Verified V3 pools: QuickSwap Algebra,
Uniswap V3. Only historical eth_getLogs, eth_call, eth_getCode, and block reads.
An event is a pool-activity indicator (not necessarily a swap or price change).
V3 quoter estimates do not establish a trade that the V2-only executor can make.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from step11_collect import (EvidenceError, ProviderError, TOKENS, PAIRS, VAULT, QUICK,
    SUSHI, QUICK_FACTORY, SUSHI_FACTORY, FACTORY, COLLECTOR, FLASH_FEE, BALANCE,
    DECIMALS, address_word, decode_address, call_at, uint_at, get_hash, read_pair,
    encode_quote, decode_quote, flash_fee)
from step11_2_v3_collect import (V3Rpc, QuoteRevert, ALG_FACTORY, ALG_QUOTER,
    UNI_FACTORY, UNI_QUOTER_V2, read_pool, quote_alg, quote_uni, valid_code)

MAX_WINDOW = 400
MAX_EVENTS = 2
MAX_LOGS = 4500
MAX_SIZES = 3
PAIR_IDS = {p[0] for p in PAIRS}

class EventRpc(V3Rpc):
    ALLOWED = V3Rpc.ALLOWED | {'eth_getLogs'}


def valid_historical_tip(rpc, end):
    if int(rpc.call('eth_chainId', []), 16) != 137:
        raise EvidenceError('RPC is not Polygon')
    tip = int(rpc.call('eth_blockNumber', []), 16)
    if end > tip - 128:
        raise EvidenceError('requested event window not sufficiently historical')


def read_venues(rpc, pair_id, block, tiers):
    pair = next((x for x in PAIRS if x[0] == pair_id), None)
    if pair is None: raise EvidenceError('unvetted pair')
    _, borrowed, mid = pair
    a, dec = TOKENS[borrowed]
    b, _ = TOKENS[mid]
    return {'pair_id': pair_id, 'borrow_symbol': borrowed, 'mid_symbol': mid,
        'vault_balance_raw': str(uint_at(rpc, a, BALANCE + address_word(VAULT), block)),
        'v2': {'quick': read_pair(rpc, QUICK_FACTORY, a, b, block),
               'sushi': read_pair(rpc, SUSHI_FACTORY, a, b, block)},
        'v3': {'algebra': read_pool(rpc, ALG_FACTORY, a, b, 0, block, 'algebra'),
               **{'uniswap_' + str(t): read_pool(rpc, UNI_FACTORY, a, b, t, block, 'uniswap')
                  for t in tiers}}}


def validated_v2(rpc, block):
    for router, fac in [(QUICK, QUICK_FACTORY), (SUSHI, SUSHI_FACTORY)]:
        if decode_address(call_at(rpc, router, '0x' + FACTORY, block)) != fac:
            raise EvidenceError('V2 router/factory mismatch')


def read_fee(rpc, block):
    collector = decode_address(call_at(rpc, VAULT, '0x' + COLLECTOR, block))
    if int(collector, 16) == 0: raise EvidenceError('empty Vault fee collector')
    fee = uint_at(rpc, collector, FLASH_FEE, block)
    if fee > 10**16: raise EvidenceError('flash fee outside research cap')
    return fee


def select_activity_blocks(rpc, first, last, pool_map, max_events):
    addresses = sorted(pool_map)
    if not addresses: return [], {}
    logs = rpc.call('eth_getLogs', [{
        'address': addresses, 'fromBlock': hex(first), 'toBlock': hex(last)}])
    if not isinstance(logs, list) or len(logs) > MAX_LOGS:
        raise EvidenceError('invalid or excessive pool log response; shrink window')
    active = {}
    counts = Counter()
    seen_logs = set()
    for log in logs:
        if not isinstance(log, dict) or log.get('removed') is not False:
            raise EvidenceError('missing/removed pool log')
        pool = str(log.get('address','')).lower()
        if pool not in pool_map: raise EvidenceError('unexpected log address')
        try:
            h = int(log['blockNumber'], 16)
        except (KeyError, ValueError, TypeError):
            raise EvidenceError('invalid log block') from None
        if not first <= h <= last:
            raise EvidenceError('out of range pool log')
        # Log blockHash is compared to independently fetched block hash below.
        if not isinstance(log.get('blockHash'), str) or len(log['blockHash']) != 66:
            raise EvidenceError('missing log blockHash')
        tx_hash = log.get('transactionHash')
        index_hex = log.get('logIndex')
        if not isinstance(tx_hash,str) or len(tx_hash)!=66 or not isinstance(index_hex,str):
            raise EvidenceError('missing transaction identity in log')
        try: log_index = int(index_hex,16)
        except ValueError: raise EvidenceError('invalid log index') from None
        if log_index<0 or (tx_hash.lower(),log_index) in seen_logs:
            raise EvidenceError('duplicate or invalid pool log')
        seen_logs.add((tx_hash.lower(),log_index))
        p, role = pool_map[pool]
        active.setdefault(h, {'pairs': set(), 'roles': set(), 'hashes': set()})
        active[h]['pairs'].add(p)
        active[h]['roles'].add(role)
        active[h]['hashes'].add(log['blockHash'].lower())
        counts[h] += 1
        active[h]['count'] = counts[h]
    # Prefer blocks where both protocol families emitted events, then log volume.
    ranked = sorted(active, key=lambda h: (len(active[h]['roles']) == 2, counts[h], h), reverse=True)
    return sorted(ranked[:max_events]), {h: active[h] for h in ranked[:max_events]}


def quote_leg(rpc, venue, src, dst, amount, tier, block):
    if venue == 'quick' or venue == 'sushi':
        router = QUICK if venue == 'quick' else SUSHI
        return decode_quote(call_at(rpc, router, encode_quote(amount, src, dst), block), amount), None
    if venue == 'algebra':
        return quote_alg(rpc, src, dst, amount, block)
    if venue == 'uniswap_' + str(tier):
        return quote_uni(rpc, src, dst, amount, tier, block)
    raise EvidenceError('unvetted venue')


def evaluate_pair(rpc, snapshot, block, sizes, tiers, active):
    borrowed, mid = snapshot['borrow_symbol'], snapshot['mid_symbol']
    a, dec = TOKENS[borrowed]
    b, _ = TOKENS[mid]
    quotes = []
    fee = read_fee(rpc, block)
    for v2 in ('quick', 'sushi'):
        for v3 in ('algebra', *('uniswap_' + str(t) for t in tiers)):
            tier = int(v3.split('_')[1]) if v3.startswith('uniswap_') else 0
            for sz in sizes:
                principal = sz * (10 ** dec)
                for direction in ('v2_then_v3', 'v3_then_v2'):
                    status = 'OK'
                    first = returned = None
                    if not active:
                        status = 'SKIP_NO_POOL_ACTIVITY'
                    elif snapshot['v2'][v2] is None or snapshot['v3'][v3] is None:
                        status = 'SKIP_MISSING_POOL'
                    elif v3 == 'algebra' and int(snapshot['v3'][v3]['liquidity_raw']) == 0:
                        status = 'SKIP_ZERO_V3_LIQUIDITY'
                    elif v3.startswith('uniswap_') and int(snapshot['v3'][v3]['liquidity_raw']) == 0:
                        status = 'SKIP_ZERO_V3_LIQUIDITY'
                    else:
                        start, finish = (v2, v3) if direction == 'v2_then_v3' else (v3, v2)
                        try:
                            first, _ = quote_leg(rpc, start, a, b, principal, tier, block)
                            returned, _ = quote_leg(rpc, finish, b, a, first, tier, block)
                        except QuoteRevert:
                            first = returned = None
                            status = 'SKIP_V3_QUOTER_REVERT'
                    quotes.append({'v2':v2, 'v3':v3, 'direction':direction, 'size_units':sz,
                        'status':status, 'principal_raw':str(principal),
                        'flash_fee_raw':str(flash_fee(principal, fee)),
                        'first_out_raw':str(first) if status=='OK' else None,
                        'returned_raw':str(returned) if status=='OK' else None})
    snapshot['quotes'] = quotes
    return snapshot


def collect_one(rpc, first, last, sizes, tiers, pair_ids, max_events):
    valid_historical_tip(rpc, last)
    validated_v2(rpc, last)
    for contract in (ALG_FACTORY, ALG_QUOTER, UNI_FACTORY, UNI_QUOTER_V2, VAULT, QUICK, SUSHI):
        valid_code(rpc, contract, last)
    for pair_id, borrow, mid in PAIRS:
        if pair_id not in pair_ids: continue
        for token in (borrow, mid):
            addr, dec = TOKENS[token]
            valid_code(rpc, addr, last)
            if uint_at(rpc, addr, DECIMALS, last) != dec:
                raise EvidenceError('token decimals mismatch')
    anchor_hash = get_hash(rpc, last)
    anchors = {p:read_venues(rpc, p, last, tiers) for p in pair_ids}
    pool_map = {}
    for p, entry in anchors.items():
        for cat in ('v2','v3'):
            for venue, pool in entry[cat].items():
                if pool is None: continue
                key = pool['address']
                if key in pool_map and pool_map[key] != (p, cat):
                    raise EvidenceError('colliding pool identity across pair/protocol')
                pool_map[key] = (p, cat)
    selected, active = select_activity_blocks(rpc, first, last, pool_map, max_events)
    blocks=[]
    for height in selected:
        h = get_hash(rpc, height)
        if active[height]['hashes'] != {h}:
            raise EvidenceError('log block hash differs from canonical historical block')
        validated_v2(rpc, height)
        entries=[]
        for p in pair_ids:
            ent = read_venues(rpc, p, height, tiers)
            # Factory responses must refer to same pools as event discovery anchor.
            for cat in ('v2','v3'):
                for venue, pool in ent[cat].items():
                    prev = anchors[p][cat][venue]
                    if (pool or {}).get('address') != (prev or {}).get('address'):
                        raise EvidenceError('pool identity changed across scan window')
            entries.append(evaluate_pair(rpc, ent, height, sizes, tiers, p in active[height]['pairs']))
        if get_hash(rpc, height) != h: raise EvidenceError('block hash changed during research')
        blocks.append({'number':height,'hash':h,'activity_event_count':active[height]['count'],
          'active_pairs':sorted(active[height]['pairs']),'fee_rate_wad':str(read_fee(rpc,height)),
          'pairs':entries})
    if get_hash(rpc, last) != anchor_hash:
        raise EvidenceError('anchor changed during scan')
    return {'schema_version':1,'evidence_kind':'polygon_historical_pool_event_mixed_v2_v3_readonly',
        'chain_id':137,'window':{'from_block':first,'to_block':last,'anchor_hash':anchor_hash},
        'sizes_whole_units':sizes,'tiers':tiers,'requested_pairs':pair_ids,
        'selected_log_count':sum(active[b]['count'] for b in selected), 'selected_blocks':blocks,
        'safety':{'writes_or_broadcasts_transactions':False,
          'uses_state_overrides':False,'proves_executor_eth_call':False,
          'proves_gas_or_net_profit':False,'proves_inclusion':False,
          'executor_supports_mixed_routes':False},
        'limitations':['pool activity is not equivalent to swap or profitable price change',
          'V2 router and V3 quoter results are predictions not an atomic execution simulation',
          'V2-only Solidity executor cannot execute mixed routes',
          'read-only RPC evidence is not cryptographic proof of profit or event provenance']}


def collect(primary, fallback, first, last, sizes, tiers, pair_ids, max_events):
    if not (0 < first <= last and last-first+1 <= MAX_WINDOW):
        raise EvidenceError('invalid event window or too many blocks')
    if not (1 <= max_events <= MAX_EVENTS): raise EvidenceError('invalid max selected blocks')
    if not (1 <= len(sizes) <= MAX_SIZES and len(sizes)==len(set(sizes)) and
            all(type(x)==int and 0 < x <= 100 for x in sizes)):
        raise EvidenceError('invalid size list')
    if not tiers or len(tiers) != len(set(tiers)) or not set(tiers) <= {500,3000}:
        raise EvidenceError('invalid Uniswap tiers')
    if not pair_ids or len(pair_ids) != len(set(pair_ids)) or not set(pair_ids) <= PAIR_IDS:
        raise EvidenceError('unvetted or duplicate pairs')
    try:
        report = collect_one(primary, first, last, sizes, tiers, pair_ids, max_events)
        report['provider_role'] = 'primary'
        return report
    except (EvidenceError, ProviderError):
        if fallback is None: raise
        report = collect_one(fallback, first, last, sizes, tiers, pair_ids, max_events)
        report['provider_role'] = 'fallback'
        return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-block', type=int, default=96179700)
    p.add_argument('--to-block', type=int, default=96179857)
    p.add_argument('--pairs', nargs='+', default=['DAI_WETH'])
    p.add_argument('--sizes', type=int, nargs='+', default=[1])
    p.add_argument('--tiers', type=int, nargs='+', default=[500])
    p.add_argument('--max-events', type=int, default=2)
    p.add_argument('--output', default='outputs/step11_3_mixed_events.json')
    args = p.parse_args()
    try:
        primary = EventRpc(os.getenv('POLYGON_RPC_URL',''))
        fallback = EventRpc(os.environ['POLYGON_RPC_FALLBACK_URL']) if os.getenv('POLYGON_RPC_FALLBACK_URL') else None
        report = collect(primary, fallback, args.from_block, args.to_block,
            args.sizes, args.tiers, args.pairs, args.max_events)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + '.tmp')
        tmp.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        tmp.replace(out)
        quotes=[q for b in report['selected_blocks'] for entry in b['pairs'] for q in entry['quotes']]
        positives=[q for q in quotes if q['status']=='OK' and
                    int(q['returned_raw']) > int(q['principal_raw'])+int(q['flash_fee_raw'])]
        print('STEP 11.3 EVENT-TRIGGERED MIXED V2/V3 — READ-ONLY')
        print(f'provider={report["provider_role"]} selected_blocks={len(report["selected_blocks"])} evaluated={len(quotes)} completed={sum(q["status"]=="OK" for q in quotes)} gross_positive_unassessed={len(positives)}')
        for b in report['selected_blocks']:
            print(f'block={b["number"]} active_pairs={",".join(b["active_pairs"])}')
        if not report['selected_blocks']:
            print('NO EVENTS IN WINDOW: not evidence that arbitrage does not exist')
        print('EVIDENCE SAVED:', out)
        print('ALWAYS BLOCK: V2-only executor cannot trade mixed V2/V3 routes. No signatures, relay or broadcast.')
    except (EvidenceError, ProviderError, ValueError) as err:
        # Do not expose URL or raw provider response; validation issue only.
        print('STEP 11.3 FAIL-CLOSED:', type(err).__name__,str(err) if isinstance(err,EvidenceError) and type(err) is EvidenceError else '',file=sys.stderr)
        sys.exit(2)

if __name__ == '__main__':main()
