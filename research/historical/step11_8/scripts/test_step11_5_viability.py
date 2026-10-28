import copy
import unittest
from unittest.mock import patch
from step11_collect import EvidenceError, ProviderError
from step11_5_viability import (route_pairs, gas_budget, select_refinement_sizes,
    evaluate_quote, validate_params, get_native_fx, collect, ceildiv, GAS_FX)
from step11_5_assess import validate_report


def entry():
    pool=lambda n: {'address':'0x'+format(n,'040x'),'liquidity_raw':'1000000'}
    return {'pair_id':'DAI_WETH','borrow_symbol':'DAI','mid_symbol':'WETH',
            'vault_balance_raw':str(10000*10**18),
            'v2':{'quick':pool(1),'sushi':pool(2)},
            'v3':{'algebra':pool(3),'uniswap_500':pool(4),'uniswap_3000':pool(5)}}


def result_fixture():
    r={'block':10,'pair_id':'DAI_WETH','borrow_symbol':'DAI','venue_first':'algebra',
       'venue_second':'uniswap_500','family':'V3_V3','size_units':1,'iteration':'coarse',
       'principal_raw':str(10**18),'flash_fee_raw':'0','first_out_raw':'100',
       'returned_raw':str(10**18+2*10**18),'gross_raw':str(2*10**18),
       'gas_budget_raw':str(10**17),'modeled_net_raw':str(19*10**17),
       'required_floor_raw':str(10**18),'status':'MODEL_POSITIVE_REPLAY_REQUIRED'}
    base={'chain_id':137,'safety':{'writes_or_broadcasts_transactions':False},
          'tiers':[500],
          'selected_blocks':[{'number':10,'fee_rate_wad':'0','active_pairs':['DAI_WETH'],
                              'pairs':[entry()]}]}
    base['step11_5']={'schema_version':1,'mode':'V3_FIRST_SAME_BLOCK_END_STATE_VIABILITY_SCREEN_ONLY',
                      'v3_first':True,'executor_can_trade_these_routes':False,
                      'signed_or_submitted_transaction':False,
                      'gas_model_is_actual_transaction_measurement':False,
                      'cost_assumptions':{'gas_units':1000000,'gas_gwei':100,
                        'gas_buffer_bps':10000,'min_net_units':1,'min_net_bps':20,
                        'native_fx_source':GAS_FX,'fx_is_spot_approximation':True},
                      'gas_fx':[{'block':10,'pair_id':'DAI_WETH','quote_1_native_raw':str(10**18),
                                 'gas_budget_raw':str(10**17)}],
                      'findings':[r], 'verdict_counts':{'MODEL_POSITIVE_REPLAY_REQUIRED':1},
                      'model_positive_count':1,'conclusion':'REPLAY_RESEARCH_ONLY'}
    return base


class ViabilityTests(unittest.TestCase):
    def test_gas_ceiling(self):
        self.assertEqual(ceildiv(10001,10000),2)
        self.assertEqual(gas_budget(10**18,750000,100,12500),93750000000000000)
    def test_bad_divisor(self):
        with self.assertRaises(EvidenceError):ceildiv(1,0)
    def test_routes_v3_first(self):
        r=route_pairs([500,3000])
        self.assertEqual(r[0],('algebra','uniswap_500','V3_V3'))
        self.assertEqual(len(r),6)
        self.assertNotIn('sushi',str(r))
    def test_refine_only_when_positive(self):
        self.assertEqual(select_refinement_sizes([{'gross_raw':'-1','size_units':5}],[1,5,25]),[])
        self.assertEqual(select_refinement_sizes([{'gross_raw':'4','size_units':5}],[1,5,25]),[3,15])
    def test_base_window_rejected(self):
        with self.assertRaises(EvidenceError):validate_params(1,500,['DAI_WETH'],[500],[1],2,750000,100,12500,1,20)
    def test_more_than_two_events_rejected(self):
        with self.assertRaises(EvidenceError):validate_params(10,20,['DAI_WETH'],[500],[1],3,750000,100,12500,1,20)
    def test_quote_budget_protects_vps(self):
        with self.assertRaises(EvidenceError):validate_params(10,20,['DAI_WETH','USDCe_WETH','DAI_USDCe'],[500,3000],[1,5,25],2,750000,100,12500,1,20)
    def test_no_unvetted_tiers(self):
        with self.assertRaises(EvidenceError):validate_params(10,20,['DAI_WETH'],[100], [1],1,750000,100,12500,1,20)
    def test_quote_positive_remains_replay_only(self):
        def quoter(rpc,venue,a,b,amount,tier,block):
            return (amount+2*10**18 if venue=='uniswap_500' else amount,None)
        with patch('step11_5_viability.quote_leg',side_effect=quoter):
            # algebra first preserves amount; uniswap second adds 2 DAI
            got=evaluate_quote(None,entry=entry(),block=10,
                 route=('algebra','uniswap_500','V3_V3'),size=1,fee_wad=0,
                 gas_raw=10**17,floor_units=1,floor_bps=20,iteration='coarse')
        self.assertEqual(got['status'],'MODEL_POSITIVE_REPLAY_REQUIRED')
        self.assertEqual(got['modeled_net_raw'],str(19*10**17))
    def test_quote_gross_negative_rejected(self):
        def quoter(*a,**kw): return 1, None
        with patch('step11_5_viability.quote_leg',side_effect=quoter):
            got=evaluate_quote(None,entry=entry(),block=10,
                route=('algebra','uniswap_500','V3_V3'),size=1,fee_wad=0,
                gas_raw=0,floor_units=1,floor_bps=20,iteration='coarse')
        self.assertEqual(got['status'],'REJECT_GROSS_UNPROFITABLE')
    def test_missing_fx_not_positive(self):
        with patch('step11_5_viability.quote_leg',return_value=(2*10**18,None)):
            got=evaluate_quote(None,entry=entry(),block=10,
                route=('algebra','uniswap_500','V3_V3'),size=1,fee_wad=0,
                gas_raw=None,floor_units=1,floor_bps=20,iteration='coarse')
        self.assertEqual(got['status'],'UNASSESSED_GAS_FX')
    def test_missing_pool_does_not_quote(self):
        e=entry();e['v3']['uniswap_500']=None
        with patch('step11_5_viability.quote_leg',side_effect=AssertionError):
            got=evaluate_quote(None,entry=e,block=10,route=('algebra','uniswap_500','V3_V3'),
                size=1,fee_wad=0,gas_raw=1,floor_units=1,floor_bps=20,iteration='coarse')
        self.assertEqual(got['status'],'SKIP_MISSING_POOL')
    def test_low_vault_skips(self):
        e=entry();e['vault_balance_raw']='0'
        got=evaluate_quote(None,entry=e,block=10,route=('algebra','uniswap_500','V3_V3'),
                size=1,fee_wad=0,gas_raw=1,floor_units=1,floor_bps=20,iteration='coarse')
        self.assertEqual(got['status'],'SKIP_INSUFFICIENT_VAULT_BALANCE')
    def test_missing_fx_pool_returns_none(self):
        with patch('step11_5_viability.read_pair',return_value=None):
            self.assertIsNone(get_native_fx(None,'DAI',123))
    def test_fx_pool_normal_order(self):
        from step11_5_viability import WRAPPED_NATIVE
        pool={'token0':WRAPPED_NATIVE,'token1':'0x'+'1'*40,
              'reserve0_raw':str(10**21),'reserve1_raw':str(10**21)}
        with patch('step11_5_viability.read_pair',return_value=pool):
            q=get_native_fx(None,'DAI',123)
        self.assertGreater(q,0)
        self.assertLess(q,10**18)
    def test_fallback_full_restart(self):
        one=object();two=object();called=[]
        def fake(r,*args):
            called.append(r)
            if r is one:raise ProviderError('fail')
            return {'selected_blocks':[]}
        with patch('step11_5_viability.run_one',side_effect=fake):
            output=collect(one,two,10,20,['DAI_WETH'],[500],[1],1,750000,100,12500,1,20)
        self.assertEqual(output['provider_role'],'fallback')
        self.assertEqual(called,[one,two])


class AssessmentTests(unittest.TestCase):
    def test_valid_fixture(self):
        x=validate_report(result_fixture())
        self.assertEqual(x['model_positive_replay_only'],1)
    def test_reject_forged_gross(self):
        f=result_fixture();f['step11_5']['findings'][0]['gross_raw']='999999'
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_forged_net(self):
        f=result_fixture();f['step11_5']['findings'][0]['modeled_net_raw']='999999'
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_inflated_candidate_count(self):
        f=result_fixture();f['step11_5']['model_positive_count']=2
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_forged_gas(self):
        f=result_fixture();f['step11_5']['gas_fx'][0]['gas_budget_raw']='0'
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_fake_execution_claim(self):
        f=result_fixture();f['step11_5']['executor_can_trade_these_routes']=True
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_fake_chain(self):
        f=result_fixture();f['chain_id']=1
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_unknown_venue(self):
        f=result_fixture();f['step11_5']['findings'][0]['venue_first']='sushi'
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_duplicate_trade(self):
        f=result_fixture();f['step11_5']['findings'].append(copy.deepcopy(f['step11_5']['findings'][0]))
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_no_gas_spot_marked_positive(self):
        f=result_fixture(); f['step11_5']['gas_fx'][0]['quote_1_native_raw']=None
        f['step11_5']['gas_fx'][0]['gas_budget_raw']=None
        f['step11_5']['findings'][0]['gas_budget_raw']=None
        with self.assertRaises(EvidenceError):validate_report(f)
    def test_reject_repeated_pool_activities(self):
        f=result_fixture(); f['selected_blocks'][0]['pairs'].append(copy.deepcopy(entry()))
        with self.assertRaises(EvidenceError):validate_report(f)

if __name__=='__main__':unittest.main()
