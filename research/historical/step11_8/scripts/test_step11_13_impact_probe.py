import copy
import json
import unittest
from decimal import Decimal
import step11_13_impact_probe as m

ALG='0x'+'a'*40
UNI='0x'+'b'*40
BLOCK=96179661
BH='0x'+'c'*64
TX=['0x'+f'{x+1:064x}' for x in range(41)]
ALGPRICE=3830266144650824814533094
UNIPRE=3830065832365186024580722
UNIPOST=3830311536833363186157185
ALG_TICK=-198753
UNIPRE_TICK=-198755
UNIPOST_TICK=-198753
ALG_LIQ=19547205120552261
UNI_LIQ=31702576894861336

def word(n):return f'{n%(2**256):064x}'
def words(*v):return '0x'+''.join(word(n) for n in v)
def pack(price,tick):return (tick%(2**24))*(2**160)+price

def anchor():
    return {'block_number':BLOCK,'block_hash':BH,'tx_hash':TX[34],
        'tx_index':34,'algebra':ALG,'uniswap':UNI,
        'event':{'sqrt_price_x96':str(ALGPRICE),'tick':ALG_TICK,'liquidity_raw':str(ALG_LIQ)}}

def fixture():
    event={'address':ALG,'topics':[m.TOPIC,'0x'+'1'*64,'0x'+'2'*64],
        'data':words(2,-3,ALGPRICE,ALG_LIQ,ALG_TICK), 'blockNumber':hex(BLOCK),
        'blockHash':BH,'transactionIndex':'0x22','transactionHash':TX[34],
        'logIndex':'0x12'}
    trace=[{'txHash':x,'result':{'pre':{},'post':{}}} for x in TX]
    trace[34]['result']['pre'][ALG]={'storage':{'0x0':'0x01'}}
    trace[34]['result']['post'][ALG]={'storage':{'0x0':'0x02'}}
    trace[39]['result']['pre'][UNI]={'storage':{'0x00':hex(pack(UNIPRE,UNIPRE_TICK))}}
    trace[39]['result']['post'][UNI]={'storage':{'0x00':hex(pack(UNIPOST,UNIPOST_TICK))}}
    return {'logs':[event], 'trace':trace,
            'receipt':{'blockHash':BH,'transactionHash':TX[34], 'status':'0x1',
                    'transactionIndex':'0x22', 'logs':[event]},
            'block':{'hash':BH,'transactions':TX}}

def rpc_factory(f):
    def transport(method,params):
        if method=='eth_chainId':return '0x89'
        if method=='eth_getLogs':return f['logs']
        if method=='eth_getBlockByNumber':return f['block']
        if method=='eth_getTransactionReceipt':return f['receipt']
        if method=='debug_traceBlockByNumber':return f['trace']
        if method=='eth_getStorageAt':
            if params[1]=='0x0':return hex(pack(UNIPOST,UNIPOST_TICK))
            if params[1]=='0x4':return hex(UNI_LIQ)
        if method=='eth_call':
            c=params[0];addr=c['to'];selector=c['data']; block=params[1]
            if addr==ALG and selector==m.GLOBAL_STATE:
                return words(ALGPRICE if block==hex(BLOCK) else ALGPRICE*999//1000, ALG_TICK)
            if addr==ALG and selector==m.LIQUIDITY:return words(ALG_LIQ)
            if selector==m.TOKEN0:return words(int(m.WETH,16))
            if selector==m.TOKEN1:return words(int(m.USDT,16))
            if selector==m.UNI_FEE:return words(500)
        raise AssertionError((method,params))
    return m.RPC('https://mock.invalid',transport=transport,max_requests=m.MAX_RPC)

class ImpactTests(unittest.TestCase):
    def test_recover_historical_boundary_and_fee_rejection(self):
        r=m.run(rpc_factory(fixture()),anchor(),96179600,96179799,10)
        self.assertEqual(r['verdict'],'REJECT_UNISWAP_FEE_FLOOR',r)
        self.assertEqual(r['historical_pool_states']['uniswap_post_target']['sqrt_price_x96'],str(UNIPRE))
        self.assertEqual(r['historical_pool_states']['uniswap_post_target']['rewound_tx_indices'],[39])
        self.assertEqual(r['spot_spread_bps'],'1.04602653')
        self.assertLess(Decimal(r['fee_only_ceiling_bps']),0)
        self.assertLessEqual(r['rpc_calls'],20)
        self.assertTrue(m.assess(r))
    def test_high_threshold_stops_before_trace(self):
        r=m.run(rpc_factory(fixture()),anchor(),96179600,96179799,100)
        self.assertEqual(r['verdict'],'NO_HIGH_IMPACT_ALGEBRA_SWAP')
        self.assertEqual(r['block_trace_requests'],0)
        self.assertEqual(r['rpc_calls'],4)
    def test_no_events_is_valid_no_candidate(self):
        f=fixture();f['logs']=[]
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['verdict'],'NO_HIGH_IMPACT_ALGEBRA_SWAP')
        self.assertIsNone(r['max_observed_impact_bps'])
    def test_later_algebra_write_must_block(self):
        f=fixture();f['trace'][35]['result']['post'][ALG]={'storage':{'0x1':'0x01'}}
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'LATER_ALGEBRA_ACCOUNT_CHANGE')
    def test_bad_counterparty_chain_must_block(self):
        f=fixture();f['trace'][39]['result']['post'][UNI]['storage']['0x00']='0x01'
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'TRACE_STORAGE_CHAIN_MISMATCH')
    def test_bad_block_trace_order_rejected(self):
        f=fixture();f['trace'][0]['txHash']=TX[1]
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'TRACE_TX_ORDER_MISMATCH')
    def test_missing_block_trace_transaction_rejected(self):
        f=fixture();f['trace'].pop()
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'INCOMPLETE_BLOCK_TRACE')
    def test_invalid_token_order_rejected(self):
        f=fixture();rpc=rpc_factory(f);orig=rpc.transport
        rpc.transport=lambda method,params: words(1000) if method=='eth_call' and params[0]['data']==m.UNI_FEE else orig(method,params)
        r=m.run(rpc,anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'TOKEN_OR_FEE_MISMATCH')
    def test_wrong_last_receipt_event_rejected(self):
        f=fixture();f['receipt']['logs'][0]=copy.deepcopy(f['receipt']['logs'][0]);f['receipt']['logs'][0]['data']=words(2,-3,ALGPRICE+100,ALG_LIQ,ALG_TICK)
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'TARGET_NOT_LAST_SWAP_IN_TX')
    def test_multiple_swaps_in_one_transaction_use_net_price_movement(self):
        f=fixture()
        first=copy.deepcopy(f['logs'][0])
        first['logIndex']='0x11'
        first['data']=words(2,-3,ALGPRICE*97//100,ALG_LIQ,ALG_TICK)
        f['logs'].insert(0,first)
        ranked=m.rank_events(f['logs'],anchor(),96179600,96179799,ALGPRICE*999//1000)
        self.assertEqual(len(ranked),1)
        final, _=m.spot_bps(ALGPRICE,ALGPRICE*999//1000)
        self.assertEqual(ranked[0]['impact_bps'],str(final))
        self.assertEqual(ranked[0]['log_index'],18)
    def test_same_index_different_tx_hash_rejected(self):
        f=fixture()
        second=copy.deepcopy(f['logs'][0]);second['logIndex']='0x13'
        second['transactionHash']=TX[35]
        f['logs'].append(second)
        with self.assertRaises(m.Halt) as x:
            m.rank_events(f['logs'],anchor(),96179600,96179799,ALGPRICE)
        self.assertEqual(x.exception.reason,'INCONSISTENT_TX_HASH_IN_EVENT_LOGS')
    def test_duplicate_log_rejected(self):
        f=fixture();f['logs']=f['logs']*2
        with self.assertRaises(m.Halt) as x:m.rank_events(f['logs'],anchor(),96179600,96179799,ALGPRICE)
        self.assertEqual(x.exception.reason,'DUPLICATE_SWAP_LOG')
    def test_price_invalid(self):
        with self.assertRaises(m.Halt):m.spot_bps(0,100)
    def test_bad_scan_window(self):
        with self.assertRaises(m.Halt):m.run(rpc_factory(fixture()),anchor(),100,700,10)
    def test_rewind_missing_post_as_cleared(self):
        current,touched=m.reverse_slot(0,{UNI:{'storage':{'0x04':'0x09'}}},{UNI:{'storage':{}}},UNI,4)
        self.assertTrue(touched)
        self.assertEqual(current,9)
    def test_rewind_missing_pre_as_created(self):
        current,touched=m.reverse_slot(9,{UNI:{'storage':{}}},{UNI:{'storage':{'0x04':'0x09'}}},UNI,4)
        self.assertTrue(touched)
        self.assertEqual(current,0)
    def test_duplicate_slot_encoding_rejected(self):
        with self.assertRaises(m.Halt) as x:
            m.slot_storage({UNI:{'storage':{'0x0':'0x01','0x00':'0x01'}}},UNI,0)
        self.assertEqual(x.exception.reason,'DUPLICATE_STORAGE_SLOT')
    def test_forged_ceiling_rejected(self):
        r=m.run(rpc_factory(fixture()),anchor(),96179600,96179799,10)
        r['fee_only_ceiling_bps']='1000'
        with self.assertRaises(m.Halt):m.assess(r)
    def test_forged_success_on_no_trace_rejected(self):
        r=m.run(rpc_factory(fixture()),anchor(),96179600,96179799,100)
        r['verdict']='ABOVE_ONE_LEG_FEE_ONLY_FULL_REPLAY_REQUIRED'
        with self.assertRaises(m.Halt):m.assess(r)
    def test_unsafe_flag_rejected(self):
        r=m.run(rpc_factory(fixture()),anchor(),96179600,96179799,10)
        r['signing']=True
        with self.assertRaises(m.Halt):m.assess(r)
    def test_tampered_trace_target_rejected(self):
        f=fixture();f['trace'][34]['result']['pre'].clear();f['trace'][34]['result']['post'].clear()
        r=m.run(rpc_factory(f),anchor(),96179600,96179799,10)
        self.assertEqual(r['reason'],'TARGET_POOL_UNCHANGED')
    def test_rpc_budget_enforced(self):
        rpc=m.RPC('https://mock.invalid',lambda a,b:'0x89',max_requests=1)
        rpc.call('eth_chainId',[])
        with self.assertRaises(m.Halt):rpc.call('eth_chainId',[])

if __name__=='__main__':unittest.main()
