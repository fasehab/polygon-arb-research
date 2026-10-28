import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import csv
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from import_vps_sources import plan_import
from release_guard import inspect_text, scan_tree
from adapters.contracts import safe_research_status, SimulationAssessment


class ReleasePolicyTests(unittest.TestCase):
    def test_no_live_execution_authorized(self):
        a = SimulationAssessment('EXACT_STATE_SIMULATED', 137, '0xabc', 100, 1, 2, '')
        self.assertEqual(safe_research_status(a), 'SIMULATION_NET_POSITIVE_NOT_EXECUTION_PROOF')
        self.assertEqual(safe_research_status(SimulationAssessment('UNPROVEN', 137, '0xabc', 100, 1, 2, '')), 'UNPROVEN')
        self.assertEqual(safe_research_status(SimulationAssessment('EXACT_STATE_SIMULATED', 137, '0xabc', 1, 2, 0, '')), 'SIMULATION_NOT_PROFITABLE')

    def test_secret_detection(self):
        self.assertTrue(inspect_text('private_key = "0x' + 'a' * 64 + '"'))
        self.assertTrue(inspect_text('-'*5 + 'BEGIN PRIVATE KEY' + '-'*5))
        self.assertEqual(inspect_text('POLYGON_RPC_URL=https://YOUR_PROVIDER_ENDPOINT'), [])

    def test_import_only_source(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            src, dest = p / 'src', p / 'repo'
            src.mkdir()
            dest.mkdir()
            (src / 'main.rs').write_text('fn main() {}')
            (src / '.env').write_text('PRIVATE_KEY=do_not_publish')
            (src / 'outputs').mkdir()
            (src / 'outputs' / 'trace.json').write_text('{"secret": true}')
            items = plan_import(src, Path('runtime/rust'), dest)
            self.assertEqual([str(i[1]) for i in items], ['runtime/rust/main.rs'])
            (src / 'leak.py').write_text('private_key = "0x' + 'a'*64 + '"')
            with self.assertRaises(ValueError):
                plan_import(src, Path('runtime/rust'), dest)

    def test_kit_guard(self):
        self.assertEqual(scan_tree(ROOT), {})

    def test_ranker_smoke(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)
            inp = p / 'cycles.csv'
            out = p / 'ranking.json'
            with inp.open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['tx_hash','block_time','venue_route','pool_route','token_route','initial_usd','approximate_gross_usd','gas_usd'])
                w.writeheader()
                for i in range(3):
                    w.writerow(dict(tx_hash=f'0x{i}', block_time=f'2026-10-0{i+1} 00:00:00', venue_route='A -> B', pool_route='0xaaa -> 0xbbb', token_route='X -> Y -> X', initial_usd='1000', approximate_gross_usd='11', gas_usd='1'))
            result = subprocess.run([sys.executable, str(ROOT / 'research' / 'discovery' / '02_rank_routes.py'), str(inp), '--out', str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(out.read_text())
            self.assertEqual(report['status'], 'DISCOVERY_CANDIDATES_NOT_EXECUTION_PROOF')
            self.assertTrue(report['ranked_routes_excluding_outliers'][0]['recurring'])


if __name__ == '__main__':
    unittest.main()
