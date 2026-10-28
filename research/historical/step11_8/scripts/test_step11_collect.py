"""Strict mocked RPC checks for Step 11. No external network and no credentials."""
import unittest
from step11_collect import (TOKENS,PAIRS,DAI,WETH,USDCe,QUICK,SUSHI,
    QUICK_FACTORY,SUSHI_FACTORY,VAULT,Rpc,ProviderError,EvidenceError,
    collect,collect_block,word,encode_quote,decode_quote,blockhash,
    GETPAIR,RESERVES,TOKEN0,TOKEN1,BALANCE,COLLECTOR,FLASH_FEE,DECIMALS,FACTORY,QUOTE)

def packed(*items):return '0x'+''.join(word(x) for x in items)
COLLECTOR_ADDRESS='0x3333333333333333333333333333333333333333'

class FakeNetwork:
    def __init__(self,fail=False,wrong_chain=False,missing_pool=None,unstable=False):
        self.fail=fail; self.wrong_chain=wrong_chain; self.missing_pool=missing_pool
        self.unstable=unstable; self.methods=[]; self.blockreads=0
        self.pairs={}
        for idx,(_,borrow,mid) in enumerate(PAIRS):
            symbols=(TOKENS[borrow][0],TOKENS[mid][0])
            for ex,factory in enumerate([QUICK_FACTORY,SUSHI_FACTORY]):
                pool='0x'+f'{(idx+1)*100+ex+10:040x}'
                self.pairs[(factory, frozenset(symbols))]=pool
        self.reverse={v:next(k[1] for k,p in self.pairs.items() if p==v) for v in self.pairs.values()}
    def __call__(self,method,params):
        self.methods.append(method)
        if self.fail: raise ProviderError('mocked missing archive')
        if method=='eth_chainId':return '0x1' if self.wrong_chain else '0x89'
        if method=='eth_blockNumber':return hex(96200000)
        if method=='eth_getBlockByNumber':
            self.blockreads+=1
            num=params[0]
            return {'number':num,'hash':'0x'+ ('b' if self.unstable and self.blockreads%2==0 else 'a')*64}
        if method=='eth_getCode':
            return '0x6000' if params[0] in self.reverse else '0x'
        if method!='eth_call':raise AssertionError('unexpected method '+method)
        call,block=params
        if int(block,16)>96200000: raise AssertionError('un-pinned block')
        to=call['to'];code=call['data'][2:];sel=code[:8]
        if sel==FACTORY:
            return packed(int(QUICK_FACTORY if to==QUICK else SUSHI_FACTORY,16))
        if sel==DECIMALS:
            found=[x for x in TOKENS.values() if x[0]==to]
            return packed(found[0][1])
        if sel==COLLECTOR:return packed(int(COLLECTOR_ADDRESS,16))
        if sel==FLASH_FEE:return packed(0)
        if sel==BALANCE:return packed(10**25)
        if sel==GETPAIR:
            a='0x'+code[8:72][-40:]; b='0x'+code[72:136][-40:]
            found=self.pairs.get((to,frozenset([a,b])))
            if (to,frozenset([a,b]))==self.missing_pool:found=None
            return packed(int(found,16) if found else 0)
        if sel in (TOKEN0,TOKEN1):
            a,b=sorted(self.reverse[to]);return packed(int(a if sel==TOKEN0 else b,16))
        if sel==RESERVES:return packed(10**24,10**24,0)
        if sel==QUOTE:
            amount=int(code[8:72],16)
            return packed(32,2,amount,max(1,amount*97//100))
        raise AssertionError('unexpected selector '+sel)

class Step11CollectorTests(unittest.TestCase):
    def test_abi_quote_strict(self):
        self.assertEqual(len(encode_quote(5,DAI,WETH)),10+5*64)
        self.assertEqual(decode_quote(packed(32,2,5,9),5),9)
        with self.assertRaises(EvidenceError):decode_quote(packed(32,3,5,9),5)
        with self.assertRaises(EvidenceError):decode_quote(packed(32,2,6,9),5)
    def test_multiple_blocks_and_pairs(self):
        fake=FakeNetwork(); p=Rpc('https://example.com/v2/key',fake)
        ev=collect(p,None,[96179857,96179797],[1,5])
        self.assertEqual(len(ev['blocks']),2)
        self.assertEqual(sum(len(a['quotes']) for b in ev['blocks'] for a in b['pairs']),24)
        self.assertEqual(set(fake.methods),{'eth_chainId','eth_blockNumber',
            'eth_getBlockByNumber','eth_getCode','eth_call'})
        self.assertTrue(all(b['provider_role']=='primary' for b in ev['blocks']))
        self.assertFalse(ev['safety']['writes_or_broadcasts_transactions'])
    def test_missing_pool_explicitly_skipped(self):
        missing=(QUICK_FACTORY,frozenset([DAI,WETH]));fake=FakeNetwork(missing_pool=missing)
        b=collect_block(Rpc('https://example.com',fake),96179857,[1,5])
        self.assertEqual(b['pairs'][0]['status'],'SKIP_MISSING_QUICK_POOL')
        self.assertEqual(b['pairs'][0]['quotes'],[])
        self.assertEqual(b['pairs'][1]['status'],'OK')
    def test_provider_fallback_restarts_entire_block(self):
        failed=FakeNetwork(fail=True); replacement=FakeNetwork()
        ev=collect(Rpc('https://one.invalid',failed),Rpc('https://two.invalid',replacement),[96179857],[1])
        self.assertEqual(ev['blocks'][0]['provider_role'],'fallback')
        self.assertEqual(len(ev['blocks'][0]['pairs'][0]['quotes']),2)
        self.assertIn('eth_chainId',replacement.methods)
    def test_wrong_chain_rejected(self):
        bad=FakeNetwork(wrong_chain=True)
        with self.assertRaises(EvidenceError):collect(Rpc('https://example.com',bad),None,[96179857],[1])
    def test_changing_block_hash_rejected(self):
        bad=FakeNetwork(unstable=True)
        with self.assertRaises(EvidenceError):collect(Rpc('https://example.com',bad),None,[96179857],[1])
    def test_malformed_rpc_urls_rejected(self):
        for x in ['', 'wss://example.com','https://x y','"https://example.com"',"https://example.com\n"]:
            with self.subTest(x=x),self.assertRaises(EvidenceError): Rpc(x)
    def test_duplicate_blocks_and_sizes_fail(self):
        good=Rpc('https://example.com',FakeNetwork())
        for blocks,sizes in [([96179857,96179857],[1]),([96179857],[1,1]),([96179857],[0]),([], [1])]:
            with self.assertRaises(EvidenceError):collect(good,None,blocks,sizes)
    def test_invalid_history_height_fail_closed(self):
        fake=FakeNetwork()
        with self.assertRaises(EvidenceError):collect_block(Rpc('https://example.com',fake),96200000,[1])
    def test_rpc_methods_cannot_broadcast(self):
        p=Rpc('https://example.com',FakeNetwork())
        for action in ['eth_sendRawTransaction','eth_sendTransaction','eth_callBundle','eth_sendBundle']:
            with self.assertRaises(EvidenceError):p.call(action,[])
    def test_hash_validation_and_fee(self):
        self.assertEqual(blockhash('0x'+'a'*64),'0x'+'a'*64)
        with self.assertRaises(EvidenceError):blockhash('0x'+'0'*64)

if __name__=='__main__':unittest.main()
