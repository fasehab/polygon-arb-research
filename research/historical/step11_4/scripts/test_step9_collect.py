import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("step9_collect", Path(__file__).with_name("step9_collect.py"))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class Step9CollectorTests(unittest.TestCase):
    def test_selector_constants(self):
        self.assertEqual(collector.SEL_GET_PAIR, "e6a43905")
        self.assertEqual(collector.SEL_RESERVES, "0902f1ac")
        self.assertEqual(collector.SEL_COLLECTOR, "d2946c2b")
        self.assertEqual(collector.SEL_FLASH_FEE, "d877845c")

    def test_address_encoding(self):
        word = collector.word_address(collector.DAI)
        self.assertEqual(len(word), 64)
        self.assertEqual(collector.decode_address("0x" + word), collector.DAI)

    def test_unknown_or_zero_reserves_fail(self):
        class FakeRpc:
            def eth_call(self, dest, calldata, block):
                if calldata.startswith("0x" + collector.SEL_GET_PAIR): return "0x" + collector.word_address("0x" + "a" * 40)
                if calldata == "0x" + collector.SEL_TOKEN0: return "0x" + collector.word_address(collector.WPOL)
                if calldata == "0x" + collector.SEL_TOKEN1: return "0x" + collector.word_address(collector.DAI)
                if calldata == "0x" + collector.SEL_RESERVES: return "0x" + "0" * 64 * 3
                raise RuntimeError("bad selector")
        with self.assertRaisesRegex(ValueError, "zero-liquidity"):
            collector.reserve_reference(FakeRpc(), collector.QUICK_FACTORY, 96179857)

    def test_collector_full_read_only_success_path(self):
        from types import SimpleNamespace
        class FakeRpc:
            def call(self, method, params):
                return {"eth_chainId": "0x89", "eth_blockNumber": hex(99_000_000), "eth_gasPrice": hex(25_000_000_000)}[method]
            def eth_call(self, to, calldata, block):
                if to == collector.VAULT and calldata == "0x" + collector.SEL_COLLECTOR:
                    return "0x" + collector.word_address("0x" + "c" * 40)
                if to == "0x" + "c" * 40 and calldata == "0x" + collector.SEL_FLASH_FEE:
                    return "0x" + f"{0:064x}"
                if calldata.startswith("0x" + collector.SEL_GET_PAIR):
                    return "0x" + collector.word_address("0x" + "a" * 40)
                if calldata == "0x" + collector.SEL_TOKEN0:
                    return "0x" + collector.word_address(collector.WPOL)
                if calldata == "0x" + collector.SEL_TOKEN1:
                    return "0x" + collector.word_address(collector.DAI)
                if calldata == "0x" + collector.SEL_RESERVES:
                    return "0x" + f"{100*10**18:064x}{300000*10**18:064x}{1:064x}"
                raise ValueError("unknown mock request")
        args = SimpleNamespace(block=96179857, scenario="synthetic_fork", gas_used=428064,
             principal_raw=5*10**18, received_raw=7451954393802018299,
             flash_fee_raw=0, gas_units_multiplier_bps=15000,
             gas_price_multiplier_bps=20000, min_net_profit_bps=20)
        evidence = collector.collect(FakeRpc(), args)
        self.assertTrue(evidence["flash_fee_historical_rpc_read"])
        self.assertTrue(evidence["conversion_historical_rpc_read"])
        self.assertEqual(evidence["principal_raw"], "5000000000000000000")
        self.assertEqual(evidence["balancer_fee_rate_wad"], "0")
        self.assertNotIn("POLYGON_RPC_URL", evidence)

    def test_malicious_addresses_rejected(self):
        with self.assertRaises(ValueError): collector.word_address("garbage")
        with self.assertRaises(ValueError): collector.decode_address("0xdeadbeef")


if __name__ == "__main__":
    unittest.main()
