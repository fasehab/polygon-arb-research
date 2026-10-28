import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import step11_19_cross_venue_quotes as m

ALG = '0x'+ 'a'*40

class Stub:
    def __init__(self):
        self.requests=[]
    def eth_call(self,address,data):
        self.requests.append((address,data))
        token_in = '0x' + format(int(data[10:74], 16), '040x')
        quoted = 10**18 if token_in == m.USDT else 100_000_100
        if address==m.ALG_QUOTER:
            return '0x' + m.abi_word(quoted)+m.abi_word(3000)
        if address==m.UNI_QUOTER:
            return '0x' + ''.join(m.abi_word(v) for v in (quoted,2**96,1,75000))
        raise AssertionError(address)

class TestQuoter(unittest.TestCase):
    def test_alg_selector(self):
        self.assertEqual(m.calldata_algebra(m.USDT,m.WETH,100)[0:10],'0x2d9ebd1d')
    def test_uniswap_selector(self):
        self.assertEqual(m.calldata_uni(m.USDT,m.WETH,100)[0:10],'0xc6a5026a')
    def test_calldata_lengths(self):
        self.assertEqual(len(m.calldata_algebra(m.USDT,m.WETH,100)),10+4*64)
        self.assertEqual(len(m.calldata_uni(m.USDT,m.WETH,100)),10+5*64)
    def test_quote_types(self):
        stub=Stub()
        a=m.quote_algebra(stub,m.USDT,m.WETH,100)
        u=m.quote_uni(stub,m.USDT,m.WETH,100)
        self.assertEqual(a['reported_fee_units'],3000)
        self.assertEqual(u['initialized_ticks_crossed'],1)
    def test_roundtrip_algebra_first(self):
        stub=Stub()
        q=m.score_route(stub,100,'algebra')
        self.assertEqual(q['delta_raw'], '100')
        self.assertEqual(stub.requests[0][0],m.ALG_QUOTER)
        self.assertEqual(stub.requests[1][0],m.UNI_QUOTER)
    def test_roundtrip_uniswap_first(self):
        stub=Stub()
        q=m.score_route(stub,100,'uniswap_500')
        self.assertEqual(q['delta_raw'], '100')
        self.assertEqual(stub.requests[0][0],m.UNI_QUOTER)
        self.assertEqual(stub.requests[1][0],m.ALG_QUOTER)
    def test_bad_quote_words(self):
        with self.assertRaises(m.Halt):m.decode_words('0x12',2)
        with self.assertRaises(m.Halt):m.decode_words('0x'+'z'*128,2)
    def test_bad_token(self):
        with self.assertRaises(m.Halt):m.check_address('0xabcd')
    def test_zero_fee_return_invalid(self):
        class Bad:
            def eth_call(self,*a):return '0x'+m.abi_word(1)+m.abi_word(2**16)
        with self.assertRaises(m.Halt):m.quote_algebra(Bad(),m.USDT,m.WETH,1)
    def test_wrong_fee_units_uni(self):
        class Bad:
            def eth_call(self,*a):return '0x'+''.join(m.abi_word(v) for v in (1,1,2**32,1))
        with self.assertRaises(m.Halt):m.quote_uni(Bad(),m.USDT,m.WETH,1)
    def test_wrong_route(self):
        with self.assertRaises(m.Halt):m.score_route(Stub(),10,'badroute')
    def test_fee_encoding(self):
        c=m.calldata_uni(m.USDT,m.WETH,100)
        self.assertEqual(int(c[10+3*64:10+4*64],16),500)

# End-to-end mock validates RPC method ordering, historical checks and budget.
class TestRun(unittest.TestCase):
    def mock_transport(self, wrong_hash=False):
        def transport(method, params):
            if method=='eth_chainId':return hex(137)
            if method=='eth_getBlockByNumber':
                return {'hash':('0x'+'0'*64) if wrong_hash else m.BLOCK_HASH}
            if method=='eth_getCode':return '0x6001600055'
            if method=='eth_getLogs':
                assert params[0]['topics']==[m.SWAP_TOPIC]
                return []
            if method=='eth_call':
                address=params[0]['to']
                data=params[0]['data']
                if data=='0x0dfe1681':return '0x'+m.abi_word(int(m.USDT,16))
                if data=='0xd21220a7':return '0x'+m.abi_word(int(m.WETH,16))
                if data=='0xddca3f43':return '0x'+m.abi_word(500)
                tokenin='0x'+format(int(data[10:74],16),'040x')
                amt=int(data[10+2*64:10+3*64],16)
                out=amt*10**12//100 if tokenin==m.USDT else amt*100//10**12
                assert out>0
                if address==m.UNI_QUOTER:
                    return '0x'+''.join(m.abi_word(x) for x in (out,2**96,0,50000))
                if address==m.ALG_QUOTER:
                    return '0x'+m.abi_word(out)+m.abi_word(3000)
            raise AssertionError((method,params))
        return transport

    def test_complete_mock_run(self):
        rpc=m.RPC('https://example.invalid',transport=self.mock_transport())
        doc=m.run(rpc,ALG)
        self.assertEqual(len(doc['quotes']),6)
        self.assertEqual(doc['positive_quote_count'],0)
        self.assertEqual(doc['rpc_calls'],25)
        self.assertEqual(doc['verdict'],'NO_POSITIVE_CROSS_VENUE_QUOTES_AT_TESTED_SIZES')
        self.assertFalse(doc['profit_proven'])
        self.assertFalse(doc['signing'])
        self.assertEqual(doc['swap_activity_last_20_blocks']['algebra']['last_20_blocks_swap_logs'],0)

    def test_wrong_block_fails_before_quotes(self):
        rpc=m.RPC('https://example.invalid',transport=self.mock_transport(wrong_hash=True))
        with self.assertRaisesRegex(m.Halt,'HISTORICAL_BLOCK_HASH_MISMATCH'):
            m.run(rpc,ALG)
        self.assertEqual(rpc.count,2)

    def test_request_cap(self):
        rpc=m.RPC('https://example.invalid',transport=self.mock_transport())
        rpc.count=m.MAX_REQUESTS
        with self.assertRaisesRegex(m.Halt,'RPC_REQUEST_CAP'):
            rpc.call('eth_chainId',[])

if __name__=='__main__':
    unittest.main()
