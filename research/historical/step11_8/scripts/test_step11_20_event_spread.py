import copy
from decimal import Decimal
import unittest
import step11_20_event_spread as m

ALG='0x'+'a'*40
UNI='0x'+'b'*40
BLOCK=96179661
BH='0x'+'c'*64
TX=['0x'+f'{i+1:064x}' for i in range(41)]
AQ=3830266144650824814533094
UQ=3830065832365186024580722
END_U=3830311536833363186157185
AL=-198753
UL=-198755
END_T=-198753
ALI=19547205120552261
ULI=31702576894861336

def word(i):return f'{i%(2**256):064x}'
def words(*values):return '0x'+''.join(word(x) for x in values)
def pack(price,tick):return (tick % (2**24))*2**160 + price

def anchor():
    return {'algebra':ALG,'uniswap':UNI,'block_number':BLOCK,'tx_index':34,
            'block_hash':BH,'tx_hash':TX[34]}

def swap(pool, ix, price, tick, liq, block=BLOCK, log_index=18, txs=TX):
    return {'address':pool,'topics':[m.TOPIC,'0x'+'1'*64,'0x'+'2'*64],
        'data':words(2,-3,price,liq,tick),'blockNumber':hex(block),
        'blockHash':BH,'transactionIndex':hex(ix),'transactionHash':txs[ix],
        'logIndex':hex(log_index)}

def fixture(target_venue='algebra'):
    if target_venue=='algebra':
        event=swap(ALG,34,AQ,AL,ALI)
    else:
        event=swap(UNI,34,UQ,UL,ULI)
    traces=[{'txHash':h,'result':{'pre':{},'post':{}}} for h in TX]
    target=ALG if target_venue=='algebra' else UNI
    traces[34]['result']['pre'][target]={'storage':{'0x0':'0x01'}}
    traces[34]['result']['post'][target]={'storage':{'0x0':'0x02'}}
    if target_venue=='algebra':
        traces[39]['result']['pre'][UNI]={'storage':{'0x0':hex(pack(UQ,UL))}}
        traces[39]['result']['post'][UNI]={'storage':{'0x0':hex(pack(END_U,END_T))}}
    else:
        # For uniswap target transaction, prestateTracer slot0 changes in target;
        # final state after target is UQ, later transaction changes it to END_U.
        traces[34]['result']['pre'][UNI]={'storage':{'0x0':hex(pack(UQ-123456789,UL))}}
        traces[34]['result']['post'][UNI]={'storage':{'0x0':hex(pack(UQ,UL))}}
        traces[39]['result']['pre'][UNI]={'storage':{'0x0':hex(pack(UQ,UL))}}
        traces[39]['result']['post'][UNI]={'storage':{'0x0':hex(pack(END_U,END_T))}}
    return {'logs':[event], 'receipt':{'transactionHash':TX[34], 'blockHash':BH,
                'status':'0x1','transactionIndex':'0x22','logs':[event]},
            'trace':traces,'block':{'hash':BH,'transactions':TX}}

def rpc_for(f, algebra_end=AQ, fee=404):
    def transport(method,params):
        if method=='eth_chainId':return '0x89'
        if method=='eth_getLogs':return f['logs']
        if method=='eth_getBlockByNumber':return f['block']
        if method=='eth_getTransactionReceipt':return f['receipt']
        if method=='debug_traceBlockByNumber':return f['trace']
        if method=='eth_getStorageAt':
            return hex(pack(END_U,END_T)) if int(params[1],16)==0 else hex(ULI)
        if method=='eth_call':
            params0=params[0];selector=params0['data'];pool=params0['to']
            if selector==m.GLOBAL_STATE and pool==ALG:
                return words(algebra_end if params[1]!=hex(96179599) else AQ*999//1000,AL,fee,0,0,1)
            if selector==m.UNI_SLOT0 and pool==UNI:
                return words(UQ,UL,0,0,0,0,0)
            if selector==m.LIQUIDITY and pool==ALG:return words(ALI)
            if selector==m.TOKEN0:return words(int(m.WETH,16))
            if selector==m.TOKEN1:return words(int(m.USDT,16))
            if selector==m.UNI_FEE:return words(500)
        raise AssertionError((method,params))
    return m.RPC('https://test.invalid',transport=transport,max_requests=m.MAX_RPC)

class Step1120Tests(unittest.TestCase):
    def test_reconstruct_algebra_target_two_leg_fee_reject(self):
        f=fixture()
        doc=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('0.5'))
        self.assertEqual(doc['verdict'],'REJECT_KNOWN_UNISWAP_FEE_FLOOR',doc)
        self.assertEqual(doc['verified_boundary']['uniswap_post_tx']['sqrt_price_x96'],str(UQ))
        self.assertEqual(doc['verified_boundary']['uniswap_post_tx']['rewound_tx_indices'],[39])
        self.assertEqual(doc['verified_boundary']['algebra_fee_hint_units'],404)
        self.assertLess(Decimal(doc['verified_boundary']['known_uniswap_fee_only_ceiling_bps']),0)
        self.assertLessEqual(doc['rpc_calls'],m.MAX_RPC)
        self.assertTrue(m.assess(doc))
    def test_reconstruct_uniswap_target(self):
        f=fixture('uniswap_500')
        doc=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('0.5'))
        self.assertEqual(doc['verdict'],'REJECT_KNOWN_UNISWAP_FEE_FLOOR',doc)
        self.assertEqual(doc['verified_boundary']['uniswap_post_tx']['sqrt_price_x96'],str(UQ))
    def test_no_candidate_does_not_trace(self):
        f=fixture();doc=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('100'))
        self.assertEqual(doc['verdict'],'NO_EVENT_HEURISTIC_ABOVE_THRESHOLD',doc)
        self.assertEqual(doc['rpc_calls'],4)
        self.assertEqual(doc['block_traces'],0)
    def test_empty_logs_is_no_candidate(self):
        f=fixture();f['logs']=[]
        doc=m.run(rpc_for(f),anchor(),96179600,96179799)
        self.assertEqual(doc['verdict'],'NO_EVENT_HEURISTIC_ABOVE_THRESHOLD')
    def test_both_pools_in_same_transaction_skipped(self):
        f=fixture(); f['logs'].append(swap(UNI,34,UQ,UL,ULI,log_index=19))
        d,activity=m.discover(f['logs'],anchor(),96179600,96179799,AQ*999//1000,UQ,Decimal('.1'))
        self.assertEqual(d,[])
        self.assertEqual(activity['skipped_both_pool_transactions'],1)
    def test_aggregate_multiple_swaps_one_tx(self):
        f=fixture(); earlier=swap(ALG,34,AQ-2000000000000000000,AL,ALI,log_index=17)
        f['logs'].insert(0,earlier)
        out,_=m.discover(f['logs'],anchor(),96179600,96179799,AQ*999//1000,UQ,Decimal('.1'))
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['log_index'],18)
    def test_later_algebra_modification_rejects_fee_equivalence(self):
        f=fixture();f['trace'][35]['result']['post'][ALG]={'storage':{'0x2':'0x01'}}
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['verdict'],'UNPROVEN')
        self.assertEqual(d['reason'],'LATER_ALGEBRA_ACCOUNT_CHANGE_FEE_UNVERIFIED')
    def test_trace_order_mismatch_blocks(self):
        f=fixture();f['trace'][0]['txHash']=TX[1]
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'TRACE_HASH_OR_ORDER_MISMATCH')
    def test_trace_incomplete_blocks(self):
        f=fixture();f['trace'].pop()
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'INCOMPLETE_BLOCK_TRACE')
    def test_trace_storage_chain_mismatch_blocks(self):
        f=fixture();f['trace'][39]['result']['post'][UNI]['storage']['0x0']='0x20'
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'TRACE_STORAGE_CHAIN_MISMATCH')
    def test_wrong_receipt_event_blocks(self):
        f=fixture();f['receipt']['logs'][0]=copy.deepcopy(f['receipt']['logs'][0])
        f['receipt']['logs'][0]['data']=words(2,-3,AQ+100,ALI,AL)
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'FINAL_SWAP_EVENT_MISMATCH')
    def test_wrong_token_identity_blocks(self):
        f=fixture();rpc=rpc_for(f);orig=rpc.transport
        def fake(method,params):
            if method=='eth_call' and params[0]['data']==m.TOKEN0:return words(123)
            return orig(method,params)
        rpc.transport=fake
        d=m.run(rpc,anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'TOKEN_IDENTITY_OR_ORDER_MISMATCH')
        self.assertEqual(d['block_traces'],0)
    def test_invalid_fee_blocks(self):
        f=fixture();d=m.run(rpc_for(f,fee=1_000_000),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'BAD_ALGEBRA_FEE_HINT')
    def test_multi_pool_receipt_blocks(self):
        f=fixture();f['receipt']['logs'].append(swap(UNI,34,UQ,UL,ULI,log_index=19))
        d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['reason'],'BOTH_POOLS_SWAPPED_IN_TARGET_TX')
    def test_forged_fee_result_rejected(self):
        f=fixture();d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        d['verified_boundary']['known_uniswap_fee_only_ceiling_bps']='100.0'
        with self.assertRaises(m.Halt):m.assess(d)
    def test_success_claim_rejected(self):
        f=fixture();d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('.5'))
        d['profit_proven']=True
        with self.assertRaises(m.Halt):m.assess(d)
    def test_no_trace_result_tampering_rejected(self):
        f=fixture();d=m.run(rpc_for(f),anchor(),96179600,96179799,Decimal('100'))
        d['block_traces']=1
        with self.assertRaises(m.Halt):m.assess(d)
    def test_rpc_budget_enforced(self):
        rpc=m.RPC('https://test.invalid',transport=lambda x,y:'0x89',max_requests=1)
        rpc.call('eth_chainId',[])
        with self.assertRaises(m.Halt):rpc.call('eth_chainId',[])
    def test_bad_window_blocks(self):
        with self.assertRaises(m.Halt):m.run(rpc_for(fixture()),anchor(),96179600,96179900)
    def test_duplicate_log_rejected(self):
        f=fixture();withdup=f['logs']*2
        with self.assertRaises(m.Halt):m.discover(withdup,anchor(),96179600,96179799,AQ,UQ,Decimal('1'))
    def test_algebra_fee_abis_are_hints_only(self):
        for data,expected in [
            (words(AQ,AL,404,0,0,1),404),
            (words(AQ,AL,404,7,0,0,1),404),
            (words(AQ,AL,404,405,7,0,0,1),404),
        ]:
            value, label=m.algebra_fee_hint(data)
            self.assertEqual(value,expected)
            self.assertTrue(label)
    def test_invalid_algebra_fee_abi_blocks(self):
        with self.assertRaises(m.Halt):m.algebra_fee_hint(words(AQ,AL,404,2,1))
    def test_fee_formula_one_bps_trivial(self):
        self.assertLess(m.full_fee_ceiling_bps(10000,10000,404),0)
    def test_heuristic_can_agree_without_being_independent_proof(self):
        d=m.run(rpc_for(fixture()),anchor(),96179600,96179799,Decimal('.5'))
        self.assertEqual(d['candidate']['heuristic_spread_bps'],
                         d['verified_boundary']['boundary_spread_bps'])
        self.assertIs(d['verified_100_usdt_depth'],False)
    def test_positive_spread_formula_not_quote(self):
        self.assertGreater(m.full_fee_ceiling_bps(1000000,998000,404),0)

if __name__=='__main__':unittest.main()
