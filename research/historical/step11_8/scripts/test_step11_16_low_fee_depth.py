import unittest
from decimal import Decimal
from step11_16_low_fee_depth import decode_slot0, price_and_local_depth, fee_100_vs_500_ceiling, EXPECTED_HASH, POOL_PAIRS

class TestStep1116(unittest.TestCase):
    def test_slot0_decode(self):
        sqrt = 3830065832365186024580722
        tick = -198755
        data = '0x' + format(sqrt, '064x') + format((1<<256)+tick, '064x') + ('0'*64)*5
        self.assertEqual((sqrt,tick),decode_slot0(data))
    def test_slot0_short_rejected(self):
        with self.assertRaises(ValueError): decode_slot0('0x01')
    def test_slot0_zero_rejected(self):
        with self.assertRaises(ValueError): decode_slot0('0x'+('0'*64)*7)
    def test_price_positive(self):
        p, d = price_and_local_depth(3830065832365186024580722, 18607054910371, 'token1')
        self.assertGreater(p, 1000)
        self.assertLess(p, 4000)
        self.assertGreater(d, Decimal('0.01'))
        self.assertLess(d, Decimal('1'))
    def test_zero_liquidity_rejected(self):
        with self.assertRaises(ValueError): price_and_local_depth(3830065832365186024580722,0, 'token1')
    def test_token0_stable_inverse_price(self):
        # USDC.e token0 / WETH token1: s^2 = 1e12 / 2500, i.e. 2500 stablecoin/WETH.
        from step11_16_low_fee_depth import Q96
        with __import__('decimal').localcontext() as ctx:
            ctx.prec = 70
            sqrt_raw = int((Decimal(10)**12 / Decimal(2500)).sqrt() * Q96)
        p, d = price_and_local_depth(sqrt_raw, 145463609495526, 'token0')
        self.assertGreater(p, 2400)
        self.assertLess(p, 2600)
        self.assertGreater(d, Decimal('0.01'))
        self.assertLess(d, Decimal('10'))
    def test_invalid_order_rejected(self):
        with self.assertRaises(ValueError):
            price_and_local_depth(3830065832365186024580722, 18607054910371, 'bad')
    def test_equal_price_fails_two_fee_floor(self):
        gross, ceiling = fee_100_vs_500_ceiling(Decimal(100),Decimal(100))
        self.assertEqual(gross, 0)
        self.assertLess(ceiling, -5)
    def test_fee_floor_for_two_percents(self):
        gross, ceiling = fee_100_vs_500_ceiling(Decimal(100),Decimal(99.95))
        self.assertGreater(gross, 5)
        self.assertLess(ceiling, 0)
    def test_expected_pairs(self):
        self.assertEqual(len(POOL_PAIRS),2)
        self.assertEqual(len(EXPECTED_HASH),66)

if __name__ == '__main__': unittest.main()
