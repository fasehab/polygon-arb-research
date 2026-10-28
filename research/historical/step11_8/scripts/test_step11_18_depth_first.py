import importlib.util
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).with_name('step11_18_depth_first.py')
spec = importlib.util.spec_from_file_location('step11_18_depth_first', ROOT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MathTests(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(len(m.abi_word(500)), 64)
        self.assertRaises(AssertionError, m.abi_word, -1)
        self.assertEqual(m.decode_address('0x'+m.abi_word(1)), '0x' + '0'*39 + '1')

    def test_identity_encoding_rejects_overflow(self):
        self.assertRaises(ValueError, m.decode_address, '0x'+m.abi_word(2**161))

    def test_rejects_bad_uint(self):
        self.assertRaises(ValueError, m.as_uint256, '0x123')

    def test_sqrt_parse(self):
        self.assertEqual(m.parse_sqrt_slot0('0x'+m.abi_word(2**96)), 2**96)
        self.assertRaises(ValueError, m.parse_sqrt_slot0, '0x'+m.abi_word(0))

    def test_ideal_raw_if_stable_token0(self):
        q = m.ideal_stable_to_weth(1_000_000, 2**96, m.STABLES['USDT'], m.STABLES['USDT'], 500)
        self.assertEqual(q, Decimal('999500'))

    def test_ideal_raw_if_weth_token0(self):
        q = m.ideal_stable_to_weth(1_000_000, 2**96, m.WETH, m.STABLES['USDT'], 500)
        self.assertEqual(q, Decimal('999500'))

    def test_impact(self):
        self.assertEqual(m.quote_impact_bps(990, Decimal(1000)), Decimal(100))

    def test_quote_parser(self):
        words = [4, 2**96, 3, 20000]
        d = m.decode_quote('0x'+''.join(m.abi_word(v) for v in words))
        self.assertEqual(d['initialized_ticks_crossed'],3)
        self.assertEqual(d['amount_out_raw'],4)

    def test_invalid_quote_parser(self):
        self.assertRaises(ValueError, m.decode_quote, '0x')
        self.assertRaises(ValueError, m.decode_quote, '0x'+m.abi_word(0)*4)

    def test_requires_https(self):
        self.assertRaises(ValueError, m.RPC, 'http://example.com')


if __name__ == '__main__':
    unittest.main()
