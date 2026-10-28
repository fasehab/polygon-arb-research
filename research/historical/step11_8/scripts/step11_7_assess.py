#!/usr/bin/env python3
"""Offline structural checks for Step 11.7 trace capability summaries.

Not an independent RPC replay, state proof, or proof of an arbitrage opportunity.
"""
import argparse
import json
import sys
from pathlib import Path
from step11_collect import EvidenceError
from step11_4_swap_collect import strict_hex, V2_SWAP, V3_SWAP
from step11_6_discover import RESEARCH_PAIRS


def evaluate(item):
    if not isinstance(item,dict) or item.get('schema_version') != 1 or item.get('evidence_kind') != 'polygon_single_swap_trace_capability_research_only' or item.get('chain_id') !=137:
        raise EvidenceError('unexpected Step11.7 evidence schema')
    block=item.get('block'); swap=item.get('swap'); tr=item.get('trace'); cap=item.get('capabilities'); safe=item.get('safety')
    if not all(isinstance(x,dict) for x in [block,swap,tr,cap,safe]):
        raise EvidenceError('report structure invalid')
    number=block.get('number'); ix=swap.get('transaction_index'); count=block.get('transaction_count')
    if not all(type(x)==int for x in [number,ix,count]) or not (0<number<200000000 and 0<=ix<count<=100000):
        raise EvidenceError('invalid block and tx range')
    hash_=strict_hex(block.get('hash'),66)
    if strict_hex(swap.get('block_hash'),66)!=hash_:
        raise EvidenceError('block/log hash mismatch')
    strict_hex(swap.get('tx_hash'),66)
    strict_hex(swap.get('pool'),42)
    if swap.get('pair_id') not in RESEARCH_PAIRS or swap.get('venue') not in {'quick','algebra','uniswap_500','uniswap_3000'}:
        raise EvidenceError('unvetted swap identity')
    is_v2=swap['venue']=='quick'
    if swap.get('kind')!=('v2' if is_v2 else 'v3') or strict_hex(swap.get('topic0'),66)!=(V2_SWAP if is_v2 else V3_SWAP):
        raise EvidenceError('pool event signature/protocol mismatch')
    if type(swap.get('log_index'))!=int or swap['log_index']<0:
        raise EvidenceError('log index invalid')
    if item.get('receipt_confirmed') is not True:
        raise EvidenceError('missing receipt verification')
    c,d=tr.get('call_status'),tr.get('diff_status')
    if c not in {'SUPPORTED','UNAVAILABLE'} or d not in {'SUPPORTED','UNAVAILABLE'}:
        raise EvidenceError('bad trace status')
    if (cap.get('call_tree_supported_for_target_swap') is not (c=='SUPPORTED') or
        cap.get('transaction_storage_diff_supported') is not (d=='SUPPORTED')):
        raise EvidenceError('capability mismatch')
    if type(tr.get('call_frames'))!=int or tr['call_frames']<0:
        raise EvidenceError('invalid frame count')
    if type(tr.get('verified_pool_in_call_tree'))!=bool:
        raise EvidenceError('invalid call membership flag')
    if c=='SUPPORTED':
        if not 1 <= tr['call_frames'] <= 50000 or tr.get('call_error_kind') is not None:
            raise EvidenceError('callTracer inconsistencies')
    else:
        if tr['call_frames']!=0 or tr['verified_pool_in_call_tree'] or not isinstance(tr.get('call_error_kind'),str):
            raise EvidenceError('unsupported callTracer still claims evidence')
    if d=='SUPPORTED':
        diff=tr.get('diff_summary')
        if not isinstance(diff,dict) or tr.get('diff_error_kind') is not None:
            raise EvidenceError('missing state diff summary')
        pre=diff.get('pre_account_count');post=diff.get('post_account_count')
        if type(pre)!=int or type(post)!=int or pre<0 or post<0 or pre>10000 or post>10000:
            raise EvidenceError('invalid modified-account count')
        pools=diff.get('pool_diffs')
        if not isinstance(pools,list) or not 1 <= len(pools) <= 5:
            raise EvidenceError('invalid pool diff records')
        seen=set(); positive=0
        for p in pools:
            if not isinstance(p,dict):raise EvidenceError('invalid pool record')
            addr=strict_hex(p.get('pool'),42)
            if addr in seen:raise EvidenceError('duplicate pool')
            seen.add(addr)
            before,after=p.get('pre_slots'),p.get('post_slots')
            if type(before)!=int or type(after)!=int or before<0 or after<0 or before>100000 or after>100000 or p.get('has_storage_delta') is not bool(before or after):
                raise EvidenceError('invalid slot count')
            positive+=bool(before or after)
        if strict_hex(swap.get('pool'),42) not in seen or positive!=diff.get('pools_with_slot_changes'):
            raise EvidenceError('pool coverage/count mismatch')
        digest=diff.get('trace_sha256')
        if not isinstance(digest,str) or len(digest)!=64:
            raise EvidenceError('missing trace digest')
        try:int(digest,16)
        except ValueError:raise EvidenceError('invalid digest') from None
    else:
        if tr.get('diff_summary') is not None or not isinstance(tr.get('diff_error_kind'),str):
            raise EvidenceError('unsupported trace still claims storage diff')
    expected_caps={'intermediate_pool_state_reconstructed':False,
        'intrablock_eth_call_or_quote_supported':False,
        'arbitrage_executable_profit_proven':False}
    expected_safe={'read_only':True,'signing':False,'broadcast':False,'state_overrides':False,
        'relay_submission':False,'v3_executor_ready':False}
    if any(cap.get(k) is not v for k,v in expected_caps.items()) or any(safe.get(k) is not v for k,v in expected_safe.items()):
        raise EvidenceError('unsafe or falsely certified trace claim')
    return 'PRESTATE_DIFF_AVAILABLE_RECONSTRUCTION_NOT_IMPLEMENTED' if d=='SUPPORTED' else 'TRACE_DIFF_UNAVAILABLE_CANNOT_RECONSTRUCT'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('report')
    args=p.parse_args()
    try:
        item=json.loads(Path(args.report).read_text())
        verdict=evaluate(item)
        print('STEP 11.7 OFFLINE TRACE EVIDENCE CHECK — PASS (NOT AN RPC PROOF)')
        print('call='+item['trace']['call_status']+' diff='+item['trace']['diff_status'])
        print('verdict='+verdict)
        print('ALWAYS BLOCK: no intrablock quote reconstruction, executor, signing or submission.')
    except (ValueError,OSError,KeyError,EvidenceError) as e:
        print('STEP 11.7 OFFLINE CHECK FAILED:',type(e).__name__, file=sys.stderr)
        sys.exit(2)


if __name__=='__main__': main()
