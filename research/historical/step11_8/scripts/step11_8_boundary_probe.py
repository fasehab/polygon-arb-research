#!/usr/bin/env python3
"""Step 11.8: validate transaction-boundary V3 pool read with debug_traceCall(txIndex).

Historical, read-only capability research. Verify after-state getter output against
receipt's *last* swap on that pool in the target transaction, plus transaction
prestateTracer storage diff. A successful trace does NOT imply a valid txIndex.
No quotations, signing, broadcasting, account keys, or state overrides.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from step11_collect import EvidenceError
from step11_4_swap_collect import V3_SWAP, strict_hex
from step11_6_discover import snapshot_pair
from step11_7_trace_probe import (TraceRpc, TraceUnavailable, probe,
    normalize_diff_keys, inspect_prestate_diff)

MAX_TRACE_BYTES = 1_000_000
MAX_TX_INDEX = 10000
GLOBAL_STATE = '0xe76c01e4'     # Algebra globalState()
SLOT0 = '0x3850c7bd'           # Uniswap v3 slot0()
LIQUIDITY = '0x1a686502'       # liquidity()
SLOT_LIMIT = (1 << 160) - 1


class BoundaryTraceUnavailable(Exception):
    def __init__(self, kind):
        self.kind = kind
        super().__init__(kind)


class BoundaryRpc(TraceRpc):
    """Only debug_traceCall in addition to previously allowlisted RPC methods."""
    def trace_call(self, to, selector, block, txindex):
        if strict_hex(to, 42) != to.lower() or selector not in (GLOBAL_STATE, SLOT0, LIQUIDITY):
            raise EvidenceError('unvetted getter or target')
        if type(txindex) is not int or not 0 <= txindex <= MAX_TX_INDEX:
            raise EvidenceError('invalid historical transaction boundary')
        if type(block) is not int or not 1 <= block <= 200000000:
            raise EvidenceError('invalid historical block')
        params = [
            {'to': to, 'data': selector, 'gas': '0x7a120'},
            hex(block),
            {'tracer': 'callTracer', 'tracerConfig': {'onlyTopCall': True},
             'txIndex': txindex, 'timeout': '10s'},
        ]
        if self.transport is not None:
            try:
                reply = self.transport('debug_traceCall', params)
            except Exception:
                raise BoundaryTraceUnavailable('TRANSPORT_ERROR') from None
            if reply is None or (isinstance(reply, dict) and reply.get('error') is not None):
                raise BoundaryTraceUnavailable('RPC_REJECTED')
        else:
            req = urllib.request.Request(self.url,
                data=json.dumps({'jsonrpc':'2.0','id':1,'method':'debug_traceCall',
                    'params':params}).encode(), headers={'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=55) as r:
                    raw = r.read(MAX_TRACE_BYTES + 1)
            except urllib.error.HTTPError:
                raise BoundaryTraceUnavailable('HTTP_REJECTED') from None
            except Exception:
                raise BoundaryTraceUnavailable('TRANSPORT_ERROR') from None
            if len(raw) > MAX_TRACE_BYTES:
                raise BoundaryTraceUnavailable('OVERSIZE_RESPONSE')
            try: obj = json.loads(raw)
            except (ValueError, UnicodeError):
                raise BoundaryTraceUnavailable('BAD_JSON') from None
            if not isinstance(obj, dict) or obj.get('error') is not None:
                raise BoundaryTraceUnavailable('RPC_REJECTED')
            reply = obj.get('result')
        if not isinstance(reply, dict) or reply.get('error') or reply.get('revertReason'):
            raise BoundaryTraceUnavailable('CALL_FAILED_OR_MALFORMED')
        if reply.get('type') not in ('CALL', 'STATICCALL'):
            raise BoundaryTraceUnavailable('UNEXPECTED_TRACE_TYPE')
        if strict_hex(reply.get('to'), 42) != to:
            raise BoundaryTraceUnavailable('WRONG_CALL_TARGET')
        output = reply.get('output')
        if not isinstance(output, str) or not output.startswith('0x') or len(output)<66 or (len(output)-2)%64:
            raise BoundaryTraceUnavailable('MALFORMED_GETTER_OUTPUT')
        try:
            out = [int(output[i:i+64],16) for i in range(2,len(output),64)]
        except ValueError:
            raise BoundaryTraceUnavailable('MALFORMED_GETTER_OUTPUT') from None
        if len(out)>16:
            raise BoundaryTraceUnavailable('EXCESS_GETTER_WORDS')
        return out


def signed_tick(value):
    if type(value) is not int or not 0 <= value < 2**256:
        raise EvidenceError('bad encoded tick')
    sign = value & ((1 << 24)-1)
    signed = sign - (1 << 24) if sign & (1 << 23) else sign
    expected = signed & ((1 << 256)-1)
    if value != expected or not -887272 <= signed <= 887272:
        raise EvidenceError('invalid int24 tick encoding')
    return signed


def decode_swap_event(log):
    """Uniswap v3 / Algebra v1 Swap: amount0,amount1,price,liquidity,tick."""
    if not isinstance(log,dict) or not isinstance(log.get('topics'),list) or not log['topics']:
        raise EvidenceError('malformed swap event')
    if strict_hex(log['topics'][0], 66) != V3_SWAP or len(log['topics']) != 3:
        raise EvidenceError('unexpected V3 swap topic layout')
    data=log.get('data')
    if not isinstance(data,str) or not data.startswith('0x') or len(data)!=2+5*64:
        raise EvidenceError('malformed V3 swap payload')
    try: w=[int(data[2+i*64:2+(i+1)*64],16) for i in range(5)]
    except ValueError: raise EvidenceError('invalid V3 event hex') from None
    if w[2]<=0 or w[2]>SLOT_LIMIT or w[3]<=0 or w[3]>=1<<128:
        raise EvidenceError('invalid event price/liquidity')
    return {'sqrt_price_x96':str(w[2]),'liquidity_raw':str(w[3]),'tick':signed_tick(w[4])}


def getter_decoded(state, liquidity):
    if not isinstance(state,list) or len(state)<2 or not isinstance(liquidity,list) or len(liquidity)!=1:
        raise EvidenceError('unrecognized V3 getter result')
    price=state[0]
    liq=liquidity[0]
    if not 0 < price <= SLOT_LIMIT or not 0 < liq < 1<<128:
        raise EvidenceError('invalid getter price/liquidity')
    return {'sqrt_price_x96':str(price),'liquidity_raw':str(liq),'tick':signed_tick(state[1])}


def receipt_final_swap(rpc, base):
    event=base['swap']
    tx=event['tx_hash']
    receipt=rpc.call('eth_getTransactionReceipt',[tx])
    if not isinstance(receipt,dict) or strict_hex(receipt.get('transactionHash'),66)!=tx or \
        strict_hex(receipt.get('blockHash'),66)!=base['block']['hash'] or receipt.get('status')!='0x1':
        raise EvidenceError('receipt mismatch')
    logs=receipt.get('logs')
    if not isinstance(logs,list) or len(logs)>2000:
        raise EvidenceError('receipt log set invalid')
    matching=[]
    for log in logs:
        if not isinstance(log,dict): raise EvidenceError('malformed log')
        if strict_hex(log.get('address'),42)==event['pool'] and \
            isinstance(log.get('topics'),list) and log['topics'] and \
            strict_hex(log['topics'][0],66)==V3_SWAP:
            matching.append((int(log['logIndex'],16),decode_swap_event(log)))
    if not matching or len({idx for idx,_ in matching}) != len(matching):
        raise EvidenceError('no unambiguous pool swap')
    if event['log_index'] not in {idx for idx,_ in matching}:
        raise EvidenceError('target historical swap absent')
    return max(matching,key=lambda t:t[0])[1],len(matching)


def changed_pool_diff(rpc,base):
    try:
        diff=normalize_diff_keys(rpc.trace(base['swap']['tx_hash'],{
            'tracer':'prestateTracer',
            'tracerConfig':{'diffMode':True,'disableCode':True,'disableStorage':False},
            'timeout':'10s'}))
        s=inspect_prestate_diff(diff,{base['swap']['pool']:('target','v3','algebra')})
    except TraceUnavailable as e:
        return False,'UNAVAILABLE_'+e.kind,None
    if s['pools_with_slot_changes']<1:
        return False,'NO_POOL_STORAGE_DELTA',s['trace_sha256']
    return True,'POOL_STORAGE_CHANGED',s['trace_sha256']


def compare_event(a,b):
    return all(a[k] == b[k] for k in ('sqrt_price_x96','liquidity_raw','tick'))


def gather(rpc, block_num=96179661,pair='USDT_WETH',tier=500):
    if pair!='USDT_WETH' or tier!=500 or block_num!=96179661:
        raise EvidenceError('first Step 11.8 milestone pinned to the verified transaction')
    base=probe(rpc,block_num,pair,'algebra',tier)
    event=base['swap']
    txindex=event['transaction_index']
    if type(txindex) is not int or not 0 <= txindex < base['block']['transaction_count']:
        raise EvidenceError('invalid receipt transaction index')
    snapshot=snapshot_pair(rpc,pair,block_num,[tier])
    alg=snapshot['v3']['algebra']
    uni=snapshot['v3']['uniswap_500']
    if not alg or alg['address'].lower()!=event['pool']:
        raise EvidenceError('Algebra pool identity does not match historical event')
    target=event['pool']
    expected,count=receipt_final_swap(rpc,base)
    storage_changed,diff_status,diff_hash=changed_pool_diff(rpc,base)
    report={'schema_version':1,'evidence_kind':'polygon_tx_boundary_getter_probe_research_only',
            'block':dict(base['block']), 'chain_id':137,
            'transaction':{'hash':event['tx_hash'],'index':txindex,'final_pool_swap_event':expected,
                'verified_swap_events_for_target_pool':count},
            'pools':{'algebra':target,'uniswap_500':uni['address'].lower() if uni else None},
            'storage_diff':{'status':diff_status,'has_target_pool_delta':storage_changed,
                'sha256':diff_hash},
            'txindex_tracecall':{'status':'UNPROVEN','reason':None,'before':None,'after':None,
                'competitor_after':None,'event_matches_after':False,'before_differs_from_after':False,
                'same_boundary_competitor_read':False},
            'capabilities':{'intrablock_pool_state_confirmed':False,
                'intrablock_quotations_performed':False,'complete_tick_state_reconstructed':False,
                'real_profit_proven':False},
            'safety':{'read_only':True,'signing':False,'broadcast':False,
                'relay_submission':False,'state_overrides':False,'v3_executor_ready':False}}
    t=report['txindex_tracecall']
    if not storage_changed:
        t['reason']='NO_VERIFIED_TARGET_STORAGE_CHANGE'
        return report
    if not uni:
        t['reason']='MISSING_VERIFIED_COUNTERPARTY_POOL'
        return report
    try:
        # The txIndex boundary convention is proven empirically against the final
        # receipt Swap event; do NOT trust provider merely accepting this option.
        before=getter_decoded(
            rpc.trace_call(target,GLOBAL_STATE,block_num,txindex),
            rpc.trace_call(target,LIQUIDITY,block_num,txindex))
        after=getter_decoded(
            rpc.trace_call(target,GLOBAL_STATE,block_num,txindex+1),
            rpc.trace_call(target,LIQUIDITY,block_num,txindex+1))
        competitor=getter_decoded(
            rpc.trace_call(uni['address'].lower(),SLOT0,block_num,txindex+1),
            rpc.trace_call(uni['address'].lower(),LIQUIDITY,block_num,txindex+1))
    except BoundaryTraceUnavailable as e:
        t['reason']=e.kind
        return report
    t.update(before=before,after=after,competitor_after=competitor,
        event_matches_after=compare_event(after,expected),
        before_differs_from_after=not compare_event(before,after),
        same_boundary_competitor_read=True)
    if not t['event_matches_after']:
        t['reason']='POST_STATE_DISAGREES_WITH_RECEIPT_EVENT'
    elif not t['before_differs_from_after']:
        t['reason']='TXINDEX_IGNORED_OR_PRICE_UNCHANGED'
    else:
        t['status']='VERIFIED_TWO_POOL_TRANSACTION_BOUNDARY'
        t['reason']=None
        report['capabilities']['intrablock_pool_state_confirmed']=True
    check=rpc.call('eth_getBlockByNumber',[hex(block_num),False])
    if not isinstance(check,dict) or strict_hex(check.get('hash'),66)!=base['block']['hash']:
        raise EvidenceError('historical block hash changed during boundary probe')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--block',type=int,default=96179661)
    p.add_argument('--pair',default='USDT_WETH')
    p.add_argument('--tier',type=int,default=500)
    p.add_argument('--output',default='outputs/step11_8_boundary.json')
    a=p.parse_args()
    try:
        rpc=BoundaryRpc(os.getenv('POLYGON_RPC_URL',''))
        report=gather(rpc,a.block,a.pair,a.tier)
        dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        b=report['txindex_tracecall']
        print('STEP 11.8 — TWO-POOL TX-BOUNDARY VALIDATION — READ-ONLY')
        print('block=%d tx_index=%d target=algebra counterpart=uniswap_500' %
            (report['block']['number'],report['transaction']['index']))
        print('pool_storage_change=%s txindex=%s' % (report['storage_diff']['status'],b['status']))
        print('event_matches_after=%s before_differs=%s competing_pool_read=%s' %
            (b['event_matches_after'],b['before_differs_from_after'],b['same_boundary_competitor_read']))
        if b['reason']: print('UNPROVEN_REASON='+b['reason'])
        print('EVIDENCE SAVED: '+str(dest))
        print('NO QUOTES / NO SIGNING / NO BROADCAST / NO RELAY')
    except (EvidenceError,TraceUnavailable,BoundaryTraceUnavailable,ValueError) as e:
        print('STEP 11.8 FAIL-CLOSED:',type(e).__name__,str(e) if isinstance(e,EvidenceError) else '',file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
