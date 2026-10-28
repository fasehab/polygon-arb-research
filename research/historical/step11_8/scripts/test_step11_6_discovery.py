import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from step11_collect import EvidenceError, ProviderError
from step11_6_discover import (
    RESEARCH_PAIRS, RESEARCH_TOKENS, venues_for, routes, check_args, make_quote,
    gas_budget, collect, analyze_one, fx_one_native)
from step11_6_assess import verify, assess

A='0x'+'1'*40
B='0x'+'2'*40
C='0x'+'3'*40
H='0x'+'a'*64
TX='0x'+'b'*64


def entry():
    pool=lambda addr: {'address':addr,'liquidity_raw':'10000000'}
    return {'pair_id':'USDT_WETH','borrow_symbol':'USDT','mid_symbol':'WETH',
        'vault_balance_raw':'1000000000',
        'v2':{'quick':pool(A)},
        'v3':{'algebra':pool(B),'uniswap_500':pool(C)}}


def fixture():
    e=entry()
    with patch('step11_6_discover.quote_leg',return_value=(1000000,None)):
        quotes=[make_quote(None,e,105,a,b,1,0,93750,0,20)
            for a,b in routes(venues_for([500]))]
    activities=[{'pair_id':'USDT_WETH','venue':v,'pool':p,
                 'swap_logs_selected':1 if v=='algebra' else 0,
                 'status':'ACTIVE_LIQUIDITY_NOT_QUANTIFIED'}
                for v,p in [('quick',A),('algebra',B),('uniswap_500',C)]]
    ev={'block_hash':H,'tx_hash':TX,'transaction_index':0,'log_index':0,
        'pool':B,'pair_id':'USDT_WETH','kind':'v3','venue':'algebra','topic0':'0x'+'c'*64}
    return {'schema_version':1,'chain_id':137,'evidence_kind':'polygon_active_liquidity_bounded_read_only',
       'provider_role':'primary','window':{'from_block':100,'to_block':199,'anchor_hash':H},
       'research_pair_universe':[{'pair_id':'USDT_WETH','borrow_symbol':'USDT','mid_symbol':'WETH'}],
       'tier_ids':[500],'sizes_whole_units':[1],'pool_activity':activities,
       'selected_blocks':[{'number':105,'hash':H,'swap_event_count':1,
          'distinct_swap_txs':1,'active_pairs':['USDT_WETH'], 'fee_rate_wad':'0','swap_events':[ev]}],
       'selected_swap_logs':1,'gas_stress_model':{'gas_units':750000,'gas_gwei':100,
          'buffer_bps':12500,'min_net_units':0,'min_net_bps':20,'fx_source':'synthetic',
          'actual_execution_gas_measured':False},
       'gas_fx_records':[{'block':105,'pair_id':'USDT_WETH',
                           'fx_one_native_raw':'1000000','gas_budget_raw':'93750'}],
       'quotes':quotes,'status_counts':dict(Counter(q['status'] for q in quotes)),
       'complete_quotes':6,'model_positive_count':0,
       'coverage':{'selected_blocks':1,'completed_quotes':6,'adequate_window_for_study':True},
       'safety':{'signing':False,'broadcast':False,'private_relay_submission':False,
           'executor_supports_v3':False,'transaction_level_reconstruction':False,
           'gas_estimates_are_measured':False,'profitable_execution_proven':False}}


class DiscoveryTests(unittest.TestCase):
    def base_args(self):
        return (100,499,['USDT_WETH'],[500],[1,5],1,750000,100,12500,0,20)

    def test_new_allowlist(self):
        self.assertEqual(RESEARCH_TOKENS['USDT'][1],6)
        self.assertEqual(RESEARCH_TOKENS['WBTC'][1],8)
        self.assertEqual(RESEARCH_PAIRS['USDCe_WBTC'],('USDCe','WBTC'))
    def test_absent_sushi_and_disjoint_routes(self):
        r=routes(venues_for([500,3000]))
        self.assertEqual(len(r),12)
        self.assertTrue(all(a!=b for a,b in r))
        self.assertNotIn('sushi',str(r))
    def test_reject_bad_token(self):
        args=list(self.base_args()); args[2]=['UNKNOWN']
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_reject_duplicate_pair(self):
        args=list(self.base_args()); args[2]=['USDT_WETH','USDT_WETH']
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_reject_large_window(self):
        args=list(self.base_args());args[1]=500
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_reject_excessive_quote_budget(self):
        args=list(self.base_args());args[2]=['DAI_WETH','USDT_WETH','USDCe_WBTC'];args[3]=[500,3000];args[4]=[1,5,25];args[5]=2
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_reject_huge_sizes(self):
        args=list(self.base_args());args[4]=[1,500]
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_reject_duplicate_tiers(self):
        args=list(self.base_args());args[3]=[500,500]
        with self.assertRaises(EvidenceError):check_args(*args)
    def test_full_fallback_restart(self):
        first,second=object(),object();called=[]
        def mock(r,*a):
            called.append(r)
            if r is first:raise ProviderError('node error')
            return {'provider_role':'primary'}
        with patch('step11_6_discover.analyze_one',side_effect=mock):
            output=collect(first,second,*self.base_args())
        self.assertEqual(called,[first,second])
        self.assertEqual(output['provider_role'],'fallback')
    def test_no_fallback_remains_fail_closed(self):
        with patch('step11_6_discover.analyze_one',side_effect=ProviderError('offline')):
            with self.assertRaises(ProviderError):collect(object(),None,*self.base_args())
    def test_missing_pool_skips_without_rpc(self):
        e=entry();e['v3']['algebra']=None
        with patch('step11_6_discover.quote_leg',side_effect=AssertionError('must not call')):
            q=make_quote(None,e,105,'quick','algebra',1,0,100,0,20)
        self.assertEqual(q['status'],'SKIP_MISSING_OR_INACTIVE_POOL')
    def test_low_vault_skips(self):
        e=entry();e['vault_balance_raw']='0'
        q=make_quote(None,e,105,'quick','algebra',1,0,100,0,20)
        self.assertEqual(q['status'],'SKIP_INSUFFICIENT_VAULT_LIQUIDITY')
    def test_gross_loss_rejected(self):
        with patch('step11_6_discover.quote_leg',return_value=(1000000,None)):
            q=make_quote(None,entry(),105,'quick','algebra',1,0,100,0,20)
        self.assertEqual(q['status'],'REJECT_GROSS_UNPROFITABLE')
    def test_positive_gross_but_not_gas(self):
        def quote(*args):
            return (1000000 if args[1]=='quick' else 1100000,None)
        with patch('step11_6_discover.quote_leg',side_effect=quote):
            q=make_quote(None,entry(),105,'quick','algebra',1,0,300000,0,20)
        self.assertEqual(q['status'],'REJECT_MODELED_NET')
    def test_positive_model_is_unverified(self):
        def quote(*args):
            return (1000000 if args[1]=='quick' else 2000000,None)
        with patch('step11_6_discover.quote_leg',side_effect=quote):
            q=make_quote(None,entry(),105,'quick','algebra',1,0,93750,0,20)
        self.assertEqual(q['status'],'MODEL_POSITIVE_REPLAY_REQUIRED')
    def test_missing_fx_not_positive(self):
        with patch('step11_6_discover.quote_leg',return_value=(2000000,None)):
            q=make_quote(None,entry(),105,'quick','algebra',1,0,None,0,20)
        self.assertEqual(q['status'],'UNASSESSED_GAS_FX')
    def test_fake_no_native_pool(self):
        with patch('step11_6_discover.read_pair',return_value=None):
            self.assertIsNone(fx_one_native(None,'USDT',105))
    def test_full_scanner_and_assessor_round_trip_simulated_rpc(self):
        ev={'block_hash':H,'tx_hash':TX,'transaction_index':0,'log_index':0,
            'pool':A,'pair_id':'USDT_WETH','kind':'v2','venue':'quick','topic0':'0x'+'f'*64}
        obj=type('MockRPC',(),{'call':lambda self,m,p:'0x89' if m=='eth_chainId' else None})()
        with (patch('step11_6_discover.valid_historical_tip'),
              patch('step11_6_discover.decode_address',return_value='0x5757371414417b8c6caad45baef941abc7d3ab32'),
              patch('step11_6_discover.call_at',return_value='0x'+'0'*64),
              patch('step11_6_discover.valid_code'),
              patch('step11_6_discover.uint_at',side_effect=lambda rpc,addr,*a: 6 if addr==RESEARCH_TOKENS['USDT'][0] else 18),
              patch('step11_6_discover.get_hash',return_value=H),
              patch('step11_6_discover.snapshot_pair',return_value=entry()),
              patch('step11_6_discover.select_swap_blocks',return_value=([105],{105:[ev]})),
              patch('step11_6_discover.fx_one_native',return_value=1000000),
              patch('step11_6_discover.read_fee',return_value=0),
              patch('step11_6_discover.quote_leg',return_value=(1000000,None))):
            result=analyze_one(obj,100,199,['USDT_WETH'],[500],[1],1,750000,100,12500,0,20)
        self.assertEqual(result['selected_swap_logs'],1)
        self.assertEqual(result['complete_quotes'],6)
        self.assertEqual(verify(result)['complete_quotes'],6)

    def test_whole_scan_no_events_explicitly_insufficient(self):
        obj=type('MockRPC',(),{'call':lambda self,m,p:'0x89' if m=='eth_chainId' else None})()
        with (patch('step11_6_discover.valid_historical_tip'),
              patch('step11_6_discover.decode_address',return_value='0x5757371414417b8c6caad45baef941abc7d3ab32'),
              patch('step11_6_discover.call_at',return_value='0x'+'0'*64),
              patch('step11_6_discover.valid_code'),patch('step11_6_discover.uint_at',side_effect=lambda rpc,addr,*a: 6 if addr==RESEARCH_TOKENS['USDT'][0] else 18),
              patch('step11_6_discover.get_hash',return_value=H),
              patch('step11_6_discover.snapshot_pair',return_value=entry()),
              patch('step11_6_discover.select_swap_blocks',return_value=([],{}))):
            res=analyze_one(obj,100,199,['USDT_WETH'],[500],[1],1,750000,100,12500,0,20)
        self.assertFalse(res['coverage']['adequate_window_for_study'])
        self.assertEqual(res['model_positive_count'],0)
        self.assertEqual(res['quotes'],[])

class VerificationTests(unittest.TestCase):
    def test_fixture_passes(self):
        x=verify(fixture());self.assertEqual(x['complete_quotes'],6)
    def test_reject_wrong_chain(self):
        f=fixture();f['chain_id']=1
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_profit(self):
        f=fixture();f['quotes'][0]['gross_raw']='100'
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_net(self):
        f=fixture();f['quotes'][0]['status']='MODEL_POSITIVE_REPLAY_REQUIRED'
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_missing_quote(self):
        f=fixture();f['quotes'].pop()
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_duplicate_quote(self):
        f=fixture();f['quotes'].append(copy.deepcopy(f['quotes'][0]))
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_activity(self):
        f=fixture();f['pool_activity'][1]['swap_logs_selected']=5
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_gas_conversion(self):
        f=fixture();f['gas_fx_records'][0]['gas_budget_raw']='1'
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_flash_fee(self):
        f=fixture();f['quotes'][0]['flash_fee_raw']='123'
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_false_broadcast_authorization(self):
        f=fixture();f['safety']['broadcast']=True
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_unselected_pool_event(self):
        f=fixture();f['selected_blocks'][0]['swap_events'][0]['pool']=A
        with self.assertRaises(EvidenceError):verify(f)
    def test_reject_repeated_window(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'one.json';p.write_text(json.dumps(fixture()))
            with self.assertRaises(EvidenceError):assess([p,p])
    def test_insufficient_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'one.json';p.write_text(json.dumps(fixture()))
            self.assertEqual(assess([p])['verdict'],'INSUFFICIENT_COVERAGE_FOR_GO_NO_GO')
    def test_pause_only_after_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            files=[]
            for n in range(20):
                x=fixture();shift=n*1000
                x['window']['from_block']+=shift;x['window']['to_block']+=shift
                x['selected_blocks'][0]['number']+=shift
                x['gas_fx_records'][0]['block']+=shift
                for q in x['quotes']:q['block']+=shift
                p=Path(d)/f'{n}.json';p.write_text(json.dumps(x));files.append(p)
            self.assertEqual(assess(files)['verdict'],'INSUFFICIENT_PAIR_DIVERSITY_FOR_GO_NO_GO')
            self.assertEqual(assess(files,required_pairs=1)['verdict'],'PAUSE_STATIC_TWO_VENUE_STRATEGY_NO_EDGE_IN_STUDIED_WINDOWS')

if __name__=='__main__':unittest.main()
