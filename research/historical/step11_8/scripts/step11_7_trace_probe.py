#!/usr/bin/env python3
"""Step 11.7: one verified historical Polygon Swap tx + call/prestate diff tracing.

Research only. No signer, broadcast, state override, contract execution or intrablock
eth_call. An RPC trace is *not* a cryptographic proof or a recreated intermediate
quote. Avoid saving full trace contents by default; emit summaries and hashes only.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from step11_collect import EvidenceError, ProviderError
from step11_3_event_mixed_collect import EventRpc, valid_historical_tip
from step11_4_swap_collect import build_pool_map, select_swap_blocks, strict_hex
from step11_6_discover import RESEARCH_PAIRS, snapshot_pair

MAX_TRACE_BYTES = 16_000_000
MAX_CALL_DEPTH = 64
MAX_CALL_FRAMES = 50000
TRACE_METHOD = 'debug_traceTransaction'


class TraceUnavailable(Exception):
    def __init__(self, kind='UNAVAILABLE'):
        self.kind = kind
        super().__init__(kind)


class TraceRpc(EventRpc):
    ALLOWED = EventRpc.ALLOWED | {'eth_getTransactionReceipt'}

    def trace(self, tx_hash, options):
        """Use separate bounded transport so arbitrary trace JSON never reaches stdout."""
        if self.transport is not None:
            try:
                obj = self.transport(TRACE_METHOD, [tx_hash, options])
            except Exception:
                raise TraceUnavailable('TRANSPORT_ERROR') from None
            if isinstance(obj, dict) and 'error' in obj:
                raise TraceUnavailable('RPC_REJECTED')
            if obj is None:
                raise TraceUnavailable('EMPTY_RESPONSE')
            return obj
        request = urllib.request.Request(self.url,
            data=json.dumps({'jsonrpc':'2.0','id':1,'method':TRACE_METHOD,
                'params':[tx_hash,options]}).encode(),
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=65) as response:
                raw = response.read(MAX_TRACE_BYTES + 1)
        except urllib.error.HTTPError:
            raise TraceUnavailable('HTTP_REJECTED') from None
        except Exception:
            raise TraceUnavailable('TRANSPORT_ERROR') from None
        if len(raw)>MAX_TRACE_BYTES:
            raise TraceUnavailable('OVERSIZE_RESPONSE')
        try:
            item=json.loads(raw)
        except (ValueError,UnicodeDecodeError):
            raise TraceUnavailable('MALFORMED_JSON') from None
        if not isinstance(item,dict) or item.get('error') is not None:
            raise TraceUnavailable('RPC_REJECTED')
        if not isinstance(item.get('result'),dict):
            raise TraceUnavailable('MALFORMED_TRACE')
        return item['result']


def count_calls(tree):
    if not isinstance(tree,dict) or not isinstance(tree.get('type'),str):
        raise EvidenceError('invalid callTracer root frame')
    pending=[(tree,0)]
    calls=[]
    while pending:
        frame, depth=pending.pop()
        if depth>MAX_CALL_DEPTH or len(calls)>=MAX_CALL_FRAMES:
            raise EvidenceError('call tracing frame safety bound exceeded')
        if not isinstance(frame,dict) or not isinstance(frame.get('type'),str):
            raise EvidenceError('malformed call frame')
        destination=frame.get('to')
        if destination is not None:
            destination=strict_hex(destination,42)
        calls.append(destination)
        sub=frame.get('calls',[])
        if not isinstance(sub,list):
            raise EvidenceError('malformed call tree children')
        pending.extend((s,depth+1) for s in reversed(sub))
    return calls


def inspect_prestate_diff(trace, known_pools):
    if not isinstance(trace,dict) or set(trace)!={'pre','post'}:
        raise TraceUnavailable('PRESTATE_DIFF_NOT_RETURNED')
    pre,post=trace['pre'],trace['post']
    if not isinstance(pre,dict) or not isinstance(post,dict):
        raise TraceUnavailable('PRESTATE_DIFF_MALFORMED')
    if len(pre)>10000 or len(post)>10000:
        raise TraceUnavailable('EXCESSIVE_ACCOUNT_COUNT')
    pool_summary=[]
    storage_touched=[]
    for section in (pre,post):
        for a,entry in section.items():
            try: strict_hex(a,42)
            except EvidenceError: raise TraceUnavailable('PRESTATE_DIFF_MALFORMED') from None
            if not isinstance(entry,dict):
                raise TraceUnavailable('PRESTATE_DIFF_MALFORMED')
            slots=entry.get('storage',{})
            if not isinstance(slots,dict) or len(slots)>100000:
                raise TraceUnavailable('PRESTATE_DIFF_MALFORMED')
            for slot,value in slots.items():
                try:
                    strict_hex(slot,66)
                    if not isinstance(value,str) or not value.startswith('0x') or not 1<=len(value[2:])<=64:
                        raise ValueError()
                    int(value[2:],16)
                except (EvidenceError,ValueError):
                    raise TraceUnavailable('PRESTATE_DIFF_MALFORMED') from None
    for address in sorted(known_pools):
        a=address.lower()
        before=pre.get(a,{}).get('storage',{})
        after=post.get(a,{}).get('storage',{})
        # Some providers use checksum addresses; normalize first below when possible.
        if before or after:
            storage_touched.append(a)
        pool_summary.append({'pool':a,'pre_slots':len(before),'post_slots':len(after),
            'has_storage_delta':bool(before or after)})
    return {'pre_account_count':len(pre),'post_account_count':len(post),
        'pool_diffs':pool_summary,'pools_with_slot_changes':len(storage_touched),
        'trace_sha256':hashlib.sha256(json.dumps(trace,sort_keys=True,separators=(',',':')).encode()).hexdigest()}


def normalize_diff_keys(trace):
    """Normalize account case without altering slot data."""
    if not isinstance(trace,dict) or set(trace)!={'pre','post'}:
        raise TraceUnavailable('PRESTATE_DIFF_NOT_RETURNED')
    normalized={}
    for key in ('pre','post'):
        if key not in trace or not isinstance(trace[key],dict):
            return trace
        lower={}
        for addr,obj in trace[key].items():
            if not isinstance(addr,str) or addr.lower() in lower:
                raise TraceUnavailable('PRESTATE_DIFF_MALFORMED')
            lower[addr.lower()]=obj
        normalized[key]=lower
    return normalized


def verify_swap_in_receipt(receipt,event,block):
    if not isinstance(receipt,dict):
        raise EvidenceError('transaction receipt missing')
    if strict_hex(receipt.get('transactionHash'),66)!=event['tx_hash']:
        raise EvidenceError('receipt transaction hash mismatch')
    if strict_hex(receipt.get('blockHash'),66)!=block['hash']:
        raise EvidenceError('receipt block hash mismatch')
    if int(receipt.get('transactionIndex','-1'),16)!=event['transaction_index']:
        raise EvidenceError('receipt transaction index mismatch')
    if int(receipt.get('blockNumber','-1'),16)!=block['number']:
        raise EvidenceError('receipt block number mismatch')
    if receipt.get('status')!='0x1':
        raise EvidenceError('swap transaction did not succeed')
    logs=receipt.get('logs')
    if not isinstance(logs,list):
        raise EvidenceError('receipt missing log collection')
    matches=0
    for lg in logs:
        if not isinstance(lg,dict):
            raise EvidenceError('bad receipt log')
        try: idx=int(lg['logIndex'],16)
        except (TypeError,KeyError,ValueError):
            raise EvidenceError('bad receipt log index') from None
        if idx==event['log_index']:
            if (strict_hex(lg.get('address'),42)!=event['pool'] or
                    not isinstance(lg.get('topics'),list) or not lg['topics'] or
                    strict_hex(lg['topics'][0],66)!=event['topic0']):
                raise EvidenceError('receipt swap log diverges from block log')
            matches+=1
    if matches!=1:
        raise EvidenceError('receipt does not uniquely confirm selected swap event')


def verify_block_membership(block,event):
    if not isinstance(block,dict) or not isinstance(block.get('transactions'),list):
        raise EvidenceError('historical block tx list missing')
    ix=event['transaction_index']
    if ix<0 or ix>=len(block['transactions']) or strict_hex(block['transactions'][ix],66)!=event['tx_hash']:
        raise EvidenceError('swap transaction not found at canonical transaction index')
    return len(block['transactions'])


def probe(rpc, block_number, pair_id, venue, tier):
    if pair_id not in RESEARCH_PAIRS or venue not in ('quick','algebra','uniswap_500','uniswap_3000'):
        raise EvidenceError('unvetted pair or venue')
    if tier not in (500,3000) or (venue.startswith('uniswap_') and venue!='uniswap_'+str(tier)):
        raise EvidenceError('inconsistent Uniswap fee tier')
    if not isinstance(block_number,int) or not 1 <= block_number <= 200000000:
        raise EvidenceError('invalid block number')
    valid_historical_tip(rpc,block_number)
    entry=snapshot_pair(rpc,pair_id,block_number,[tier])
    anchors={pair_id:entry}
    pools=build_pool_map(anchors)
    if not pools:raise EvidenceError('no verified historical pools')
    selected, observed=select_swap_blocks(rpc,block_number,block_number,pools,1)
    events=[e for e in observed.get(block_number,[]) if e['venue']==venue]
    if not events:
        raise EvidenceError('no matching verified Swap log for selected pair/venue/block')
    event=events[0]
    block=rpc.call('eth_getBlockByNumber',[hex(block_number),False])
    if not isinstance(block,dict) or block.get('number')!=hex(block_number):
        raise EvidenceError('historical block response invalid')
    block_hash=strict_hex(block.get('hash'),66)
    if event['block_hash']!=block_hash:
        raise EvidenceError('selected log belongs to different block hash')
    count=verify_block_membership(block,event)
    receipt=rpc.call('eth_getTransactionReceipt',[event['tx_hash']])
    verify_swap_in_receipt(receipt,event,{'hash':block_hash,'number':block_number})
    trace={'call_status':'UNAVAILABLE','call_error_kind':None,
        'call_frames':0,'verified_pool_in_call_tree':False,
        'diff_status':'UNAVAILABLE','diff_error_kind':None,'diff_summary':None}
    try:
        tree=rpc.trace(event['tx_hash'],{'tracer':'callTracer','tracerConfig':{'onlyTopCall':False},'timeout':'10s'})
        destinations=count_calls(tree)
        trace.update(call_status='SUPPORTED',call_frames=len(destinations),
            verified_pool_in_call_tree=event['pool'] in destinations)
    except TraceUnavailable as e:
        trace['call_error_kind']=e.kind
    try:
        diff=rpc.trace(event['tx_hash'],{'tracer':'prestateTracer',
            'tracerConfig':{'diffMode':True,'disableCode':True,'disableStorage':False},
            'timeout':'10s'})
        diff=normalize_diff_keys(diff)
        summary=inspect_prestate_diff(diff,pools)
        trace.update(diff_status='SUPPORTED',diff_summary=summary)
    except TraceUnavailable as e:
        trace['diff_error_kind']=e.kind
    check=rpc.call('eth_getBlockByNumber',[hex(block_number),False])
    if not isinstance(check,dict) or strict_hex(check.get('hash'),66)!=block_hash:
        raise EvidenceError('historical block hash changed during trace probe')
    return {'schema_version':1,
        'evidence_kind':'polygon_single_swap_trace_capability_research_only',
        'chain_id':137,'block':{'number':block_number,'hash':block_hash,
            'transaction_count':count},
        'swap':dict(event), 'receipt_confirmed':True,
        'trace':trace,
        'capabilities':{
            'call_tree_supported_for_target_swap':trace['call_status']=='SUPPORTED',
            'transaction_storage_diff_supported':trace['diff_status']=='SUPPORTED',
            'intermediate_pool_state_reconstructed':False,
            'intrablock_eth_call_or_quote_supported':False,
            'arbitrage_executable_profit_proven':False},
        'safety':{'read_only':True,'signing':False,'broadcast':False,
            'state_overrides':False,'relay_submission':False,'v3_executor_ready':False},
        'limitations':['A transaction receipt and trace response are RPC assertions, not signed state proofs.',
            'prestateTracer diffMode is per-transaction; full historical state replay remains unimplemented.',
            'Storage-slot deltas are not automatically a decoded V3 tick/liquidity state.',
            'No intermediate eth_call, gas estimate of executor, live profitability or inclusion verification.']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block',type=int,default=96179661)
    parser.add_argument('--pair',default='USDT_WETH')
    parser.add_argument('--venue',default='algebra')
    parser.add_argument('--tier',type=int,default=500)
    parser.add_argument('--output',default='outputs/step11_7_trace_probe.json')
    args=parser.parse_args()
    try:
        rpc=TraceRpc(os.getenv('POLYGON_RPC_URL',''))
        result=probe(rpc,args.block,args.pair,args.venue,args.tier)
        dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
        temp=dest.with_suffix(dest.suffix+'.tmp')
        temp.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
        temp.replace(dest)
        t=result['trace']
        print('STEP 11.7 SINGLE VERIFIED SWAP TRACE — READ ONLY')
        print('block=',result['block']['number'],'tx_index=',result['swap']['transaction_index'],
            'venue=',result['swap']['venue'],sep='')
        print('receipt=PASS call_trace='+t['call_status']+
              ' prestate_diff='+t['diff_status'])
        if t['diff_summary']:
            print('changed_pool_storage_count='+str(t['diff_summary']['pools_with_slot_changes'])+
                ' changed_account_count='+str(t['diff_summary']['post_account_count']))
        if t['call_error_kind']:print('call_trace_error_kind='+t['call_error_kind'])
        if t['diff_error_kind']:print('prestate_error_kind='+t['diff_error_kind'])
        print('EVIDENCE SAVED:',dest)
        print('ALWAYS BLOCK: trace capability != intermediate pool replay, quotes, economic edge, or execution authorization.')
    except (EvidenceError,ProviderError,ValueError) as e:
        print('STEP 11.7 FAIL-CLOSED:',type(e).__name__,
            str(e) if type(e)==EvidenceError else 'RPC error or malformed data; credentials hidden',file=sys.stderr)
        sys.exit(2)


if __name__=='__main__':main()
