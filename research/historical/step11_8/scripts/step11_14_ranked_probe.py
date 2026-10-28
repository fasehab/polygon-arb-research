#!/usr/bin/env python3
"""Step 11.14: second-ranked Algebra-swap investigation and Uniswap V3 intrablock spot fee-floor screening.

Read-only. One historical block trace maximum. Price-state comparison is not
an executable quote, net profit estimate, complete tick replay, or authorization.
Uses saved 11.8 pool identity evidence and installed Step 11.9 RPC helper.
"""
import argparse
from decimal import Decimal, getcontext
import json
import os
from pathlib import Path
import sys

from step11_9_block_diff_probe import (RPC, Halt, check_hex, load_anchor, normalize_accounts,
    decode_getter, event_decode, GLOBAL_STATE, LIQUIDITY, TOPIC, MAX_TXS)

getcontext().prec = 85
SLOT0 = 0
LIQUIDITY_SLOT = 4
TOKEN0 = '0x0dfe1681'
TOKEN1 = '0xd21220a7'
UNI_FEE = '0xddca3f43'
USDT = '0xc2132d05d31c914a87c6611c10748aeb04b58e8f'
WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
MAX_RPC = 20
MAX_LOGS = 750
MAX_BLOCK_WINDOW = 400
MAX_BLOCK_SPAN = 140000000
Q96 = 2**96
UNISWAP_FEE_UNITS = 500


def words(data, n=1):
    if not isinstance(data,str) or not data.startswith('0x') or len(data)!=2+64*n:
        raise Halt('BAD_GETTER_LENGTH')
    try: return [int(data[2+i*64:2+(i+1)*64],16) for i in range(n)]
    except ValueError: raise Halt('BAD_GETTER_HEX') from None


def parse_addr(data):
    w=words(data)[0]
    if w>=2**160 or w==0: raise Halt('BAD_TOKEN_IDENTITY')
    return '0x'+f'{w:040x}'


def slot_storage(accounts, pool, key):
    entry=accounts.get(pool, {})
    if not isinstance(entry,dict): raise Halt('INVALID_ACCOUNT_DIFF')
    d=entry.get('storage',{})
    if not isinstance(d,dict) or len(d)>10000: raise Halt('INVALID_STORAGE_DIFF')
    matches=[]
    for raw,value in d.items():
        if not isinstance(raw,str) or not raw.startswith('0x'):
            raise Halt('INVALID_STORAGE_KEY')
        try: number=int(raw,16)
        except ValueError:raise Halt('INVALID_STORAGE_KEY') from None
        if number>=2**256:raise Halt('INVALID_STORAGE_KEY')
        if number == key:
            if not isinstance(value,str) or not value.startswith('0x'):
                raise Halt('INVALID_STORAGE_VALUE')
            try: i=int(value,16)
            except ValueError:raise Halt('INVALID_STORAGE_VALUE') from None
            if not 0<=i<2**256:raise Halt('INVALID_STORAGE_VALUE')
            matches.append(i)
    if len(matches)>1:raise Halt('DUPLICATE_STORAGE_SLOT')
    return matches[0] if matches else None


def reverse_slot(current, pre, post, pool, key):
    """Reverse one geth prestateTracer diff; missing pre/post means zero/new/deleted.

    When a slot is untouched in both sides, current is unchanged. A missing
    pre-state value for a touched slot is zero; likewise a missing post value.
    """
    a=slot_storage(pre,pool,key);b=slot_storage(post,pool,key)
    if a is None and b is None:return current,False
    expected=0 if b is None else b
    if current != expected: raise Halt('TRACE_STORAGE_CHAIN_MISMATCH')
    return (0 if a is None else a), True


def unpack_uni_slot0(raw):
    if not isinstance(raw,int) or raw<0 or raw>=2**256:raise Halt('BAD_UNISWAP_SLOT0')
    sqrt=raw & ((1<<160)-1)
    t=(raw>>160) & ((1<<24)-1)
    if t & (1<<23):t-=1<<24
    if not 0<sqrt<2**160 or not -887272<=t<=887272:
        raise Halt('BAD_UNISWAP_SLOT0')
    return sqrt,t


def spot_bps(a,b):
    if not all(isinstance(x,int) and 0<x<2**160 for x in (a,b)):
        raise Halt('BAD_PRICE')
    ratio=(Decimal(a)/Decimal(b))**2
    advantage=max(ratio, Decimal(1)/ratio)
    spread=(advantage-1)*10000
    ceiling=(advantage*(1-Decimal(UNISWAP_FEE_UNITS)/1_000_000)-1)*10000
    return round(spread,8),round(ceiling,8)


def log_fields(log, anchor, lo, hi):
    if not isinstance(log,dict) or str(log.get('address','')).lower()!=anchor['algebra']:
        raise Halt('UNEXPECTED_LOG_ADDRESS')
    topics=log.get('topics')
    if not isinstance(topics,list) or len(topics)!=3 or str(topics[0]).lower()!=TOPIC:
        raise Halt('BAD_SWAP_TOPIC')
    for topic in topics:check_hex(topic,64)
    block=int(log.get('blockNumber','-1'),16)
    tx_idx=int(log.get('transactionIndex','-1'),16)
    log_idx=int(log.get('logIndex','-1'),16)
    if not lo<=block<=hi or tx_idx<0 or log_idx<0:
        raise Halt('LOG_OUT_OF_WINDOW')
    block_hash=check_hex(log.get('blockHash'),64)
    tx_hash=check_hex(log.get('transactionHash'),64)
    decoded=event_decode(log.get('data'))
    return {'block':block,'block_hash':block_hash,'tx_index':tx_idx,'log_index':log_idx,
            'tx_hash':tx_hash,'pool_price':int(decoded['sqrt_price_x96']),
            'event':decoded}


def rank_events(logs, anchor, lo, hi, baseline):
    if not isinstance(logs,list) or len(logs)>MAX_LOGS:raise Halt('EXCESS_SWAP_LOGS')
    decoded=[log_fields(row,anchor,lo,hi) for row in logs]
    if len(set((d['block'],d['log_index']) for d in decoded))!=len(decoded):
        raise Halt('DUPLICATE_SWAP_LOG')
    decoded.sort(key=lambda d:(d['block'],d['tx_index'],d['log_index']))
    # Aggregate events by transaction, selecting the FINAL swap in each tx.
    # The impact is from the previous transaction's ending price to this
    # transaction's ending price (not merely from its penultimate swap).
    unique={}
    for d in decoded:
        if d['pool_price']<=0:raise Halt('BAD_SWAP_PRICE')
        key=(d['block'],d['tx_index'])
        previous=unique.get(key)
        if previous and previous['tx_hash']!=d['tx_hash']:
            raise Halt('INCONSISTENT_TX_HASH_IN_EVENT_LOGS')
        unique[key]=d
    last=baseline; ranked=[]
    for key in sorted(unique):
        d=unique[key]
        impact,_=spot_bps(d['pool_price'],last)
        d['impact_bps']=str(impact)
        ranked.append(d)
        last=d['pool_price']
    return sorted(ranked,key=lambda d:(-Decimal(d['impact_bps']),d['block'],d['tx_index']))


def check_block_trace(trace, txs, target, anchor):
    """Validate ALL transaction trace entries and recover Uniswap slot0/liquidity at post-target boundary."""
    if not isinstance(trace,list) or len(trace)!=len(txs) or not 1<=len(txs)<=MAX_TXS:
        raise Halt('INCOMPLETE_BLOCK_TRACE')
    out=[]
    for i,entry in enumerate(trace):
        if not isinstance(entry,dict) or check_hex(entry.get('txHash'),64)!=txs[i]:
            raise Halt('TRACE_TX_ORDER_MISMATCH')
        if 'error' in entry:raise Halt('PARTIAL_BLOCK_TRACE')
        result=entry.get('result')
        if not isinstance(result,dict) or set(result)!={'pre','post'}:
            raise Halt('BAD_BLOCK_TRACE_ENTRY')
        out.append((normalize_accounts(result['pre']),normalize_accounts(result['post'])))
    if target['tx_index']>=len(txs) or txs[target['tx_index']]!=target['tx_hash']:
        raise Halt('TARGET_TX_MISMATCH')
    pre_t,post_t=out[target['tx_index']]
    if anchor['algebra'] not in pre_t and anchor['algebra'] not in post_t:
        raise Halt('TARGET_POOL_UNCHANGED')
    if not (pre_t.get(anchor['algebra'],{}).get('storage') or post_t.get(anchor['algebra'],{}).get('storage')):
        raise Halt('TARGET_POOL_NO_STORAGE_DELTA')
    for idx in range(target['tx_index']+1,len(out)):
        before,after=out[idx]
        if anchor['algebra'] in before or anchor['algebra'] in after:
            raise Halt('LATER_ALGEBRA_ACCOUNT_CHANGE')
    return out


def reconstruct_uniswap(trace_rows, target_ix, pool, end_slot0, end_liq):
    state0=end_slot0;liq=end_liq; changed=[]
    for idx in range(len(trace_rows)-1,target_ix,-1):
        pre,post=trace_rows[idx]
        if (pool in pre) != (pool in post):
            # Account creation/deletion cannot be safely reconstructed by this probe.
            raise Halt('COUNTERPARTY_ACCOUNT_LIFECYCLE')
        old0,changed0=reverse_slot(state0,pre,post,pool,SLOT0)
        old4,changed4=reverse_slot(liq,pre,post,pool,LIQUIDITY_SLOT)
        if changed0 or changed4:changed.append(idx)
        state0,liq=old0,old4
    price,tick=unpack_uni_slot0(state0)
    if not 0<liq<2**128:raise Halt('COUNTERPARTY_ZERO_OR_BAD_LIQUIDITY')
    return {'sqrt_price_x96':price,'tick':tick,'liquidity':liq,'rewound_tx_indices':sorted(changed)}


def run(rpc, anchor, lo, hi, min_bps, candidate_rank=2):
    if not (type(lo) is int and type(hi) is int and type(min_bps) is int and
            1<=lo<=hi<=MAX_BLOCK_SPAN and hi-lo+1<=MAX_BLOCK_WINDOW and 1<=min_bps<=10000 and type(candidate_rank) is int and 1<=candidate_rank<=2):
        raise Halt('INVALID_SCAN_WINDOW_OR_THRESHOLD')
    report={'schema_version':1,'study':'step11_13_algebra_intrablock_spot_screen','block_window':[lo,hi],
        'min_impact_bps':min_bps,'selected_swap':None,'observed_swap_logs':0,'qualified_swap_events':0,
        'max_observed_impact_bps':None,'historical_pool_states':None,'spot_spread_bps':None,
        'fee_only_ceiling_bps':None,'verdict':'UNPROVEN','reason':None,'rpc_calls':0,
        'block_trace_requests':0,'candidate_rank':candidate_rank,'qualified_top_two':[], 'actual_swap_quote':False,'tick_replay':False,
        'measured_gas':False,'profit_proven':False,'signing':False,'broadcast':False}
    try:
        if int(rpc.call('eth_chainId',[]),16)!=137:raise Halt('WRONG_CHAIN')
        # Price preceding the window. Event impacts use event-after vs prior state.
        # This is not a cross-DEX comparison and not a transaction-boundary quote.
        prev=decode_getter(rpc.call('eth_call',[{'to':anchor['algebra'],'data':GLOBAL_STATE},hex(lo-1)]),
                           rpc.call('eth_call',[{'to':anchor['algebra'],'data':LIQUIDITY},hex(lo-1)]))
        baseline=int(prev['sqrt_price_x96'])
        logs=rpc.call('eth_getLogs',[{'address':anchor['algebra'],'topics':[TOPIC],
                                      'fromBlock':hex(lo),'toBlock':hex(hi)}])
        ranked=rank_events(logs,anchor,lo,hi,baseline)
        report['observed_swap_logs']=len(logs)
        report['max_observed_impact_bps']=ranked[0]['impact_bps'] if ranked else None
        passed=[x for x in ranked if Decimal(x['impact_bps'])>=min_bps]
        report['qualified_swap_events']=len(passed)
        # Auditable ranking output contains identifiers only, no guessed economics.
        report['qualified_top_two']=[{
            'rank':i+1,'block':row['block'],'tx_index':row['tx_index'],
            'tx_hash':row['tx_hash'],'impact_bps':row['impact_bps']}
            for i,row in enumerate(passed[:2])]
        if not passed:
            report['verdict']='NO_HIGH_IMPACT_ALGEBRA_SWAP'
            report['reason']='All verified event-to-event impacts below configured threshold; no trace requested.'
            return report
        if candidate_rank>len(passed):
            report['verdict']='CANDIDATE_RANK_UNAVAILABLE'
            report['reason']='Requested ranked candidate does not exist; no trace requested.'
            return report
        target=passed[candidate_rank-1]
        report['selected_swap']={k:v for k,v in target.items() if k not in ('event','pool_price')}
        num=target['block']; at=hex(num)
        block=rpc.call('eth_getBlockByNumber',[at,False])
        if not isinstance(block,dict) or check_hex(block.get('hash'),64)!=target['block_hash']:
            raise Halt('BLOCK_HASH_MISMATCH')
        txs=block.get('transactions')
        if not isinstance(txs,list) or not 1<=len(txs)<=MAX_TXS:
            raise Halt('BAD_TX_LIST')
        txs=[check_hex(t,64) for t in txs]
        if target['tx_index']>=len(txs) or txs[target['tx_index']]!=target['tx_hash']:
            raise Halt('EVENT_TX_INDEX_MISMATCH')
        receipt=rpc.call('eth_getTransactionReceipt',[target['tx_hash']])
        if not isinstance(receipt,dict) or check_hex(receipt.get('blockHash'),64)!=target['block_hash'] or \
                check_hex(receipt.get('transactionHash'),64)!=target['tx_hash'] or \
                receipt.get('status')!='0x1' or int(receipt.get('transactionIndex','-1'),16)!=target['tx_index']:
            raise Halt('RECEIPT_MISMATCH')
        receipt_events=[]
        for log in receipt.get('logs',[]):
            if not isinstance(log,dict):raise Halt('BAD_RECEIPT_LOG')
            if str(log.get('address','')).lower()==anchor['algebra'] and isinstance(log.get('topics'),list) and \
                 log['topics'] and str(log['topics'][0]).lower()==TOPIC:
                d=event_decode(log.get('data'))
                receipt_events.append((int(log['logIndex'],16),d))
        if not receipt_events or len(set(i for i,_ in receipt_events))!=len(receipt_events):
            raise Halt('RECEIPT_SWAP_MISSING')
        last_index,last_event=max(receipt_events)
        if last_index!=target['log_index'] or last_event!=target['event']:
            raise Halt('TARGET_NOT_LAST_SWAP_IN_TX')
        # Same-token ordering and exact Uniswap 500 tier at the historical block.
        a0=parse_addr(rpc.call('eth_call',[{'to':anchor['algebra'],'data':TOKEN0},at]))
        a1=parse_addr(rpc.call('eth_call',[{'to':anchor['algebra'],'data':TOKEN1},at]))
        u0=parse_addr(rpc.call('eth_call',[{'to':anchor['uniswap'],'data':TOKEN0},at]))
        u1=parse_addr(rpc.call('eth_call',[{'to':anchor['uniswap'],'data':TOKEN1},at]))
        tier=words(rpc.call('eth_call',[{'to':anchor['uniswap'],'data':UNI_FEE},at]))[0]
        if (a0,a1)!=(u0,u1) or {a0,a1}!={USDT,WETH} or tier!=UNISWAP_FEE_UNITS:
            raise Halt('TOKEN_OR_FEE_MISMATCH')
        end_alg=decode_getter(rpc.call('eth_call',[{'to':anchor['algebra'],'data':GLOBAL_STATE},at]),
                               rpc.call('eth_call',[{'to':anchor['algebra'],'data':LIQUIDITY},at]))
        if end_alg!=last_event:raise Halt('ALGEBRA_EVENT_VS_BLOCK_END_MISMATCH')
        end_slot0=int(rpc.call('eth_getStorageAt',[anchor['uniswap'],'0x0',at]),16)
        end_liq=int(rpc.call('eth_getStorageAt',[anchor['uniswap'],'0x4',at]),16)
        if not 0<=end_slot0<2**256 or not 0<=end_liq<2**128:
            raise Halt('INVALID_COUNTERPARTY_END_STATE')
        report['block_trace_requests']=1
        trace=rpc.call('debug_traceBlockByNumber',[at,{'tracer':'prestateTracer',
              'tracerConfig':{'diffMode':True,'disableCode':True,'disableStorage':False},'timeout':'12s'}])
        checked=check_block_trace(trace,txs,target,anchor)
        uni=reconstruct_uniswap(checked,target['tx_index'],anchor['uniswap'],end_slot0,end_liq)
        # Recheck block hash, but never treat raw RPC JSON as a signed chain proof.
        check=rpc.call('eth_getBlockByNumber',[at,False])
        if not isinstance(check,dict) or check_hex(check.get('hash'),64)!=target['block_hash']:
            raise Halt('BLOCK_HASH_CHANGED')
        gross,netceil=spot_bps(int(last_event['sqrt_price_x96']),uni['sqrt_price_x96'])
        report['historical_pool_states']={'algebra_post_target':last_event,
            'uniswap_post_target':{'sqrt_price_x96':str(uni['sqrt_price_x96']),
                  'tick':uni['tick'],'liquidity_raw':str(uni['liquidity']),
                  'rewound_tx_indices':uni['rewound_tx_indices']}}
        report['spot_spread_bps']=str(gross)
        report['fee_only_ceiling_bps']=str(netceil)
        if netceil<=0:
            report['verdict']='REJECT_UNISWAP_FEE_FLOOR'
            report['reason']='Best direction cannot recover even known Uniswap V3 fee. Algebra fee, slippage, flash fee and gas excluded.'
        else:
            report['verdict']='ABOVE_ONE_LEG_FEE_ONLY_FULL_REPLAY_REQUIRED'
            report['reason']='Spot discrepancy survives only known Uniswap fee; NOT swap output, realized net profit, or executable opportunity.'
    except Halt as exc:
        report['verdict']='UNPROVEN'
        report['reason']=exc.reason
        if exc.code is not None:report['rpc_error_code']=exc.code
    finally:
        report['rpc_calls']=rpc.count
    return report


def assess(report):
    if not isinstance(report,dict) or report.get('study')!='step11_13_algebra_intrablock_spot_screen' or \
         report.get('schema_version')!=1:
        raise Halt('BAD_REPORT')
    if type(report.get('rpc_calls')) is not int or not 0<=report['rpc_calls']<=MAX_RPC or \
            type(report.get('block_trace_requests')) is not int or not 0<=report['block_trace_requests']<=1:
        raise Halt('REPORT_BUDGET_VIOLATION')
    for key in ('actual_swap_quote','tick_replay','measured_gas','profit_proven','signing','broadcast'):
        if report.get(key) is not False:raise Halt('UNSAFE_REPORT_CLAIM')
    rank=report.get('candidate_rank')
    if type(rank) is not int or rank not in (1,2):raise Halt('BAD_CANDIDATE_RANK')
    shortlist=report.get('qualified_top_two')
    count=report.get('qualified_swap_events')
    if not isinstance(shortlist,list) or len(shortlist)!=min(2,count) or not 0<=count<=MAX_LOGS:
        raise Halt('INVALID_RANKING_SUMMARY')
    for i,row in enumerate(shortlist):
        if not isinstance(row,dict) or row.get('rank')!=i+1 or not isinstance(row.get('impact_bps'),str) or \
               not isinstance(row.get('tx_index'),int) or not isinstance(row.get('block'),int):
            raise Halt('INVALID_RANKING_SUMMARY')
    v=report.get('verdict')
    if v in ('REJECT_UNISWAP_FEE_FLOOR','ABOVE_ONE_LEG_FEE_ONLY_FULL_REPLAY_REQUIRED'):
        selected=report.get('selected_swap')
        if not isinstance(selected,dict) or rank>len(shortlist):
            raise Halt('SELECTED_RANK_NOT_IN_REPORT')
        expected=shortlist[rank-1]
        if any(selected.get(key)!=expected.get(key) for key in ('block','tx_index','tx_hash','impact_bps')):
            raise Halt('SELECTED_RANK_MISMATCH')
        if report.get('block_trace_requests')!=1 or not isinstance(report.get('historical_pool_states'),dict):
            raise Halt('FABRICATED_RECONSTRUCTION')
        alg=report['historical_pool_states'].get('algebra_post_target',{})
        uni=report['historical_pool_states'].get('uniswap_post_target',{})
        try:
            spread,ceiling=spot_bps(int(alg['sqrt_price_x96']),int(uni['sqrt_price_x96']))
        except (KeyError,TypeError,ValueError):raise Halt('BAD_POOL_PRICE_PROOF') from None
        if str(spread)!=report.get('spot_spread_bps') or str(ceiling)!=report.get('fee_only_ceiling_bps'):
            raise Halt('FORGED_SPOT_ECONOMICS')
        if (ceiling<=0)!=(v=='REJECT_UNISWAP_FEE_FLOOR'):
            raise Halt('FORGED_FEE_VERDICT')
    elif v=='NO_HIGH_IMPACT_ALGEBRA_SWAP':
        if report.get('block_trace_requests')!=0 or report.get('historical_pool_states') is not None or \
              count!=0 or report.get('selected_swap') is not None:
            raise Halt('FORGED_NO_EVENT')
    elif v=='CANDIDATE_RANK_UNAVAILABLE':
        if count>=rank or report.get('selected_swap') is not None or \
                report.get('historical_pool_states') is not None or report.get('block_trace_requests')!=0:
            raise Halt('FORGED_UNAVAILABLE_RANK')
    elif v!='UNPROVEN':raise Halt('UNKNOWN_VERDICT')
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--anchor',default='outputs/step11_8_boundary.json')
    parser.add_argument('--from-block',type=int,default=96179600)
    parser.add_argument('--to-block',type=int,default=96179799)
    parser.add_argument('--min-impact-bps',type=int,default=4)
    parser.add_argument('--candidate-rank',type=int,choices=(1,2),default=2)
    parser.add_argument('--output',default='outputs/step11_14_second_candidate.json')
    args=parser.parse_args()
    try:
        anchor=load_anchor(args.anchor)
        rpc=RPC(os.getenv('POLYGON_RPC_URL',''),max_requests=MAX_RPC)
        result=run(rpc,anchor,args.from_block,args.to_block,args.min_impact_bps,args.candidate_rank)
        assess(result)
        out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_name(out.name+'.tmp')
        tmp.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        tmp.replace(out)
        print('STEP 11.14 — RANKED ALGEBRA INTRABLOCK SPOT SCREEN (READ ONLY)')
        print('candidate_rank='+str(result['candidate_rank'])+' qualified_top_two='+str(result['qualified_top_two']))
        print('verdict='+result['verdict']+' reason='+str(result['reason']))
        print('observed_swap_logs='+str(result['observed_swap_logs'])+' qualified='+str(result['qualified_swap_events'])+' max_impact_bps='+str(result['max_observed_impact_bps']))
        print('selected='+str(result['selected_swap']))
        print('spot_spread_bps='+str(result['spot_spread_bps'])+' fee_only_ceiling_bps='+str(result['fee_only_ceiling_bps']))
        print('rpc_calls='+str(result['rpc_calls'])+' / '+str(MAX_RPC)+' block_traces='+str(result['block_trace_requests']))
        print('EVIDENCE SAVED:',out)
        print('ALWAYS BLOCK: NO V3 QUOTES / NO FULL TICK REPLAY / NO EXECUTION / NO SIGNING / NO BROADCAST')
    except (Halt,ValueError,KeyError,TypeError,OSError) as exc:
        print('STEP 11.13 FAIL-CLOSED:',exc.reason if isinstance(exc,Halt) else type(exc).__name__,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
