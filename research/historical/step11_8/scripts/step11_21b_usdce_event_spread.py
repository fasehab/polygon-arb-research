#!/usr/bin/env python3
"""11.21B: USDC.e/WETH Algebra vs Uniswap 500 historical event screening, one optional intrablock trace.

READ-ONLY. Event-derived ranks are HEURISTICS, not proven same-boundary spreads.
A completed trace proves only spot/fee-screen consistency, not full-tick execution.
Requires existing Step 11.9 and 11.13 scripts; does not overwrite them.
This scanner is configured exclusively for the verified USDC.e/WETH pair.
"""
import argparse
from collections import defaultdict
from decimal import Decimal, getcontext
import json
import os
from pathlib import Path
import sys

from step11_9_block_diff_probe import (
    RPC, Halt, check_hex, load_anchor, normalize_accounts, decode_words,
    decode_getter, event_decode, GLOBAL_STATE, LIQUIDITY, UNI_SLOT0,
    TOPIC, MAX_TXS,
)
from step11_13_impact_probe import (
    UNISWAP_FEE_UNITS, WETH, TOKEN0, TOKEN1, UNI_FEE,
    parse_addr, words, unpack_uni_slot0, reconstruct_uniswap,
)

getcontext().prec = 90
MAX_RPC = 30
MAX_BLOCKS = 200
MAX_LOGS = 1000
MAX_BOUND = 140_000_000
MAX_SCAN_BPS = 1000
DEFAULT_MIN_BPS = Decimal('9')
USDCe = '0x2791bca1f2de4661ed88a30c99a7a9449aa84174'
ALGEBRA_POOL = '0x55caabb0d2b704fd0ef8192a7e35d8837e678207'
UNISWAP_POOL = '0x45dda9cb7c25131df268515131f647d726f50608'
POOL_ANCHOR = {'algebra': ALGEBRA_POOL, 'uniswap': UNISWAP_POOL}



def spread_bps(x, y):
    if not all(type(i) is int and 0 < i < 2**160 for i in (x, y)):
        raise Halt('BAD_SPOT_PRICE')
    ratio = (Decimal(x) / Decimal(y)) ** 2
    return (max(ratio, 1 / ratio) - 1) * 10000


def full_fee_ceiling_bps(x, y, algebra_fee_units):
    """Optimistic best-direction mathematical spot ceiling after TWO swap fees.

    This is NOT a swap quote: no impact, fees changing mid-swap, flash loan, gas.
    """
    if type(algebra_fee_units) is not int or not 0 <= algebra_fee_units < 1_000_000:
        raise Halt('ALGEBRA_FEE_UNVERIFIED')
    ratio = (Decimal(x) / Decimal(y)) ** 2
    advantage = max(ratio, 1 / ratio)
    remain = ((1 - Decimal(algebra_fee_units)/1_000_000)
              * (1 - Decimal(UNISWAP_FEE_UNITS)/1_000_000))
    return (advantage * remain - 1) * 10000


def algebra_fee_hint(raw):
    """Conservative report hint across documented Algebra globalState ABI layouts.

    The value is NOT verified effective quote fee. Dynamic fee plugins can change
    it during a hypothetical swap, so NEVER use it as a hard rejection proof.
    """
    w=decode_words(raw,3)
    if len(w)==6:  # Integral (price,tick,lastFee,pluginConfig,communityFee,unlocked)
        vals=[w[2]]
        abi='integral_6word_lastFee'
    elif len(w)==7:  # AMBIGUOUS: Algebra v1 single fee OR v2 directional
        # Using first fee is only a hint, NEVER a hard rejection.
        vals=[w[2]]
        abi='7word_ambiguous_fee_or_directional'
    elif len(w)==8:  # Algebra v1.9 directional variant
        vals=[w[2],w[3]]
        abi='directional_8word_minFee'
    else:
        raise Halt('UNKNOWN_ALGEBRA_FEE_ABI')
    if any(type(v) is not int or not 0<=v<=65535 for v in vals):
        raise Halt('BAD_ALGEBRA_FEE_HINT')
    return min(vals),abi


def log_value(log, anchor, lo, hi):
    if not isinstance(log, dict):
        raise Halt('MALFORMED_SWAP_LOG')
    pool = check_hex(log.get('address'), 40)
    if pool == anchor['algebra']:
        venue = 'algebra'
    elif pool == anchor['uniswap']:
        venue = 'uniswap_500'
    else:
        raise Halt('UNKNOWN_POOL_IN_LOG')
    topics = log.get('topics')
    if (not isinstance(topics, list) or len(topics) != 3
            or str(topics[0]).lower() != TOPIC):
        raise Halt('INVALID_SWAP_TOPIC')
    for topic in topics:
        check_hex(topic, 64)
    try:
        block = int(log['blockNumber'], 16)
        ix = int(log['transactionIndex'], 16)
        li = int(log['logIndex'], 16)
    except (KeyError, ValueError, TypeError):
        raise Halt('BAD_SWAP_LOG_INDEX') from None
    if not lo <= block <= hi or not 0 <= ix < MAX_TXS or li < 0:
        raise Halt('SWAP_LOG_OUT_OF_RANGE')
    ev = event_decode(log.get('data'))
    return {
        'venue':venue, 'block':block,'block_hash':check_hex(log.get('blockHash'),64),
        'tx_index':ix,'tx_hash':check_hex(log.get('transactionHash'),64),
        'log_index':li, 'sqrt_price_x96':int(ev['sqrt_price_x96']),
        'event':ev,
    }


def discover(logs, anchor, lo, hi, algebra_baseline, uniswap_baseline, min_bps):
    """Event-price heuristic, aggregated at transaction END, for both pools.

    Price updates from Mint/Burn/other writes are not independently replayed here.
    Any candidate MUST pass full-block reconstruction before a fee comparison.
    """
    if not isinstance(logs, list) or len(logs) > MAX_LOGS:
        raise Halt('LOG_BUDGET_EXCEEDED')
    parsed = [log_value(v, anchor, lo, hi) for v in logs]
    parsed.sort(key=lambda p: (p['block'], p['tx_index'], p['log_index']))
    seen_logs=set();groups=defaultdict(list)
    for row in parsed:
        logid=(row['block'],row['log_index'])
        if logid in seen_logs:
            raise Halt('DUPLICATE_OR_COLLIDING_LOG_INDEX')
        seen_logs.add(logid)
        groups[(row['block'],row['tx_index'])].append(row)
    prices={'algebra':algebra_baseline,'uniswap_500':uniswap_baseline}
    ranked=[];skipped_both=0
    for (block, txix), rows in sorted(groups.items()):
        venues={r['venue'] for r in rows}
        for r in rows:
            prices[r['venue']]=r['sqrt_price_x96']
        if len(venues)!=1:
            # Both venues touched within the SAME tx; would need special replay.
            skipped_both += 1
            continue
        item=rows[-1].copy()
        if any(r['tx_hash']!=item['tx_hash'] or r['block_hash']!=item['block_hash'] for r in rows):
            raise Halt('INCONSISTENT_LOG_GROUP')
        sp=spread_bps(prices['algebra'],prices['uniswap_500'])
        if sp >= min_bps:
            item['heuristic_spread_bps']=format(sp,'.8f')
            item['heuristic_algebra_price']=str(prices['algebra'])
            item['heuristic_uniswap_price']=str(prices['uniswap_500'])
            ranked.append(item)
    ranked.sort(key=lambda p:(-Decimal(p['heuristic_spread_bps']),p['block'],p['tx_index']))
    return ranked,{'swap_logs':len(parsed),'transaction_groups':len(groups),
                   'skipped_both_pool_transactions':skipped_both}


def check_trace(trace, hashes, target):
    if (not isinstance(trace, list) or len(trace)!=len(hashes)
            or not 1<=len(hashes)<=MAX_TXS):
        raise Halt('INCOMPLETE_BLOCK_TRACE')
    rows=[]
    for i, item in enumerate(trace):
        if (not isinstance(item,dict) or check_hex(item.get('txHash'),64)!=hashes[i]
                or 'error' in item):
            raise Halt('TRACE_HASH_OR_ORDER_MISMATCH')
        diff=item.get('result')
        if not isinstance(diff,dict) or set(diff)!={'pre','post'}:
            raise Halt('MALFORMED_TRACE_ENTRY')
        rows.append((normalize_accounts(diff['pre']),normalize_accounts(diff['post'])))
    if target['tx_index']>=len(hashes) or hashes[target['tx_index']]!=target['tx_hash']:
        raise Halt('TARGET_TX_INDEX_MISMATCH')
    return rows


def verify_target_receipt(receipt, target, anchor):
    if (not isinstance(receipt,dict)
            or check_hex(receipt.get('transactionHash'),64)!=target['tx_hash']
            or check_hex(receipt.get('blockHash'),64)!=target['block_hash']
            or receipt.get('status')!='0x1'
            or int(receipt.get('transactionIndex','-1'),16)!=target['tx_index']):
        raise Halt('RECEIPT_MISMATCH')
    pool = anchor['algebra'] if target['venue']=='algebra' else anchor['uniswap']
    matches=[]
    for row in receipt.get('logs',[]):
        if not isinstance(row,dict):raise Halt('BAD_RECEIPT_LOG')
        if (str(row.get('address','')).lower()==pool and isinstance(row.get('topics'),list)
                and len(row['topics'])==3 and str(row['topics'][0]).lower()==TOPIC):
            matches.append((int(row['logIndex'],16),event_decode(row['data'])))
    if not matches or max(matches)[0]!=target['log_index'] or max(matches)[1]!=target['event']:
        raise Halt('FINAL_SWAP_EVENT_MISMATCH')
    # Skip simultaneous opposite-pool Swap events within the same transaction:
    # a post-tx boundary is reconstructible, but event-triggered single-venue
    # assumptions would be ambiguous.
    other=anchor['uniswap'] if target['venue']=='algebra' else anchor['algebra']
    for row in receipt.get('logs',[]):
        if (str(row.get('address','')).lower()==other and isinstance(row.get('topics'),list)
                and row['topics'] and str(row['topics'][0]).lower()==TOPIC):
            raise Halt('BOTH_POOLS_SWAPPED_IN_TARGET_TX')


def reconstruct(rpc, anchor, target, mark_trace=lambda:None):
    at=hex(target['block'])
    block=rpc.call('eth_getBlockByNumber',[at,False])
    if not isinstance(block,dict) or check_hex(block.get('hash'),64)!=target['block_hash']:
        raise Halt('TARGET_BLOCK_HASH_MISMATCH')
    hashes=block.get('transactions')
    if not isinstance(hashes,list) or not 1<=len(hashes)<=MAX_TXS:
        raise Halt('BAD_BLOCK_TX_ARRAY')
    hashes=[check_hex(x,64) for x in hashes]
    if target['tx_index']>=len(hashes) or hashes[target['tx_index']]!=target['tx_hash']:
        raise Halt('TARGET_BLOCK_TX_MISMATCH')
    receipt=rpc.call('eth_getTransactionReceipt',[target['tx_hash']])
    verify_target_receipt(receipt,target,anchor)
    # Validate token pair and known fixed Uniswap fee on same historical block.
    ids=[]
    for pool in (anchor['algebra'],anchor['uniswap']):
        ids.append(tuple(parse_addr(rpc.call('eth_call',[{'to':pool,'data':selector},at]))
                         for selector in (TOKEN0,TOKEN1)))
    if ids[0]!=ids[1] or set(ids[0])!={USDCe,WETH}:
        raise Halt('TOKEN_IDENTITY_OR_ORDER_MISMATCH')
    if words(rpc.call('eth_call',[{'to':anchor['uniswap'],'data':UNI_FEE},at]))[0]!=500:
        raise Halt('UNISWAP_FEE_TIER_MISMATCH')
    alg_raw=rpc.call('eth_call',[{'to':anchor['algebra'],'data':GLOBAL_STATE},at])
    alg_liq=rpc.call('eth_call',[{'to':anchor['algebra'],'data':LIQUIDITY},at])
    alg=decode_getter(alg_raw,alg_liq)
    fee,fee_layout=algebra_fee_hint(alg_raw)
    end_slot0=int(rpc.call('eth_getStorageAt',[anchor['uniswap'],'0x0',at]),16)
    end_liq=int(rpc.call('eth_getStorageAt',[anchor['uniswap'],'0x4',at]),16)
    if not 0 <= end_slot0 < 2**256 or not 0<end_liq<2**128:
        raise Halt('BAD_UNISWAP_END_STORAGE')
    mark_trace()
    trace=rpc.call('debug_traceBlockByNumber',[at,{'tracer':'prestateTracer',
        'tracerConfig':{'diffMode':True,'disableCode':True,'disableStorage':False},
        'timeout':'12s'}])
    rows=check_trace(trace,hashes,target)
    target_pool=anchor['algebra'] if target['venue']=='algebra' else anchor['uniswap']
    pre,post=rows[target['tx_index']]
    if not (pre.get(target_pool,{}).get('storage') or post.get(target_pool,{}).get('storage')):
        raise Halt('TARGET_POOL_STORAGE_UNCHANGED')
    # GlobalState/fee end-block equivalence only allowed when Algebra account
    # has *no* later changes of any kind. Conservative by design.
    if any(anchor['algebra'] in a or anchor['algebra'] in b
           for a,b in rows[target['tx_index']+1:]):
        raise Halt('LATER_ALGEBRA_ACCOUNT_CHANGE_FEE_UNVERIFIED')
    uni=reconstruct_uniswap(rows,target['tx_index'],anchor['uniswap'],end_slot0,end_liq)
    ev=target['event']
    if target['venue']=='algebra':
        if alg!=ev:raise Halt('ALGEBRA_EVENT_VS_POST_STATE_MISMATCH')
    else:
        if (uni['sqrt_price_x96']!=int(ev['sqrt_price_x96']) or uni['tick']!=ev['tick']
                or uni['liquidity']!=int(ev['liquidity_raw'])):
            raise Halt('UNISWAP_EVENT_VS_POST_STATE_MISMATCH')
    cross=spread_bps(int(alg['sqrt_price_x96']),uni['sqrt_price_x96'])
    ceiling=full_fee_ceiling_bps(int(alg['sqrt_price_x96']),uni['sqrt_price_x96'],fee)
    one_leg=full_fee_ceiling_bps(int(alg['sqrt_price_x96']),uni['sqrt_price_x96'],0)
    last=rpc.call('eth_getBlockByNumber',[at,False])
    if not isinstance(last,dict) or check_hex(last.get('hash'),64)!=target['block_hash']:
        raise Halt('BLOCK_CHANGED_DURING_REPLAY')
    return {'algebra_post_tx':alg,'uniswap_post_tx':{
        'sqrt_price_x96':str(uni['sqrt_price_x96']),'tick':uni['tick'],
        'liquidity_raw':str(uni['liquidity']),
        'rewound_tx_indices':uni['rewound_tx_indices']},
        'algebra_fee_hint_units':fee,
        'algebra_fee_hint_layout':fee_layout,
        'algebra_effective_fee_verified':False,
        'uniswap_fee_units':500,
        'boundary_spread_bps':format(cross,'.8f'),
        'known_uniswap_fee_only_ceiling_bps':format(one_leg,'.8f'),
        'indicative_two_leg_fee_ceiling_bps':format(ceiling,'.8f')}


def run(rpc, anchor, lo, hi, min_bps=DEFAULT_MIN_BPS):
    if not (type(lo) is int and type(hi) is int and 0 < lo <= hi <= MAX_BOUND
            and hi-lo+1<=MAX_BLOCKS and isinstance(min_bps,Decimal)
            and Decimal('0')<min_bps<=MAX_SCAN_BPS):
        raise Halt('INVALID_SCAN_PARAMETERS')
    report={'schema_version':1,'step':'11.21B',
        'method':'EVENT_CROSS_POOL_HEURISTIC_THEN_FULL_BLOCK_TRACE',
        'window':[lo,hi],'min_heuristic_spread_bps':str(min_bps),
        'pair':'USDCe_WETH', 'algebra_pool':ALGEBRA_POOL,
        'uniswap_500_pool':UNISWAP_POOL,
        'max_event_heuristic_spread_bps':None,'top_five_event_heuristics':[],
        'candidate':None,'heuristic_candidate_count':0,'activity':None,
        'verified_boundary':None,'verdict':'UNPROVEN','reason':None,
        'rpc_calls':0,'max_rpc_calls':MAX_RPC,'block_traces':0,
        'historical_exact_quotes':False,'verified_100_usdt_depth':False,
        'measured_gas':False,'profit_proven':False,
        'signing':False,'broadcast':False}
    try:
        if int(rpc.call('eth_chainId',[]),16)!=137:raise Halt('WRONG_CHAIN')
        prior=hex(lo-1)
        astate=rpc.call('eth_call',[{'to':anchor['algebra'],'data':GLOBAL_STATE},prior])
        abase=decode_words(astate,2)[0]
        ustate=rpc.call('eth_call',[{'to':anchor['uniswap'],'data':UNI_SLOT0},prior])
        ubase=decode_words(ustate,2)[0]
        # Independent identity/fee preflight is mandatory even when no swap qualifies.
        ids=[]
        for pool in (anchor['algebra'],anchor['uniswap']):
            ids.append(tuple(parse_addr(rpc.call('eth_call',[
                {'to':pool,'data':selector},hex(hi)]))
                for selector in (TOKEN0,TOKEN1)))
        if ids[0]!=ids[1] or ids[0]!=(USDCe,WETH):
            raise Halt('HISTORICAL_USDCE_WETH_POOL_IDENTITY_MISMATCH')
        if words(rpc.call('eth_call',[
                {'to':anchor['uniswap'],'data':UNI_FEE},hex(hi)]))[0]!=500:
            raise Halt('HISTORICAL_UNISWAP_FEE_MISMATCH')
        logs=rpc.call('eth_getLogs',[{'address':[anchor['algebra'],anchor['uniswap']],
            'topics':[TOPIC],'fromBlock':hex(lo),'toBlock':hex(hi)}])
        full_rank,activity=discover(logs,anchor,lo,hi,abase,ubase,Decimal('0.00000001'))
        ranked=[row for row in full_rank if Decimal(row['heuristic_spread_bps'])>=min_bps]
        report['activity']=activity
        report['heuristic_candidate_count']=len(ranked)
        if full_rank:
            report['max_event_heuristic_spread_bps']=full_rank[0]['heuristic_spread_bps']
        report['top_five_event_heuristics']=[{
            key:row[key] for key in ('block','tx_index','venue','tx_hash','heuristic_spread_bps')
        } for row in full_rank[:5]]
        if not ranked:
            report['verdict']='NO_EVENT_HEURISTIC_ABOVE_THRESHOLD'
            report['reason']='No candidate passed event-derived cross-pool spread filter; this is not a market-wide no-go.'
            return report
        target=ranked[0]
        report['candidate']={k:v for k,v in target.items() if k not in ('event','sqrt_price_x96')}
        verified=reconstruct(rpc,anchor,target,lambda:report.__setitem__('block_traces',1))
        report['verified_boundary']=verified
        if Decimal(verified['known_uniswap_fee_only_ceiling_bps'])<=0:
            report['verdict']='REJECT_KNOWN_UNISWAP_FEE_FLOOR'
            report['reason']='Fails even known Uniswap 5bp fee; Algebra fee and impact excluded.'
        elif Decimal(verified['indicative_two_leg_fee_ceiling_bps'])<=0:
            report['verdict']='BELOW_INDICATIVE_TWO_LEG_FEES_UNPROVEN'
            report['reason']='Fails fee hint, but actual Algebra fee can be dynamic/directional. Not a hard rejection.'
        else:
            report['verdict']='ABOVE_REPORTED_FEES_NEEDS_TICK_REPLAY'
            report['reason']='Spot passes indicative fees only; $100 depth, tick-aware execution and inclusion unverified.'
    except Halt as e:
        report['verdict']='UNPROVEN'
        report['reason']=e.reason
        if e.code is not None:report['rpc_error_code']=e.code
    finally:
        report['rpc_calls']=rpc.count
    return report


def assess(report):
    if not isinstance(report,dict) or report.get('schema_version')!=1 or report.get('step')!='11.21B':
        raise Halt('INVALID_REPORT')
    if (report.get('pair')!='USDCe_WETH'
            or report.get('algebra_pool')!=ALGEBRA_POOL
            or report.get('uniswap_500_pool')!=UNISWAP_POOL):
        raise Halt('REPORT_WRONG_POOL_IDENTITY')
    top=report.get('top_five_event_heuristics')
    if not isinstance(top,list) or len(top)>5:raise Halt('MALFORMED_TOP_FIVE')
    if top:
        if report.get('max_event_heuristic_spread_bps')!=top[0].get('heuristic_spread_bps'):
            raise Halt('MALFORMED_MAX_SPREAD')
        if any(Decimal(row['heuristic_spread_bps'])<0 for row in top):
            raise Halt('BAD_HEURISTIC_SPREAD')
        if [Decimal(row['heuristic_spread_bps']) for row in top]!=sorted(
            [Decimal(row['heuristic_spread_bps']) for row in top],reverse=True):
            raise Halt('HEURISTIC_RANK_ORDER_INVALID')
    if (type(report.get('rpc_calls')) is not int or not 0<=report['rpc_calls']<=MAX_RPC
            or report.get('max_rpc_calls')!=MAX_RPC or report.get('block_traces') not in (0,1)):
        raise Halt('REPORT_BUDGET_INCONSISTENT')
    for flag in ('historical_exact_quotes','verified_100_usdt_depth','measured_gas',
                 'profit_proven','signing','broadcast'):
        if report.get(flag) is not False:raise Halt('UNSAFE_SUCCESS_CLAIM')
    v=report.get('verdict')
    if v in ('REJECT_KNOWN_UNISWAP_FEE_FLOOR','BELOW_INDICATIVE_TWO_LEG_FEES_UNPROVEN',
             'ABOVE_REPORTED_FEES_NEEDS_TICK_REPLAY'):
        if report['block_traces']!=1 or not isinstance(report.get('verified_boundary'),dict):
            raise Halt('FORGED_BOUNDARY_RESULT')
        b=report['verified_boundary']
        try:
            alg=int(b['algebra_post_tx']['sqrt_price_x96'])
            uni=int(b['uniswap_post_tx']['sqrt_price_x96'])
            fee=int(b['algebra_fee_hint_units'])
            spot=format(spread_bps(alg,uni),'.8f')
            ceil=format(full_fee_ceiling_bps(alg,uni,fee),'.8f')
            one=format(full_fee_ceiling_bps(alg,uni,0),'.8f')
        except (KeyError,TypeError,ValueError):raise Halt('INVALID_BOUNDARY_VALUES') from None
        if (b['boundary_spread_bps']!=spot or b['indicative_two_leg_fee_ceiling_bps']!=ceil
                or b['known_uniswap_fee_only_ceiling_bps']!=one
                or b.get('algebra_effective_fee_verified') is not False):
            raise Halt('FORGED_FEE_ECONOMICS')
        expected = ('REJECT_KNOWN_UNISWAP_FEE_FLOOR' if Decimal(one)<=0 else
                    'BELOW_INDICATIVE_TWO_LEG_FEES_UNPROVEN' if Decimal(ceil)<=0 else
                    'ABOVE_REPORTED_FEES_NEEDS_TICK_REPLAY')
        if v!=expected:raise Halt('INCORRECT_FEE_VERDICT')
    elif v=='NO_EVENT_HEURISTIC_ABOVE_THRESHOLD':
        if report['block_traces']!=0 or report['candidate'] is not None:
            raise Halt('FALSE_NO_CANDIDATE')
    elif v!='UNPROVEN':raise Halt('UNKNOWN_VERDICT')
    return True


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--from-block',type=int,default=96179600)
    ap.add_argument('--to-block',type=int,default=96179799)
    ap.add_argument('--min-spread-bps',type=Decimal,default=DEFAULT_MIN_BPS)
    ap.add_argument('--output',default='outputs/step11_21b_usdce_event_spread.json')
    args=ap.parse_args()
    try:
        anchor=POOL_ANCHOR.copy()
        rpc=RPC(os.environ.get('POLYGON_RPC_URL',''),max_requests=MAX_RPC)
        report=run(rpc,anchor,args.from_block,args.to_block,args.min_spread_bps)
        assess(report)
        dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
        tmp=dest.with_name(dest.name+'.tmp')
        tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        tmp.replace(dest)
        print('STEP 11.21B — USDC.e/WETH CROSS-POOL EVENT SCREEN — READ ONLY')
        print('verdict:',report['verdict'],'reason:',report['reason'])
        print('activity:',report['activity'])
        print('heuristic_candidates:',report['heuristic_candidate_count'])
        print('maximum_heuristic_spread_bps:', report['max_event_heuristic_spread_bps'])
        print('top_five_events:',report['top_five_event_heuristics'])
        print('selected:',report['candidate'])
        print('verified_boundary:',report['verified_boundary'])
        print('rpc_calls:',report['rpc_calls'],'/',MAX_RPC,'block_traces:',report['block_traces'])
        print('saved:',dest)
        print('NO EXECUTABLE QUOTES / NO $100 DEPTH PROOF / NO GAS / NO SIGNING OR BROADCAST')
    except (Halt,ValueError,KeyError,TypeError,OSError) as exc:
        print('STEP 11.21B FAIL CLOSED:',exc.reason if isinstance(exc,Halt) else type(exc).__name__,file=sys.stderr)
        return 2
    return 0

if __name__=='__main__':sys.exit(main())
