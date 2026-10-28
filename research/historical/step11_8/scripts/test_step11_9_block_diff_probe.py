import copy
import json
import tempfile
import unittest
from pathlib import Path
import step11_9_block_diff_probe as m

ALG='0x'+'a'*40
UNI='0x'+'b'*40
BH='0x'+'d'*64
TX=['0x'+f'{i+1:064x}' for i in range(36)]
PRICE=2**96
EXPECTED={'sqrt_price_x96':str(PRICE),'tick':7,'liquidity_raw':'100'}

def words(*items):
    return '0x'+''.join(f'{x:064x}' for x in items)

def fixture():
    trace=[{'txHash':t,'result':{'pre':{},'post':{}}} for t in TX]
    trace[34]['result']['pre'][ALG]={'storage':{'0x'+'0'*64:'0x01'}}
    trace[34]['result']['post'][ALG]={'storage':{'0x'+'0'*64:'0x02'}}
    events=[{'address':ALG,'topics':[m.TOPIC,'0x'+'1'*64,'0x'+'2'*64], 'data':words(0,0,PRICE,100,7), 'logIndex':'0x10'}]
    chain={'block':{'hash':BH,'transactions':TX},'receipt':{'blockHash':BH,'status':'0x1','transactionIndex':'0x22','logs':events},'trace':trace}
    return chain

def anchor():
    return {'block_number':m.MAX_BLOCK,'block_hash':BH,'tx_hash':TX[34], 'tx_index':34, 'algebra':ALG,'uniswap':UNI,'event':EXPECTED}

def rpc_for(f):
    def transport(method,params):
        if method=='eth_chainId':return '0x89'
        if method=='eth_getBlockByNumber':return f['block']
        if method=='eth_getTransactionReceipt':return f['receipt']
        if method=='debug_traceBlockByNumber':return f['trace']
        if method=='eth_call':
            to=params[0]['to']; selector=params[0]['data']
            if selector == m.LIQUIDITY:return words(100 if to==ALG else 200)
            if to==ALG and selector==m.GLOBAL_STATE:return words(PRICE,7)
            if to==UNI and selector==m.UNI_SLOT0:return words(PRICE*2,8)
        raise AssertionError((method,params))
    return m.RPC('https://mock.example',transport=transport)

class BlockDiffTests(unittest.TestCase):
    def test_fully_ordered_complete_trace_can_form_conditional_equivalence(self):
        r=m.perform(rpc_for(fixture()),anchor())
        self.assertEqual(r['verdict'],'CONDITIONAL_SAME_BOUNDARY_GETTER_EQUIVALENCE')
        self.assertEqual(r['rpc_calls'],9)
        self.assertTrue(m.assess(r))
        self.assertFalse(r['economic_profit_proven'])
    def test_later_algebra_change_blocks(self):
        f=fixture();f['trace'][35]['result']['post'][ALG]={'storage':{'0x'+'0'*64:'0x05'}}
        r=m.perform(rpc_for(f),anchor())
        self.assertEqual(r['reason'],'LATER_POOL_ACCOUNT_CHANGES_DETECTED')
    def test_later_uniswap_change_blocks(self):
        f=fixture();f['trace'][35]['result']['post'][UNI]={'balance':'0x1'}
        r=m.perform(rpc_for(f),anchor())
        self.assertEqual(r['reason'],'LATER_POOL_ACCOUNT_CHANGES_DETECTED')
    def test_bad_order_is_rejected(self):
        f=fixture();f['trace'][22]['txHash']=TX[23]
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'TRACE_TRANSACTION_ORDER_MISMATCH')
    def test_partial_trace_is_rejected(self):
        f=fixture();f['trace']=f['trace'][:-1]
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'INCOMPLETE_BLOCK_TRACE')
    def test_malformed_trace_is_rejected(self):
        f=fixture();f['trace'][13]['result']={'calls':[]}
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'MALFORMED_BLOCK_TRACE')
    def test_target_must_change_storage(self):
        f=fixture();f['trace'][34]['result']={'pre':{},'post':{}}
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'TARGET_TRACE_HAS_NO_STORAGE_DELTA')
    def test_bad_chain_is_rejected(self):
        f=fixture()
        rpc=rpc_for(f)
        old=rpc.transport
        rpc.transport=lambda method,params: '0x1' if method=='eth_chainId' else old(method,params)
        self.assertEqual(m.perform(rpc,anchor())['reason'],'WRONG_CHAIN')
    def test_wrong_swap_event_is_rejected(self):
        f=fixture();f['receipt']['logs'][0]['data']=words(0,0,PRICE+1,100,7)
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'ANCHOR_EVENT_NOT_RECONFIRMED')
    def test_wrong_pool_receipt_does_not_pass(self):
        f=fixture();f['receipt']['logs'][0]['address']=UNI
        self.assertEqual(m.perform(rpc_for(f),anchor())['reason'],'ANCHOR_EVENT_NOT_RECONFIRMED')
    def test_uniswap_read_must_be_valid(self):
        f=fixture(); rpc=rpc_for(f)
        old=rpc.transport
        rpc.transport=lambda method,params: '0x' if method=='eth_call' and params[0]['to']==UNI else old(method,params)
        self.assertEqual(m.perform(rpc,anchor())['reason'],'MALFORMED_GETTER_RESULT')
    def test_rpc_budget_enforced(self):
        rpc=m.RPC('https://mock.example',lambda method,params:'0x89',max_requests=1)
        rpc.call('eth_chainId',[])
        with self.assertRaises(m.Halt) as c:rpc.call('eth_chainId',[])
        self.assertEqual(c.exception.reason,'RPC_BUDGET_EXHAUSTED')
    def test_forged_profit_report_rejected(self):
        r=m.perform(rpc_for(fixture()),anchor())
        r['economic_profit_proven']=True
        with self.assertRaises(m.Halt):m.assess(r)
    def test_forged_conditional_result_rejected(self):
        r=m.perform(rpc_for(fixture()),anchor())
        r['pool_changes_after_target']['algebra']=[35]
        with self.assertRaises(m.Halt):m.assess(r)
    def test_anchor_strict_parser(self):
        a=anchor()
        doc={'schema_version':1,'chain_id':137,'evidence_kind':'polygon_tx_boundary_getter_probe_research_only',
             'block':{'number':m.MAX_BLOCK,'hash':BH},'transaction':{'index':34,'hash':TX[34],
             'final_pool_swap_event':EXPECTED},'storage_diff':{'has_target_pool_delta':True},
             'pools':{'algebra':ALG,'uniswap_500':UNI}}
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'anchor.json';p.write_text(json.dumps(doc))
            self.assertEqual(m.load_anchor(p),a)
            doc['transaction']['index']=33;p.write_text(json.dumps(doc))
            with self.assertRaises(m.Halt):m.load_anchor(p)

if __name__=='__main__':unittest.main()
