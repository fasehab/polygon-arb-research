#!/usr/bin/env python3
"""Step 11.9: one bounded Polygon block-diff replay, no signer or broadcasting.

A conditional end-of-block getter equivalence test, NOT complete intra-block
state reconstruction, independent RPC proof, correct V3 tick replay, or profit.

Requires Step 11.8's saved JSON. No transaction-indexed trace calls.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

# All addresses and hashes are revalidated rather than trusted from disk.
HX40 = re.compile(r'^0x[0-9a-fA-F]{40}$')
HX64 = re.compile(r'^0x[0-9a-fA-F]{64}$')
TOPIC = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'  # Uniswap/Algebra V3 Swap
MAX_REQUESTS = 9
MAX_REPLY = 12_000_000
MAX_TXS = 500
MAX_ELAPSED_SECONDS = 120
MAX_BLOCK = 96179661
GLOBAL_STATE = '0xe76c01e4'
UNI_SLOT0 = '0x3850c7bd'
LIQUIDITY = '0x1a686502'

class Halt(Exception):
    def __init__(self, reason, code=None):
        self.reason, self.code = reason, code
        super().__init__(reason)


def check_hex(value, n):
    if not isinstance(value, str) or (n==40 and not HX40.fullmatch(value)) or (n==64 and not HX64.fullmatch(value)):
        raise Halt('BAD_INPUT_EVIDENCE')
    return value.lower()


class RPC:
    def __init__(self, url, transport=None, max_requests=MAX_REQUESTS):
        if not isinstance(url,str) or not url.startswith('https://') or any(c.isspace() for c in url):
            raise Halt('HTTPS_RPC_MISSING')
        self.url, self.transport, self.count, self.maximum = url, transport, 0, max_requests
        self.deadline = time.monotonic() + MAX_ELAPSED_SECONDS

    def call(self, method, params):
        if self.count >= self.maximum:
            raise Halt('RPC_BUDGET_EXHAUSTED')
        if time.monotonic() >= self.deadline:
            raise Halt('WALL_CLOCK_BUDGET_EXHAUSTED')
        self.count += 1
        if self.transport:
            result = self.transport(method, params)
            if isinstance(result, dict) and 'error' in result:
                raise Halt('RPC_REJECTED', result.get('error',{}).get('code'))
            return result
        body=json.dumps({'jsonrpc':'2.0','id':self.count,'method':method,'params':params}).encode()
        req=urllib.request.Request(self.url,body,headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=max(1,min(75 if method=='debug_traceBlockByNumber' else 30, self.deadline-time.monotonic()))) as f:
                data=f.read(MAX_REPLY+1)
        except urllib.error.HTTPError as exc:
            raw=exc.read(10240)
            try:
                err=json.loads(raw)['error']
                raise Halt('RPC_REJECTED',err.get('code')) from None
            except (ValueError,KeyError,TypeError):
                raise Halt('HTTP_REJECTED',exc.code) from None
        except (TimeoutError, OSError, urllib.error.URLError):
            raise Halt('RPC_TRANSPORT_ERROR') from None
        if len(data)>MAX_REPLY: raise Halt('RESPONSE_SIZE_CAP_EXCEEDED')
        try: obj=json.loads(data)
        except (ValueError,UnicodeDecodeError): raise Halt('MALFORMED_RPC_JSON') from None
        if not isinstance(obj,dict): raise Halt('MALFORMED_RPC_JSON')
        if 'error' in obj: raise Halt('RPC_REJECTED',obj['error'].get('code'))
        if 'result' not in obj: raise Halt('MISSING_RPC_RESULT')
        return obj['result']


def decode_words(payload, count_min=1, count_max=12):
    if not isinstance(payload,str) or not payload.startswith('0x') or len(payload)<2+count_min*64 or (len(payload)-2)%64:
        raise Halt('MALFORMED_GETTER_RESULT')
    n=(len(payload)-2)//64
    if n>count_max: raise Halt('MALFORMED_GETTER_RESULT')
    try: return [int(payload[2+i*64:2+(i+1)*64],16) for i in range(n)]
    except ValueError: raise Halt('MALFORMED_GETTER_RESULT') from None


def tick(value):
    v=value & 0xffffff
    v=v-(1<<24) if v&(1<<23) else v
    if value != v % (1<<256) or not -887272<=v<=887272:
        raise Halt('INVALID_TICK')
    return v


def decode_getter(state, liq):
    w=decode_words(state,2)
    q=decode_words(liq,1)
    if len(q)!=1 or not 0<w[0]<(1<<160) or not 0<q[0]<(1<<128):
        raise Halt('INVALID_POOL_GETTER')
    return {'sqrt_price_x96':str(w[0]),'tick':tick(w[1]),'liquidity_raw':str(q[0])}


def event_decode(data):
    w=decode_words(data,5,5)
    if not 0<w[2]<(1<<160) or not 0<w[3]<(1<<128):
        raise Halt('MALFORMED_SWAP_EVENT')
    return {'sqrt_price_x96':str(w[2]),'tick':tick(w[4]),'liquidity_raw':str(w[3])}


def normalize_accounts(obj):
    if not isinstance(obj,dict) or len(obj)>10000: raise Halt('MALFORMED_BLOCK_TRACE')
    out={}
    for key,value in obj.items():
        k=check_hex(key,40)
        if k in out or not isinstance(value,dict): raise Halt('MALFORMED_BLOCK_TRACE')
        out[k]=value
    return out


def evaluate_trace(trace, transaction_hashes, target_ix, algebra, competitor):
    if not isinstance(trace,list) or len(trace)!=len(transaction_hashes):
        raise Halt('INCOMPLETE_BLOCK_TRACE')
    after_changes={'algebra':[],'uniswap_500':[]}
    target_has_slot_delta=False
    for idx,entry in enumerate(trace):
        if not isinstance(entry,dict) or check_hex(entry.get('txHash'),64)!=transaction_hashes[idx]:
            raise Halt('TRACE_TRANSACTION_ORDER_MISMATCH')
        if 'error' in entry: raise Halt('PARTIAL_BLOCK_TRACE')
        inner=entry.get('result')
        if not isinstance(inner,dict) or set(inner)!={'pre','post'}:
            raise Halt('MALFORMED_BLOCK_TRACE')
        pre,post=normalize_accounts(inner['pre']),normalize_accounts(inner['post'])
        if idx==target_ix:
            a=post.get(algebra,{}).get('storage',{})
            b=pre.get(algebra,{}).get('storage',{})
            target_has_slot_delta=bool(a or b)
        if idx>target_ix:
            for name,address in [('algebra',algebra),('uniswap_500',competitor)]:
                # Conservative: ANY account change disqualifies the getter
                # equivalence, including balances / code / non-price storage.
                if address in pre or address in post:
                    after_changes[name].append(idx)
    if not target_has_slot_delta:
        raise Halt('TARGET_TRACE_HAS_NO_STORAGE_DELTA')
    return after_changes


def load_anchor(path):
    doc=json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('schema_version')!=1 or doc.get('chain_id')!=137 or doc.get('evidence_kind')!='polygon_tx_boundary_getter_probe_research_only':
        raise Halt('BAD_INPUT_EVIDENCE')
    block=doc.get('block',{})
    tx=doc.get('transaction',{})
    if block.get('number')!=MAX_BLOCK or tx.get('index')!=34 or not doc.get('storage_diff',{}).get('has_target_pool_delta'):
        raise Halt('INPUT_ANCHOR_MISMATCH')
    b_hash=check_hex(block.get('hash'),64)
    tx_hash=check_hex(tx.get('hash'),64)
    pools=doc.get('pools',{})
    alg=check_hex(pools.get('algebra'),40)
    uni=check_hex(pools.get('uniswap_500'),40)
    if alg==uni: raise Halt('DUPLICATE_POOL_ADDRESSES')
    expected=tx.get('final_pool_swap_event')
    if not isinstance(expected,dict): raise Halt('BAD_INPUT_EVIDENCE')
    # Normalize expected fields for comparison later, with strict bound checks.
    if set(expected)!={'sqrt_price_x96','tick','liquidity_raw'}:
        raise Halt('BAD_INPUT_EVIDENCE')
    for key in ['sqrt_price_x96','liquidity_raw']:
        if not isinstance(expected[key],str) or not expected[key].isdecimal(): raise Halt('BAD_INPUT_EVIDENCE')
    if type(expected['tick']) is not int or not -887272<=expected['tick']<=887272:raise Halt('BAD_INPUT_EVIDENCE')
    return {'block_number':MAX_BLOCK,'block_hash':b_hash,'tx_hash':tx_hash,'tx_index':34,'algebra':alg,'uniswap':uni,'event':expected}


def perform(rpc,anchor):
    report={'schema_version':1,'study':'step11_9_bounded_block_diff_replay','block':anchor['block_number'],
        'tx_index':anchor['tx_index'],'verdict':'INTRABLOCK_EQUIVALENCE_UNPROVEN',
        'reason':None,'full_block_diff':False,'pool_changes_after_target':None,
        'target_event_matches_end_block':None,'counterparty_end_block_values':None,
        'complete_tick_state':False,'economic_profit_proven':False,
        'signing':False,'broadcast':False,'relay_submission':False,'rpc_calls':0}
    try:
        if int(rpc.call('eth_chainId',[]),16)!=137: raise Halt('WRONG_CHAIN')
        block=rpc.call('eth_getBlockByNumber',[hex(anchor['block_number']),False])
        if not isinstance(block,dict) or check_hex(block.get('hash'),64)!=anchor['block_hash']:
            raise Halt('BLOCK_HASH_MISMATCH')
        txs=block.get('transactions')
        if not isinstance(txs,list) or not 1<=len(txs)<=MAX_TXS:
            raise Halt('TRANSACTION_COUNT_UNSUPPORTED')
        txs=[check_hex(tx,64) for tx in txs]
        if txs[anchor['tx_index']]!=anchor['tx_hash']: raise Halt('TRANSACTION_INDEX_MISMATCH')
        receipt=rpc.call('eth_getTransactionReceipt',[anchor['tx_hash']])
        if not isinstance(receipt,dict) or check_hex(receipt.get('blockHash'),64)!=anchor['block_hash'] or receipt.get('status')!='0x1' or int(receipt.get('transactionIndex','-1'),16)!=anchor['tx_index']:
            raise Halt('RECEIPT_MISMATCH')
        logs=receipt.get('logs',[])
        if not isinstance(logs,list) or len(logs)>2000: raise Halt('MALFORMED_RECEIPT')
        # Use the final swap event for the anchor pool within the transaction,
        # checking against the independently saved Step 11.8 anchor.
        swaps=[]
        for log in logs:
            if not isinstance(log,dict):raise Halt('MALFORMED_RECEIPT')
            if (str(log.get('address','')).lower()==anchor['algebra'] and
                isinstance(log.get('topics'),list) and len(log['topics'])==3 and
                str(log['topics'][0]).lower()==TOPIC):
                data=log.get('data','')
                if isinstance(data,str) and len(data)==322:
                    try: swaps.append((int(log['logIndex'],16),event_decode(data)))
                    except (ValueError,TypeError):raise Halt('MALFORMED_SWAP_EVENT') from None
        if not swaps or max(swaps,key=lambda r:r[0])[1]!=anchor['event']:
            raise Halt('ANCHOR_EVENT_NOT_RECONFIRMED')
        # One full-block replay, no txIndex or custom tracers needed.
        traces=rpc.call('debug_traceBlockByNumber',[hex(anchor['block_number']),{
            'tracer':'prestateTracer','tracerConfig':{
                'diffMode':True,'disableCode':True,'disableStorage':False},'timeout':'12s'}])
        changes=evaluate_trace(traces,txs,anchor['tx_index'],anchor['algebra'],anchor['uniswap'])
        report['full_block_diff']=True
        report['pool_changes_after_target']=changes
        if any(changes.values()):
            raise Halt('LATER_POOL_ACCOUNT_CHANGES_DETECTED')
        at_block=hex(anchor['block_number'])
        def getter(to,data):
            value=rpc.call('eth_call',[{'to':to,'data':data},at_block])
            if not isinstance(value,str):raise Halt('MALFORMED_GETTER_RESULT')
            return value
        algebra=decode_getter(getter(anchor['algebra'],GLOBAL_STATE),getter(anchor['algebra'],LIQUIDITY))
        uniswap=decode_getter(getter(anchor['uniswap'],UNI_SLOT0),getter(anchor['uniswap'],LIQUIDITY))
        report['target_event_matches_end_block']=algebra==anchor['event']
        report['counterparty_end_block_values']=uniswap
        if not report['target_event_matches_end_block']:
            raise Halt('TARGET_POST_STATE_EVENT_MISMATCH')
        check=rpc.call('eth_getBlockByNumber',[at_block,False])
        if not isinstance(check,dict) or check_hex(check.get('hash'),64)!=anchor['block_hash']:
            raise Halt('BLOCK_HASH_CHANGED_DURING_PROBE')
        report['verdict']='CONDITIONAL_SAME_BOUNDARY_GETTER_EQUIVALENCE'
        report['reason']='Full block diff shows no later pool-account writes; post-state Algebra getters match the receipt Swap event. No full tick replay.'
    except Halt as e:
        report['reason']=e.reason
        if e.code is not None: report['rpc_error_code']=e.code
    finally:
        report['rpc_calls']=rpc.count
    return report


def assess(doc):
    if not isinstance(doc,dict) or doc.get('study')!='step11_9_bounded_block_diff_replay':raise Halt('BAD_REPORT')
    if doc.get('rpc_calls',MAX_REQUESTS+1)>MAX_REQUESTS or any(doc.get(k)!=False for k in ('complete_tick_state','economic_profit_proven','signing','broadcast','relay_submission')):
        raise Halt('UNSAFE_REPORT')
    if doc.get('verdict')=='CONDITIONAL_SAME_BOUNDARY_GETTER_EQUIVALENCE':
        if not doc.get('full_block_diff') or doc.get('pool_changes_after_target')!={'algebra':[],'uniswap_500':[]} or doc.get('target_event_matches_end_block')!=True or not isinstance(doc.get('counterparty_end_block_values'),dict):
            raise Halt('FORGED_POSITIVE_VERDICT')
    elif doc.get('verdict')!='INTRABLOCK_EQUIVALENCE_UNPROVEN':raise Halt('UNKNOWN_VERDICT')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='outputs/step11_8_boundary.json')
    parser.add_argument('--output',default='outputs/step11_9_block_diff.json')
    args=parser.parse_args()
    try:
        anchor=load_anchor(args.input)
        rpc=RPC(os.getenv('POLYGON_RPC_URL',''))
        report=perform(rpc,anchor)
        assess(report)
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf8')
        print('STEP 11.9 — BOUNDED HISTORICAL FULL-BLOCK DIFF (READ ONLY)')
        print('verdict='+report['verdict'])
        print('reason='+str(report['reason']))
        print('full_block_diff='+str(report['full_block_diff'])+' later_changes='+str(report['pool_changes_after_target']))
        print('rpc_calls='+str(report['rpc_calls'])+' (maximum 9); max response 12 MB; time budget 120s')
        print('EVIDENCE SAVED: '+str(output))
        print('NO QUOTES / NO SIGNING / NO BROADCAST')
    except (Halt,ValueError,KeyError,OSError) as e:
        print('STEP 11.9 FAIL-CLOSED:',e.reason if isinstance(e,Halt) else type(e).__name__,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
