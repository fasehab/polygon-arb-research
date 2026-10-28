import unittest
from unittest.mock import patch
from step11_3_event_mixed_collect import (EventRpc, EvidenceError, ProviderError,
    select_activity_blocks, collect, collect_one, evaluate_pair, PAIR_IDS,
    quote_leg, MAX_WINDOW)
from step11_collect import TOKENS

A='0x'+'1'*40
B='0x'+'2'*40
H='0x'+'a'*64

def log(addr, block=200, blockhash=H, index=0):
    return {'address':addr,'blockNumber':hex(block),'blockHash':blockhash,'removed':False,
            'transactionHash':'0x'+'3'*64,'logIndex':hex(index)}

class TestStep113(unittest.TestCase):
    def test_method_allowlist(self):
        r=EventRpc('https://example.invalid',transport=lambda m,p:[])
        with self.assertRaises(EvidenceError):r.call('eth_sendRawTransaction',[])
        self.assertIn('eth_getLogs',r.ALLOWED)
    def test_select_pool_event_blocks(self):
        def transport(m,p):
            self.assertEqual(m,'eth_getLogs')
            return [log(A,200, index=0),log(A,201,index=1),log(B,201,index=2)]
        r=EventRpc('https://example.invalid',transport=transport)
        selected, activity=select_activity_blocks(r,200,202,
            {A:('DAI_WETH','v2'),B:('DAI_WETH','v3')},2)
        self.assertEqual(selected,[200,201])
        self.assertEqual(activity[201]['count'],2)
        self.assertEqual(activity[201]['roles'],{'v2','v3'})
    def test_unexpected_log_address(self):
        r=EventRpc('https://example.invalid',transport=lambda m,p:[log(B)])
        with self.assertRaises(EvidenceError):select_activity_blocks(r,200,200,{A:('DAI_WETH','v2')},2)
    def test_duplicate_log_rejected(self):
        r=EventRpc('https://example.invalid',transport=lambda m,p:[log(A),log(A)])
        with self.assertRaises(EvidenceError):
            select_activity_blocks(r,200,200,{A:('DAI_WETH','v2')},2)
    def test_removed_log_rejected(self):
        x=log(A);x['removed']=True
        r=EventRpc('https://example.invalid',transport=lambda m,p:[x])
        with self.assertRaises(EvidenceError):select_activity_blocks(r,200,200,{A:('DAI_WETH','v2')},2)
    def test_out_of_window_log_rejected(self):
        r=EventRpc('https://example.invalid',transport=lambda m,p:[log(A,10)])
        with self.assertRaises(EvidenceError):select_activity_blocks(r,200,205,{A:('DAI_WETH','v2')},2)
    def test_no_logs(self):
        r=EventRpc('https://example.invalid',transport=lambda m,p:[])
        self.assertEqual(select_activity_blocks(r,200,205,{A:('DAI_WETH','v2')},2),([],{}))
    def test_failed_fallback_restarts_whole_scan(self):
        calls=[]
        primary=object();fallback=object()
        def fake(r,*args):
            calls.append(r)
            if r is primary:raise ProviderError('rate limited')
            return {'selected_blocks':[]}
        with patch('step11_3_event_mixed_collect.collect_one',side_effect=fake):
            result=collect(primary,fallback,1,20,[1],[500],['DAI_WETH'],1)
        self.assertEqual(result['provider_role'],'fallback')
        self.assertEqual(calls,[primary,fallback])
    def test_duplicate_pairs_rejected(self):
        with self.assertRaises(EvidenceError):
            collect(None,None,1,20,[1],[500],['DAI_WETH','DAI_WETH'],1)
    def test_unvetted_pairs_rejected(self):
        with self.assertRaises(EvidenceError):
            collect(None,None,1,20,[1],[500],['SCAM_WETH'],1)
    def test_excessive_window_rejected(self):
        with self.assertRaises(EvidenceError):
            collect(None,None,1,MAX_WINDOW+1,[1],[500],['DAI_WETH'],1)
    def test_unsupported_tier_rejected(self):
        with self.assertRaises(EvidenceError):
            collect(None,None,1,20,[1],[100],['DAI_WETH'],1)
    def test_research_positive_never_authorizes(self):
        fake={'pair_id':'DAI_WETH','borrow_symbol':'DAI','mid_symbol':'WETH',
          'v2':{'quick':{'address':A},'sushi':None},
          'v3':{'algebra':{'address':B,'liquidity_raw':'1000'},'uniswap_500':None}}
        def quote(rpc,v,src,dst,amount,tier,block):return (amount+2000,None)
        with patch('step11_3_event_mixed_collect.read_fee',return_value=0),\
             patch('step11_3_event_mixed_collect.quote_leg',side_effect=quote):
            output=evaluate_pair(None,fake,200,[1],[500],True)
        self.assertEqual(len(output['quotes']),8)
        positives=[q for q in output['quotes'] if q['status']=='OK' and int(q['returned_raw'])>int(q['principal_raw'])]
        self.assertEqual(len(positives),2)
        self.assertTrue(all(q['status']=='SKIP_MISSING_POOL' for q in output['quotes'] if q['v2']=='sushi'))
    def test_no_activity_is_qualified_skip(self):
        fake={'pair_id':'DAI_WETH','borrow_symbol':'DAI','mid_symbol':'WETH',
          'v2':{'quick':None,'sushi':None},'v3':{'algebra':None,'uniswap_500':None}}
        with patch('step11_3_event_mixed_collect.read_fee',return_value=0):
            result=evaluate_pair(None,fake,100,[1],[500],False)
        self.assertEqual({q['status'] for q in result['quotes']},{'SKIP_NO_POOL_ACTIVITY'})
    def test_no_v3_executor_flag(self):
        with patch('step11_3_event_mixed_collect.collect_one',return_value={
            'safety':{'executor_supports_mixed_routes':False},'selected_blocks':[]}):
            x=collect(object(),None,1,20,[1],[500],['DAI_WETH'],1)
        self.assertIs(x['safety']['executor_supports_mixed_routes'],False)

if __name__=='__main__':unittest.main()
