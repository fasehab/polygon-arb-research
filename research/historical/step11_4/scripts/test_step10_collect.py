"""Offline adversarial tests for Step 10 collector; does not contact Polygon."""
import unittest
from step10_collect import (DAI, WETH, QUICK, SUSHI, VAULT, QUICK_FACTORY, SUSHI_FACTORY,
    DEFAULT_BLOCK, SEL_GETPAIR, SEL_GETAMOUNTSOUT, SEL_RESERVES, SEL_TOKEN0,
    SEL_TOKEN1, SEL_BALANCE, SEL_COLLECTOR, SEL_FLASHFEE,
    word, aword, words, encode_quote, decode_quote, collect, flash_fee)

QPAIR='0x4a35582a710e1f4b2030a3f826da20bfb6703c09'
SPAIR='0x6ff62bfb8c12109e8000935a6de54dad83a4f39f'
COLLECTOR='0xce88686553686da562ce7cea497ce749da109f9f'
def packed(*values):return '0x'+''.join(word(v) for v in values)
class Fake:
    def __init__(self, mismatch=False, bad_pair=False):
        self.mismatch=mismatch
        self.bad_pair=bad_pair
        self.methods=[]
    def call(self,method,params):
        self.methods.append(method)
        if method=='eth_chainId':return hex(1 if self.mismatch else 137)
        if method=='eth_blockNumber':return hex(DEFAULT_BLOCK+20)
        if method=='eth_getCode':return '0x60006000'
        if method=='eth_call':
            call, tag=params
            if tag!=hex(DEFAULT_BLOCK):raise AssertionError('un-pinned historical eth_call')
            to=call['to']; data=call['data'][2:];sel=data[:8]
            if sel==SEL_GETPAIR:
                return packed(int(QPAIR if to==QUICK_FACTORY else SPAIR,16))
            if sel==SEL_TOKEN0:return packed(int(WETH,16) if not self.bad_pair else 0)
            if sel==SEL_TOKEN1:return packed(int(DAI,16))
            if sel==SEL_RESERVES:
                return packed(12000000000000000000,28000000000000000000000,0)
            if sel==SEL_BALANCE:return packed(10000000000000000000000)
            if sel==SEL_COLLECTOR:return packed(int(COLLECTOR,16))
            if sel==SEL_FLASHFEE:return packed(0)
            if sel==SEL_GETAMOUNTSOUT:
                amount=int(data[8:72],16)
                # Fee-adjusted stub: 5 DAI -> 0.002 WETH -> 4.90 DAI
                incoming='0x'+data[-128:-64][-40:]
                out=amount*2//5000 if incoming==DAI else amount*2450
                return packed(32,2,amount,out)
        raise AssertionError('unexpected RPC method '+method)

class Step10CollectorTests(unittest.TestCase):
    def test_abi_quote_roundtrip(self):
        raw=encode_quote(5*10**18,DAI,WETH)
        self.assertEqual(raw[:10],'0x'+SEL_GETAMOUNTSOUT)
        self.assertEqual(len(raw),10+5*64)
        self.assertEqual(decode_quote(packed(32,2,5,9),5),9)
    def test_quote_rejects_mismatched_input(self):
        with self.assertRaises(ValueError):decode_quote(packed(32,2,6,9),5)
        with self.assertRaises(ValueError):decode_quote(packed(32,3,5,9),5)
    def test_historical_collector_rejects_wrong_chain(self):
        with self.assertRaises(ValueError):collect(Fake(mismatch=True),DEFAULT_BLOCK,[5*10**18])
    def test_historical_collector_rejects_other_block(self):
        with self.assertRaises(ValueError):collect(Fake(),DEFAULT_BLOCK+1,[5*10**18])
    def test_historical_collector_rejects_pool_token_mismatch(self):
        with self.assertRaises(ValueError):collect(Fake(bad_pair=True),DEFAULT_BLOCK,[5*10**18])
    def test_fee_ceil(self):
        self.assertEqual(flash_fee(5*10**18,0),0)
        self.assertEqual(flash_fee(1,1),1)
        with self.assertRaises(ValueError):flash_fee(5*10**18,10**17)
    def test_pinned_calls_only_and_no_mutating_rpc(self):
        fake=Fake()
        data=collect(fake,DEFAULT_BLOCK,[5*10**18])
        self.assertEqual(data['chain_id'],137)
        self.assertEqual(len(data['quotes']),2)
        self.assertEqual(data['safety']['proves_executor_eth_call'],False)
        self.assertEqual(set(fake.methods),{'eth_call','eth_getCode','eth_chainId','eth_blockNumber'})
        self.assertEqual(data['quotes'][0]['route'],'quick_then_sushi')

if __name__=='__main__':unittest.main()
