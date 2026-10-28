import copy
import unittest
from unittest.mock import patch
import step11_8_boundary_probe as p
import step11_8_assess as a
from step11_collect import EvidenceError
from step11_4_swap_collect import V3_SWAP

ALG='0x'+'ab'*20
UNI='0x'+'cd'*20
TX='0x'+'11'*32
HASH='0x'+'22'*32
SLOT='0x'+'01'*32

def enc_tick(t):return t & ((1<<256)-1)
def enc_words(w):return '0x'+''.join(f'{v:064x}' for v in w)
def event(price=100,liq=400,tick=-1):
    return {'address':ALG,'logIndex':'0x8','topics':[V3_SWAP,'0x'+'0'*64,'0x'+'0'*64],
        'data':enc_words([enc_tick(-15),20,price,liq,enc_tick(tick)])}
def baseline():
    return {'schema_version':1,'evidence_kind':'polygon_tx_boundary_getter_probe_research_only',
        'chain_id':137,'block':{'number':96179661,'hash':HASH,'transaction_count':99},
        'transaction':{'hash':TX,'index':34,'final_pool_swap_event':{
            'sqrt_price_x96':'100','liquidity_raw':'400','tick':-1},
            'verified_swap_events_for_target_pool':1},
        'pools':{'algebra':ALG,'uniswap_500':UNI},
        'storage_diff':{'status':'POOL_STORAGE_CHANGED','has_target_pool_delta':True,'sha256':'a'*64},
        'txindex_tracecall':{'status':'VERIFIED_TWO_POOL_TRANSACTION_BOUNDARY',
            'reason':None,'before':{'sqrt_price_x96':'90','liquidity_raw':'400','tick':-2},
            'after':{'sqrt_price_x96':'100','liquidity_raw':'400','tick':-1},
            'competitor_after':{'sqrt_price_x96':'99','liquidity_raw':'450','tick':-2},
            'event_matches_after':True,'before_differs_from_after':True,
            'same_boundary_competitor_read':True},
        'capabilities':{'intrablock_pool_state_confirmed':True,
            'intrablock_quotations_performed':False,'complete_tick_state_reconstructed':False,
            'real_profit_proven':False},
        'safety':dict(a.SAFETY)}

class DecodingTests(unittest.TestCase):
    def test_signed_tick_negative(self):self.assertEqual(p.signed_tick(enc_tick(-5)),-5)
    def test_signed_tick_positive(self):self.assertEqual(p.signed_tick(7),7)
    def test_bad_tick_noncanonical(self):
        with self.assertRaises(EvidenceError):p.signed_tick(0xfffffa)
    def test_bad_tick_over_bound(self):
        with self.assertRaises(EvidenceError):p.signed_tick(887273)
    def test_decode_swap_event(self):
        self.assertEqual(p.decode_swap_event(event()),{'sqrt_price_x96':'100','liquidity_raw':'400','tick':-1})
    def test_bad_event_signature(self):
        item=event();item['topics'][0]='0x'+'f'*64
        with self.assertRaises(EvidenceError):p.decode_swap_event(item)
    def test_bad_event_payload_length(self):
        item=event();item['data']='0x'
        with self.assertRaises(EvidenceError):p.decode_swap_event(item)
    def test_bad_event_price(self):
        with self.assertRaises(EvidenceError):p.decode_swap_event(event(price=0))
    def test_getter_valid(self):
        self.assertEqual(p.getter_decoded([100,enc_tick(-1)],[400])['tick'],-1)
    def test_getter_wrong_liquidity_shape(self):
        with self.assertRaises(EvidenceError):p.getter_decoded([100,0],[400,12])
    def test_getter_wrong_price(self):
        with self.assertRaises(EvidenceError):p.getter_decoded([0,0],[400])

class TransportTests(unittest.TestCase):
    def mk(self,response):
        return p.BoundaryRpc('https://example.invalid',transport=lambda m,ps:response)
    def test_trace_call_options(self):
        calls=[]
        def t(m,params):
            calls.append((m,params))
            return {'type':'CALL','to':ALG,'output':enc_words([100,enc_tick(-1)])}
        x=p.BoundaryRpc('https://example.invalid',transport=t)
        self.assertEqual(x.trace_call(ALG,p.GLOBAL_STATE,96179661,35)[0],100)
        self.assertEqual(calls[0][0],'debug_traceCall')
        self.assertEqual(calls[0][1][2]['txIndex'],35)
    def test_no_rpc_error_is_success(self):
        with self.assertRaises(p.BoundaryTraceUnavailable):
            self.mk({'error':{'code':-32602}}).trace_call(ALG,p.SLOT0,96179661,35)
    def test_no_output_is_success(self):
        with self.assertRaises(p.BoundaryTraceUnavailable):
            self.mk({'type':'CALL','to':ALG}).trace_call(ALG,p.SLOT0,96179661,35)
    def test_wrong_target(self):
        with self.assertRaises(p.BoundaryTraceUnavailable):
            self.mk({'type':'CALL','to':UNI,'output':enc_words([100])}).trace_call(ALG,p.SLOT0,96179661,35)
    def test_reverted_call(self):
        with self.assertRaises(p.BoundaryTraceUnavailable):
            self.mk({'type':'CALL','to':ALG,'error':'execution reverted','output':enc_words([100])}).trace_call(ALG,p.SLOT0,96179661,35)
    def test_no_arbitrary_function(self):
        with self.assertRaises(EvidenceError):self.mk({}).trace_call(ALG,'0xdeadbeef',96179661,35)

class AssessmentTests(unittest.TestCase):
    def test_valid(self):self.assertIn('CONSISTENT',a.evaluate(baseline()))
    def test_missing_actual_event_match(self):
        x=baseline();x['txindex_tracecall']['after']['sqrt_price_x96']='99'
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_false_prestate_difference(self):
        x=baseline();x['txindex_tracecall']['before']=x['txindex_tracecall']['after'].copy()
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_forged_trading_claim(self):
        x=baseline();x['capabilities']['real_profit_proven']=True
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_forged_txindex_status(self):
        x=baseline();x['storage_diff']['has_target_pool_delta']=False
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_unproven_valid(self):
        x=baseline();x['txindex_tracecall']={'status':'UNPROVEN','reason':'RPC_REJECTED',
            'before':None,'after':None,'competitor_after':None,
            'event_matches_after':False,'before_differs_from_after':False,
            'same_boundary_competitor_read':False}
        x['capabilities']['intrablock_pool_state_confirmed']=False
        self.assertIn('UNPROVEN',a.evaluate(x))
    def test_unproven_claims_capability(self):
        x=baseline();x['txindex_tracecall']['status']='UNPROVEN'
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_malformed_price(self):
        x=baseline();x['transaction']['final_pool_swap_event']['sqrt_price_x96']='01'
        with self.assertRaises(EvidenceError):a.evaluate(x)
    def test_noninteger_index(self):
        x=baseline();x['transaction']['index']=34.0
        with self.assertRaises(EvidenceError):a.evaluate(x)

class ReceiptTests(unittest.TestCase):
    def test_latest_swap_in_receipt(self):
        selected={'swap':{'tx_hash':TX,'pool':ALG,'log_index':5},
            'block':{'hash':HASH}}
        one=event();one['logIndex']='0x5';two=event(110,500,2);two['logIndex']='0x8'
        rec={'transactionHash':TX,'blockHash':HASH,'status':'0x1', 'logs':[one,two]}
        class R:
            def call(self,m,params):return rec
        expected,num=p.receipt_final_swap(R(),selected)
        self.assertEqual(expected['sqrt_price_x96'],'110')
        self.assertEqual(num,2)
    def test_no_selected_event_rejected(self):
        selected={'swap':{'tx_hash':TX,'pool':ALG,'log_index':2},'block':{'hash':HASH}}
        rec={'transactionHash':TX,'blockHash':HASH,'status':'0x1','logs':[event()]}
        class R:
            def call(self,m,params):return rec
        with self.assertRaises(EvidenceError):p.receipt_final_swap(R(),selected)

if __name__=='__main__':unittest.main()

class IntegrationTests(unittest.TestCase):
    def baseline_probe(self):
        return {'swap':{'tx_hash':TX,'pool':ALG,'transaction_index':34,'log_index':8},
            'block':{'hash':HASH,'number':96179661,'transaction_count':99}}
    def fake_rpc(self, ignore=False):
        class MockRpc:
            def trace_call(self,addr,selector,block,idx):
                if addr==UNI:
                    if selector==p.LIQUIDITY:return [450]
                    return [99,enc_tick(-2)]
                if selector==p.LIQUIDITY:return [400]
                if ignore:return [100,enc_tick(-1)]
                return [90,enc_tick(-2)] if idx==34 else [100,enc_tick(-1)]
            def call(self,method,params):
                return {'hash':HASH}
        return MockRpc()
    def patched(self):
        from contextlib import ExitStack
        stack=ExitStack()
        stack.enter_context(patch.object(p,'probe',return_value=self.baseline_probe()))
        stack.enter_context(patch.object(p,'snapshot_pair',return_value={
            'v3':{'algebra':{'address':ALG},'uniswap_500':{'address':UNI}}}))
        stack.enter_context(patch.object(p,'receipt_final_swap',return_value=(
            {'sqrt_price_x96':'100','liquidity_raw':'400','tick':-1},1)))
        stack.enter_context(patch.object(p,'changed_pool_diff',return_value=(True,'POOL_STORAGE_CHANGED','a'*64)))
        return stack
    def test_true_boundary_verifies(self):
        with self.patched():obj=p.gather(self.fake_rpc())
        self.assertIn('CONSISTENT',a.evaluate(obj))
    def test_ignored_txindex_never_certifies(self):
        with self.patched():obj=p.gather(self.fake_rpc(ignore=True))
        self.assertIn('UNPROVEN',a.evaluate(obj))
        self.assertEqual(obj['txindex_tracecall']['reason'],'TXINDEX_IGNORED_OR_PRICE_UNCHANGED')
