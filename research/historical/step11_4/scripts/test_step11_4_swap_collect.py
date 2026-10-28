import unittest
from copy import deepcopy
from unittest.mock import patch
from step11_4_swap_collect import (select_swap_blocks,build_pool_map,V2_SWAP,V3_SWAP,
    collect,EvidenceError,ProviderError,EventRpc,strict_hex)

A='0x'+'1'*40
B='0x'+'2'*40
H='0x'+'a'*64
T='0x'+'b'*64

def log(addr=A,block=200,kind='v2',txindex=0,logindex=0):
    return {'address':addr,'blockNumber':hex(block),'blockHash':H,'removed':False,
        'transactionHash':('0x'+format(txindex+1,'064x')),'transactionIndex':hex(txindex),'logIndex':hex(logindex),
        'topics':[V2_SWAP if kind=='v2' else V3_SWAP],
        'data':'0x'+('0'*63+'1')*(4 if kind=='v2' else 5)}

def rpc(logs):
    def transport(method,params):
        if method!='eth_getLogs':raise AssertionError('unexpected RPC')
        assert set(params[0]['topics'][0])=={V2_SWAP,V3_SWAP}
        return logs
    return EventRpc('https://example.invalid',transport=transport)

POOLS={A:('DAI_WETH','v2','quick'),B:('DAI_WETH','v3','algebra')}

class SwapTests(unittest.TestCase):
    def test_v2_v3_in_same_block_select(self):
        l1=log(logindex=0)
        l2=log(addr=B,kind='v3',txindex=1,logindex=1)
        selected,events=select_swap_blocks(rpc([l2,l1]),200,220,POOLS,2)
        self.assertEqual(selected,[200])
        self.assertEqual([e['venue'] for e in events[200]],['quick','algebra'])
        self.assertEqual([e['transaction_index'] for e in events[200]],[0,1])
    def test_no_swaps_produces_empty_window(self):
        self.assertEqual(select_swap_blocks(rpc([]),200,220,POOLS,2),([],{}))
    def test_wrong_protocol_signature_fails(self):
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([log(kind='v3')]),200,220,POOLS,2)
    def test_duplicate_log_index_fails(self):
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([log(),log()]),200,220,POOLS,2)
    def test_invalid_transaction_index_fails(self):
        x=log();x['transactionIndex']='oops'
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([x]),200,220,POOLS,2)
    def test_missing_index_fails(self):
        x=log();x.pop('logIndex')
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([x]),200,220,POOLS,2)
    def test_wrong_log_data_fails(self):
        x=log();x['data']='0x00'
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([x]),200,220,POOLS,2)
    def test_out_of_range_fails(self):
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([log(block=100)]),200,220,POOLS,2)
    def test_duplicate_tx_different_index_fails(self):
        x=log(logindex=0);y=log(logindex=1,txindex=1);y['transactionHash']=x['transactionHash']
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([x,y]),200,220,POOLS,2)
    def test_unknown_address_fails(self):
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([log(addr=B)]),200,220,{A:POOLS[A]},2)
    def test_removed_log_fails(self):
        x=log();x['removed']=True
        with self.assertRaises(EvidenceError):select_swap_blocks(rpc([x]),200,220,POOLS,2)
    def test_pool_collision_fails(self):
        pool={'address':A}
        with self.assertRaises(EvidenceError):build_pool_map({'DAI_WETH':{'v2':{'quick':pool,'sushi':pool},'v3':{}}})
    def test_rank_cross_venue_first(self):
        entries=[log(block=200,logindex=0),log(block=201,logindex=1,txindex=1),
                 log(addr=B,block=201,kind='v3',logindex=2,txindex=2)]
        selected,events=select_swap_blocks(rpc(entries),200,202,POOLS,1)
        self.assertEqual(selected,[201])
        self.assertEqual(len(events[201]),2)
    def test_valid_hex_is_lowercase(self):
        self.assertEqual(strict_hex(A,42),A)
        with self.assertRaises(EvidenceError):strict_hex('0xZZ',4)
    def test_no_broadcast_authorization(self):
        r=rpc([])
        with self.assertRaises(EvidenceError):r.call('eth_sendRawTransaction',['0x1234'])
    def test_bad_window_rejected_pre_rpc(self):
        with self.assertRaises(EvidenceError):collect(None,None,100,501,[1],[500],['DAI_WETH'],2)
    def test_duplicate_sizes_rejected_pre_rpc(self):
        with self.assertRaises(EvidenceError):collect(None,None,100,105,[1,1],[500],['DAI_WETH'],2)
    def test_unknown_pairs_rejected_pre_rpc(self):
        with self.assertRaises(EvidenceError):collect(None,None,100,105,[1],[500],['UNKNOWN'],2)
    def test_bad_maxevents_rejected_pre_rpc(self):
        with self.assertRaises(EvidenceError):collect(None,None,100,105,[1],[500],['DAI_WETH'],3)
    def test_fallback_restarts_full_scan(self):
        calls=[];a=object();b=object()
        def fake(r,*args):
            calls.append(r)
            if r is a:raise ProviderError('fail')
            return {'selected_blocks':[]}
        with patch('step11_4_swap_collect.collect_one',side_effect=fake):
            self.assertEqual(collect(a,b,100,105,[1],[500],['DAI_WETH'],2)['provider_role'],'fallback')
        self.assertEqual(calls,[a,b])

if __name__=='__main__':unittest.main()
