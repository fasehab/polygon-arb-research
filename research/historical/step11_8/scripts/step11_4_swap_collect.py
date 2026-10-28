#!/usr/bin/env python3
"""Step 11.4: Polygon actual-Swap-event-selected mixed V2/V3 study.

NOT transaction-level reconstruction. eth_call at block N sees final state at N,
NOT state after an individual transaction inside N. No private key, signer,
broadcast, bundle, traces or simulation claims. All RPC methods read-only.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

from step11_collect import (EvidenceError, ProviderError, TOKENS, PAIRS, VAULT,
                             DECIMALS, uint_at, get_hash)
from step11_2_v3_collect import valid_code, ALG_FACTORY, ALG_QUOTER, UNI_FACTORY, UNI_QUOTER_V2
from step11_3_event_mixed_collect import (EventRpc, MAX_WINDOW, MAX_EVENTS, MAX_LOGS,
    MAX_SIZES, PAIR_IDS, valid_historical_tip, validated_v2, read_venues, evaluate_pair,
    read_fee, QUICK, SUSHI)

# Canonical Uniswap V2 and V3 Swap event topics. QuickSwap Algebra v1 uses
# V3-compatible Swap signature; unknown Algebra variants are NOT decoded here.
V2_SWAP='0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822'
V3_SWAP='0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'


def strict_hex(value, size):
    if not isinstance(value, str) or len(value) != size or not value.startswith('0x'):
        raise EvidenceError('invalid log hex field')
    try: int(value[2:],16)
    except ValueError: raise EvidenceError('invalid hex') from None
    return value.lower()


def build_pool_map(anchors):
    result={}
    for p, entry in anchors.items():
        for kind in ('v2','v3'):
            for venue, pool in entry[kind].items():
                if pool is None: continue
                addr=pool['address'].lower()
                if addr in result: raise EvidenceError('colliding pool identity')
                result[addr]=(p,kind,venue)
    return result


def select_swap_blocks(rpc, first, last, pool_map, limit):
    if not pool_map: return [],{}
    logs=rpc.call('eth_getLogs',[{'address':sorted(pool_map),
        'topics':[[V2_SWAP,V3_SWAP]],'fromBlock':hex(first),'toBlock':hex(last)}])
    if not isinstance(logs,list) or len(logs)>MAX_LOGS:
        raise EvidenceError('invalid/excessive swap log response')
    observed=defaultdict(list)
    seen=set()
    for lg in logs:
        if not isinstance(lg,dict) or lg.get('removed') is not False:
            raise EvidenceError('removed or malformed swap log')
        pool=strict_hex(lg.get('address'),42)
        if pool not in pool_map: raise EvidenceError('unexpected pool log')
        pair,kind,venue=pool_map[pool]
        topics=lg.get('topics')
        if not isinstance(topics,list) or not topics:raise EvidenceError('missing topics')
        topic=strict_hex(topics[0],66)
        if topic!=(V2_SWAP if kind=='v2' else V3_SWAP):
            raise EvidenceError('topic/protocol mismatch')
        for t in topics[1:]:strict_hex(t,66)
        # Strictly validate expected ABI data length to avoid ranking unrelated events.
        raw=lg.get('data')
        if not isinstance(raw,str) or not raw.startswith('0x') or len(raw)!=(2+64*(4 if kind=='v2' else 5)):
            raise EvidenceError('bad swap event data length')
        try:int(raw[2:],16)
        except ValueError:raise EvidenceError('nonhex swap data') from None
        try:
            height=int(lg['blockNumber'],16)
            txindex=int(lg['transactionIndex'],16)
            logindex=int(lg['logIndex'],16)
        except (KeyError,ValueError,TypeError):
            raise EvidenceError('invalid swap log indices') from None
        if not first<=height<=last or txindex<0 or logindex<0:
            raise EvidenceError('out-of-range swap log')
        hash_=strict_hex(lg.get('blockHash'),66)
        txhash=strict_hex(lg.get('transactionHash'),66)
        if (hash_,logindex) in seen:raise EvidenceError('duplicate swap log')
        seen.add((hash_,logindex))
        observed[height].append({'block_hash':hash_,'tx_hash':txhash,
            'transaction_index':txindex,'log_index':logindex,
            'pool':pool,'pair_id':pair,'kind':kind,'venue':venue,'topic0':topic})
    for height,events in observed.items():
        events.sort(key=lambda e:(e['transaction_index'],e['log_index']))
        if len({e['block_hash'] for e in events})!=1:
            raise EvidenceError('multiple block hashes at one height')
        txindex_by_hash={}
        for e in events:
            prev=txindex_by_hash.setdefault(e['tx_hash'],e['transaction_index'])
            if prev!=e['transaction_index']:
                raise EvidenceError('transaction index changes within block')
    # Prefer multi-venue swaps and multiple distinct TXs; not claims of profitability.
    rank=sorted(observed,key=lambda h:(len({e['venue'] for e in observed[h]}),
        len({e['tx_hash'] for e in observed[h]}),len(observed[h]),h),reverse=True)
    selected=sorted(rank[:limit])
    return selected,{b:observed[b] for b in selected}


def collect_one(rpc, first,last,sizes,tiers,pair_ids,max_events):
    valid_historical_tip(rpc,last)
    validated_v2(rpc,last)
    for contract in (ALG_FACTORY,ALG_QUOTER,UNI_FACTORY,UNI_QUOTER_V2,VAULT,QUICK,SUSHI):
        valid_code(rpc,contract,last)
    for pid,borrow,mid in PAIRS:
        if pid not in pair_ids:continue
        for sym in (borrow,mid):
            addr,dec=TOKENS[sym]
            valid_code(rpc,addr,last)
            if uint_at(rpc,addr,DECIMALS,last)!=dec:
                raise EvidenceError('token decimals mismatch')
    anchor_hash=get_hash(rpc,last)
    anchors={pid:read_venues(rpc,pid,last,tiers) for pid in pair_ids}
    pool_map=build_pool_map(anchors)
    selected,events=select_swap_blocks(rpc,first,last,pool_map,max_events)
    result_blocks=[]
    for height in selected:
        h=get_hash(rpc,height)
        if any(e['block_hash']!=h for e in events[height]):
            raise EvidenceError('event block hash mismatch')
        validated_v2(rpc,height)
        active_pairs=sorted({e['pair_id'] for e in events[height]})
        pairs=[]
        for pid in pair_ids:
            entry=read_venues(rpc,pid,height,tiers)
            for kind in ('v2','v3'):
                for venue,pool in entry[kind].items():
                    if (pool or {}).get('address')!=(anchors[pid][kind][venue] or {}).get('address'):
                        raise EvidenceError('pool identity changed within scan window')
            pairs.append(evaluate_pair(rpc,entry,height,sizes,tiers,pid in active_pairs))
        if get_hash(rpc,height)!=h:raise EvidenceError('historical block changed during scan')
        result_blocks.append({'number':height,'hash':h,
            'activity_event_count':len(events[height]),'active_pairs':active_pairs,
            'fee_rate_wad':str(read_fee(rpc,height)),'pairs':pairs,
            'swap_events':events[height],
            'distinct_swap_txs':len({e['tx_hash'] for e in events[height]})})
    if get_hash(rpc,last)!=anchor_hash:raise EvidenceError('window anchor changed')
    return {'schema_version':1,
        'evidence_kind':'polygon_historical_pool_event_mixed_v2_v3_readonly',
        'chain_id':137,'window':{'from_block':first,'to_block':last,'anchor_hash':anchor_hash},
        'sizes_whole_units':sizes,'tiers':tiers,'requested_pairs':pair_ids,
        'selected_log_count':sum(len(events[b]) for b in selected),
        'selected_blocks':result_blocks,
        'step11_4':{'selection':'CANONICAL_V2_V3_SWAP_TOPIC_ONLY',
            'transaction_indices_verified':True,
            'individual_transaction_state_reconstructed':False,
            'gross_positive_not_execution_authorization':True},
        'safety':{'writes_or_broadcasts_transactions':False,
            'uses_state_overrides':False,'proves_executor_eth_call':False,
            'proves_gas_or_net_profit':False,'proves_inclusion':False,
            'executor_supports_mixed_routes':False},
        'limitations':['ONLY matching canonical V2/V3 swap signatures are monitored',
            'eth_call(block N) returns the end-of-block state, not the state after the individual Swap transaction',
            'logs identify activity and order, not executable intra-block opportunities',
            'there is no V3 flash-loan execution contract, gas-adjusted simulation or inclusion evidence']}


def collect(primary,fallback,first,last,sizes,tiers,pair_ids,max_events):
    # Match Step 11.3 bounds; reject before making RPC calls.
    if not (type(first)==int and type(last)==int and 0<first<=last and last-first+1<=MAX_WINDOW):
        raise EvidenceError('invalid historical event window')
    if not (type(max_events)==int and 1<=max_events<=MAX_EVENTS):
        raise EvidenceError('invalid selected-block cap')
    if not (1<=len(sizes)<=MAX_SIZES and len(sizes)==len(set(sizes)) and
            all(type(s)==int and 0<s<=100 for s in sizes)):
        raise EvidenceError('invalid size list')
    if not tiers or len(tiers)!=len(set(tiers)) or not set(tiers)<={500,3000}:
        raise EvidenceError('unvetted fee tiers')
    if not pair_ids or len(pair_ids)!=len(set(pair_ids)) or not set(pair_ids)<=PAIR_IDS:
        raise EvidenceError('unvetted or duplicate pair list')
    try:
        result=collect_one(primary,first,last,sizes,tiers,pair_ids,max_events)
        result['provider_role']='primary'
        return result
    except (EvidenceError,ProviderError):
        if fallback is None:raise
        result=collect_one(fallback,first,last,sizes,tiers,pair_ids,max_events)
        result['provider_role']='fallback'
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-block',type=int,default=96179458)
    p.add_argument('--to-block',type=int,default=96179857)
    p.add_argument('--pairs',nargs='+',default=['DAI_WETH'])
    p.add_argument('--sizes',nargs='+',type=int,default=[1,5])
    p.add_argument('--tiers',nargs='+',type=int,default=[500])
    p.add_argument('--max-events',type=int,default=2)
    p.add_argument('--output',default='outputs/step11_4_swap_events.json')
    args=p.parse_args()
    try:
        primary=EventRpc(os.getenv('POLYGON_RPC_URL',''))
        fallback=EventRpc(os.environ['POLYGON_RPC_FALLBACK_URL']) if os.getenv('POLYGON_RPC_FALLBACK_URL') else None
        result=collect(primary,fallback,args.from_block,args.to_block,args.sizes,args.tiers,args.pairs,args.max_events)
        path=Path(args.output);path.parent.mkdir(exist_ok=True,parents=True)
        tmp=path.with_suffix(path.suffix+'.tmp')
        tmp.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        tmp.replace(path)
        quotes=[q for b in result['selected_blocks'] for entry in b['pairs'] for q in entry['quotes']]
        positives=sum(q['status']=='OK' and int(q['returned_raw'])>int(q['principal_raw'])+int(q['flash_fee_raw']) for q in quotes)
        print('STEP 11.4 SWAP-ONLY TRANSACTION-ORDERED HISTORICAL SCREEN — READ-ONLY')
        print(f'provider={result["provider_role"]} selected_blocks={len(result["selected_blocks"])} swaps={result["selected_log_count"]} quotes={len(quotes)} completed={sum(q["status"]=="OK" for q in quotes)} gross_positive_unassessed={positives}')
        for b in result['selected_blocks']:
            print(f'block={b["number"]} swaps={b["activity_event_count"]} distinct_txs={b["distinct_swap_txs"]} active_pairs={",".join(b["active_pairs"])}')
        if not result['selected_blocks']:
            print('NO MATCHING SWAP LOGS: limited to listed vetted pools and recognized signatures')
        print('EVIDENCE SAVED:',path)
        print('ALWAYS BLOCK: end-of-block historical quotes, not transaction-level reconstruction, gas-adjusted profit, or live execution.')
    except (EvidenceError,ProviderError,ValueError) as err:
        print('STEP 11.4 FAIL-CLOSED:',type(err).__name__, str(err) if type(err)==EvidenceError else '',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':main()
