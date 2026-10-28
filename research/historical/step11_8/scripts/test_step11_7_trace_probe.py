import copy
import os
import tempfile
import unittest
from unittest.mock import patch

import step11_7_trace_probe as p
import step11_7_assess as a
from step11_collect import EvidenceError
from step11_4_swap_collect import V3_SWAP

ADDR='0x'+'ab'*20
ADDR2='0x'+'cd'*20
TX='0x'+'1a'*32
HASH='0x'+'2b'*32
TOPIC=V3_SWAP
SLOT='0x'+'11'*32


def fixture():
    event={'block_hash':HASH,'tx_hash':TX,'transaction_index':2,'log_index':4,
        'pool':ADDR,'pair_id':'USDT_WETH','kind':'v3','venue':'algebra','topic0':TOPIC}
    entry={'pair_id':'USDT_WETH','v2':{'quick':{'address':ADDR2}},
        'v3':{'algebra':{'address':ADDR},'uniswap_500':None}}
    header={'number':hex(96179661),'hash':HASH,'transactions':['0x'+'00'*32,'0x'+'01'*32,TX]}
    receipt={'transactionHash':TX,'blockHash':HASH,'blockNumber':hex(96179661),
        'transactionIndex':'0x2','status':'0x1','logs':[{'logIndex':'0x4','address':ADDR,'topics':[TOPIC]}]}
    prestate={'pre':{ADDR:{'storage':{SLOT:'0x1'}}},
             'post':{ADDR:{'storage':{SLOT:'0x2'}}}}
    return event,entry,header,receipt,prestate


def report_template():
    event,entry,header,receipt,diff=fixture()
    summary=p.inspect_prestate_diff(diff,{ADDR:None,ADDR2:None})
    return {'schema_version':1,'evidence_kind':'polygon_single_swap_trace_capability_research_only',
        'chain_id':137,'block':{'number':96179661,'hash':HASH,'transaction_count':3},
        'swap':event,'receipt_confirmed':True,
        'trace':{'call_status':'SUPPORTED','call_error_kind':None,
          'call_frames':2,'verified_pool_in_call_tree':True,
          'diff_status':'SUPPORTED','diff_error_kind':None,'diff_summary':summary},
        'capabilities':{'call_tree_supported_for_target_swap':True,
           'transaction_storage_diff_supported':True,
           'intermediate_pool_state_reconstructed':False,
           'intrablock_eth_call_or_quote_supported':False,
           'arbitrage_executable_profit_proven':False},
        'safety':{'read_only':True,'signing':False,'broadcast':False,
           'state_overrides':False,'relay_submission':False,'v3_executor_ready':False}}


class TraceDataTests(unittest.TestCase):
    def test_count_nested_calls(self):
        calls=p.count_calls({'type':'CALL','to':ADDR2,'calls':[{'type':'CALL','to':ADDR}]})
        self.assertEqual(calls,[ADDR2,ADDR])
    def test_invalid_call_tree_root_rejected(self):
        with self.assertRaises(EvidenceError):p.count_calls({'calls':[]})
    def test_nonlist_subframes_rejected(self):
        with self.assertRaises(EvidenceError):p.count_calls({'type':'CALL','calls':{}})
    def test_depth_limit(self):
        tree={'type':'CALL'}
        for _ in range(66):tree={'type':'CALL','calls':[tree]}
        with self.assertRaises(EvidenceError):p.count_calls(tree)
    def test_prestate_slot_change_count(self):
        diff=fixture()[-1]
        out=p.inspect_prestate_diff(diff,{ADDR:None,ADDR2:None})
        self.assertEqual(out['pools_with_slot_changes'],1)
        self.assertEqual(out['post_account_count'],1)
        self.assertEqual(len(out['trace_sha256']),64)
    def test_prestate_diff_required(self):
        with self.assertRaises(p.TraceUnavailable):p.inspect_prestate_diff({ADDR:{}},{ADDR:None})
    def test_diff_rejects_bad_hex(self):
        bad=fixture()[-1];bad['post'][ADDR]['storage'][SLOT]='0xZZ'
        with self.assertRaises(p.TraceUnavailable):p.inspect_prestate_diff(bad,{ADDR:None})
    def test_diff_rejects_malformed_slot(self):
        bad=fixture()[-1];bad['post'][ADDR]['storage']={'0x1':'0x2'}
        with self.assertRaises(p.TraceUnavailable):p.inspect_prestate_diff(bad,{ADDR:None})
    def test_normalize_checksum_like_case(self):
        diff=fixture()[-1];diff['pre']={ADDR.upper().replace('0X','0x'):diff['pre'][ADDR]}
        normal=p.normalize_diff_keys(diff)
        self.assertIn(ADDR,normal['pre'])
    def test_normalize_reject_duplicate_keys(self):
        diff=fixture()[-1];diff['pre'][ADDR.upper().replace('0X','0x')]={'storage':{}}
        with self.assertRaises(p.TraceUnavailable):p.normalize_diff_keys(diff)
    def test_verify_receipt_valid(self):
        e,_,h,r,_=fixture()
        p.verify_swap_in_receipt(r,e,{'hash':HASH,'number':96179661})
    def test_verify_receipt_reorg_detected(self):
        e,_,h,r,_=fixture();r['blockHash']='0x'+'ff'*32
        with self.assertRaises(EvidenceError):p.verify_swap_in_receipt(r,e,{'hash':HASH,'number':96179661})
    def test_verify_receipt_missing_log_rejected(self):
        e,_,h,r,_=fixture();r['logs']=[]
        with self.assertRaises(EvidenceError):p.verify_swap_in_receipt(r,e,{'hash':HASH,'number':96179661})
    def test_verify_receipt_failed_tx_rejected(self):
        e,_,h,r,_=fixture();r['status']='0x0'
        with self.assertRaises(EvidenceError):p.verify_swap_in_receipt(r,e,{'hash':HASH,'number':96179661})
    def test_verify_block_membership_valid(self):
        e,_,h,_,_=fixture();self.assertEqual(p.verify_block_membership(h,e),3)
    def test_verify_block_wrong_index(self):
        e,_,h,_,_=fixture();h['transactions'][2]='0x'+'ff'*32
        with self.assertRaises(EvidenceError):p.verify_block_membership(h,e)
    def test_trace_rpc_rejects_errors(self):
        rpc=p.TraceRpc('https://example.invalid',transport=lambda m,par:{'error':{'code':-32000}})
        with self.assertRaises(p.TraceUnavailable):rpc.trace(TX,{'tracer':'prestateTracer'})
    def test_rpc_trace_only_no_writes(self):
        allowed=[]
        def transport(m,params):
            allowed.append(m)
            return {'pre':{},'post':{}}
        rpc=p.TraceRpc('https://example.invalid',transport=transport)
        self.assertEqual(rpc.trace(TX,{'tracer':'prestateTracer'}),{'pre':{},'post':{}})
        self.assertEqual(allowed,['debug_traceTransaction'])


class ReportAssessmentTests(unittest.TestCase):
    def test_valid_summary_passes(self):
        self.assertIn('NOT_IMPLEMENTED',a.evaluate(report_template()))
    def test_wrong_swap_event_signature_fails(self):
        obj=report_template();obj['swap']['topic0']='0x'+'ff'*32
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_forged_executor_claim_fails(self):
        obj=report_template();obj['capabilities']['arbitrage_executable_profit_proven']=True
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_forged_intrablock_call_fails(self):
        obj=report_template();obj['capabilities']['intrablock_eth_call_or_quote_supported']=True
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_forged_signing_fails(self):
        obj=report_template();obj['safety']['signing']=True
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_forged_diff_count_fails(self):
        obj=report_template();obj['trace']['diff_summary']['pools_with_slot_changes']=0
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_duplicate_pools_fails(self):
        obj=report_template();s=obj['trace']['diff_summary'];s['pool_diffs'].append(copy.deepcopy(s['pool_diffs'][0]));s['pools_with_slot_changes']+=1
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_capability_and_trace_disagree(self):
        obj=report_template();obj['capabilities']['transaction_storage_diff_supported']=False
        with self.assertRaises(EvidenceError):a.evaluate(obj)
    def test_missing_trace_not_promoted(self):
        obj=report_template()
        obj['trace'].update(call_status='UNAVAILABLE',call_error_kind='RPC_REJECTED',call_frames=0,verified_pool_in_call_tree=False,
            diff_status='UNAVAILABLE',diff_error_kind='RPC_REJECTED',diff_summary=None)
        obj['capabilities']['call_tree_supported_for_target_swap']=False
        obj['capabilities']['transaction_storage_diff_supported']=False
        self.assertEqual(a.evaluate(obj),'TRACE_DIFF_UNAVAILABLE_CANNOT_RECONSTRUCT')
    def test_bad_block_hash_fails(self):
        obj=report_template();obj['block']['hash']='0x'+'00'*32
        with self.assertRaises(EvidenceError):a.evaluate(obj)


class ProbeTests(unittest.TestCase):
    def _run(self, trace_data=None, override=None):
        e,entry,header,receipt,diff=fixture()
        if override:override(e,entry,header,receipt,diff)
        def transport(method,params):
            if method=='eth_getBlockByNumber':return header
            if method=='eth_getTransactionReceipt':return receipt
            if method=='debug_traceTransaction':
                if params[1]['tracer']=='callTracer':return {'type':'CALL','to':ADDR,'calls':[]}
                return diff if trace_data is None else trace_data
            raise AssertionError(method)
        rpc=p.TraceRpc('https://example.invalid',transport=transport)
        with patch.object(p,'valid_historical_tip'),patch.object(p,'snapshot_pair',return_value=entry),\
             patch.object(p,'select_swap_blocks',return_value=([96179661],{96179661:[e]})):
            return p.probe(rpc,96179661,'USDT_WETH','algebra',500)
    def test_successful_probe(self):
        result=self._run();self.assertTrue(result['capabilities']['transaction_storage_diff_supported'])
        self.assertTrue(result['trace']['verified_pool_in_call_tree'])
        self.assertFalse(result['capabilities']['intermediate_pool_state_reconstructed'])
        self.assertIn('NOT_IMPLEMENTED',a.evaluate(result))
    def test_missing_diff_shape_remains_unavailable(self):
        report=self._run({'0x123':{'storage':{}}})
        self.assertEqual(report['trace']['diff_status'],'UNAVAILABLE')
        self.assertEqual(report['trace']['diff_error_kind'],'PRESTATE_DIFF_NOT_RETURNED')
        a.evaluate(report)
    def test_bad_receipt_stops_probe(self):
        def corrupt(e,entry,h,r,diff):r['blockHash']='0x'+'ff'*32
        with self.assertRaises(EvidenceError):self._run(override=corrupt)
    def test_reorg_stops_probe(self):
        def corrupt(e,entry,h,r,diff):h['hash']='0x'+'ff'*32
        with self.assertRaises(EvidenceError):self._run(override=corrupt)
    def test_unvetted_pair_rejected_before_rpc(self):
        with self.assertRaises(EvidenceError):p.probe(None,96179661,'UNKNOWN','algebra',500)
    def test_wrong_tier_rejected_before_rpc(self):
        with self.assertRaises(EvidenceError):p.probe(None,96179661,'USDT_WETH','uniswap_3000',500)


if __name__=='__main__':unittest.main()
