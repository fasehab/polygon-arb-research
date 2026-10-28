"""Read-only, mocked Step 11.14 regression tests; not live RPC proof."""
import copy
import unittest

import step11_14_ranked_probe as m
from test_step11_13_impact_probe import fixture, rpc_factory, anchor, ALGPRICE, ALG, TX, BLOCK, BH, ALG_LIQ, ALG_TICK, words


def two_candidate_fixture():
    f = fixture()
    first = copy.deepcopy(f['logs'][0])
    first['transactionIndex'] = hex(15)
    first['transactionHash'] = TX[15]
    first['logIndex'] = hex(4)
    first['data'] = words(2, -3, ALGPRICE * 1030 // 1000, ALG_LIQ, ALG_TICK)
    f['logs'] = [first] + f['logs']
    # Consistency with selected tx34 requires earlier pool to have mutated.
    f['trace'][15]['result']['pre'][ALG] = {'storage': {'0x0': '0x03'}}
    f['trace'][15]['result']['post'][ALG] = {'storage': {'0x0': '0x04'}}
    return f

class RankedProbeTests(unittest.TestCase):
    def test_second_candidate_is_reconstructed_by_full_trace(self):
        report = m.run(rpc_factory(two_candidate_fixture()), anchor(),
                       96179600, 96179799, 4, candidate_rank=2)
        self.assertEqual(report['verdict'], 'REJECT_UNISWAP_FEE_FLOOR', report)
        self.assertEqual(report['candidate_rank'], 2)
        self.assertEqual(report['qualified_swap_events'], 2)
        self.assertEqual(len(report['qualified_top_two']), 2)
        self.assertEqual(report['qualified_top_two'][0]['tx_index'],15)
        self.assertEqual(report['qualified_top_two'][1]['tx_index'],34)
        self.assertEqual(report['selected_swap']['tx_index'],34)
        self.assertEqual(report['block_trace_requests'],1)
        self.assertLessEqual(report['rpc_calls'],20)
        self.assertTrue(m.assess(report))

    def test_one_candidate_only_second_rank_unavailable_no_trace(self):
        report = m.run(rpc_factory(fixture()),anchor(),
                       96179600,96179799,4,candidate_rank=2)
        self.assertEqual(report['verdict'],'CANDIDATE_RANK_UNAVAILABLE')
        self.assertEqual(report['block_trace_requests'],0)
        self.assertEqual(report['rpc_calls'],4)
        self.assertIsNone(report['selected_swap'])
        self.assertTrue(m.assess(report))

    def test_no_candidates_reports_no_hit(self):
        report = m.run(rpc_factory(fixture()),anchor(),
                       96179600,96179799,1000,candidate_rank=2)
        self.assertEqual(report['verdict'],'NO_HIGH_IMPACT_ALGEBRA_SWAP')
        self.assertEqual(report['qualified_top_two'],[])
        self.assertTrue(m.assess(report))

    def test_candidate_one_backward_compatible(self):
        report = m.run(rpc_factory(fixture()),anchor(),
                       96179600,96179799,4,candidate_rank=1)
        self.assertEqual(report['verdict'],'REJECT_UNISWAP_FEE_FLOOR',report)
        self.assertEqual(report['selected_swap']['tx_index'],34)
        self.assertTrue(m.assess(report))

    def test_invalid_rank_fails_without_rpc(self):
        rpc = rpc_factory(fixture())
        with self.assertRaises(m.Halt):
            m.run(rpc,anchor(),96179600,96179799,4,candidate_rank=3)
        self.assertEqual(rpc.count,0)

    def test_tampered_selection_fails_assessor(self):
        report=m.run(rpc_factory(two_candidate_fixture()),anchor(),
                     96179600,96179799,4,candidate_rank=2)
        report['selected_swap']['tx_index']=15
        with self.assertRaises(m.Halt) as e:m.assess(report)
        self.assertEqual(e.exception.reason,'SELECTED_RANK_MISMATCH')

    def test_tampered_candidate_rank_fails_assessor(self):
        report=m.run(rpc_factory(two_candidate_fixture()),anchor(),
                     96179600,96179799,4,candidate_rank=2)
        report['candidate_rank']=1
        with self.assertRaises(m.Halt) as e:m.assess(report)
        self.assertEqual(e.exception.reason,'SELECTED_RANK_MISMATCH')

    def test_trace_order_mismatch_refuses_proof(self):
        f=two_candidate_fixture()
        f['trace'][0]['txHash']=TX[1]
        report=m.run(rpc_factory(f),anchor(),96179600,96179799,4,candidate_rank=2)
        self.assertEqual(report['verdict'],'UNPROVEN')
        self.assertEqual(report['reason'],'TRACE_TX_ORDER_MISMATCH')

    def test_later_algebra_change_refuses_proof(self):
        f=two_candidate_fixture()
        f['trace'][35]['result']['pre'][ALG]={'storage':{'0x1':'0x01'}}
        report=m.run(rpc_factory(f),anchor(),96179600,96179799,4,candidate_rank=2)
        self.assertEqual(report['verdict'],'UNPROVEN')
        self.assertEqual(report['reason'],'LATER_ALGEBRA_ACCOUNT_CHANGE')

if __name__=='__main__':unittest.main()
