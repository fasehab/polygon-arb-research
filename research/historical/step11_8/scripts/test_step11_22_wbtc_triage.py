import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import step11_22_wbtc_triage as m
class Step1122(unittest.TestCase):
    def test_selectors(self):
        self.assertTrue(m.pool_pair_data(m.USDT).startswith('0xd9a641e1'))
        self.assertTrue(m.pool_pair_data(m.USDT,500).startswith('0x1698ee82'))
    def test_address_zero(self):
        self.assertEqual(m.address('0x'+'0'*40,True),m.ZERO)
        with self.assertRaises(m.Halt):m.address(m.ZERO)
    def test_address_bad(self):
        with self.assertRaises(m.Halt):m.address('0x123')
    def test_uint_strict(self):
        self.assertEqual(m.abi_uint('0x'+'0'*63+'1'),1)
        with self.assertRaises(m.Halt):m.abi_uint('0x1')
    def test_get_pool(self):
        class R:
            def eth_call(self,f,d):
                self.f,self.d=f,d
                return '0x'+m.word(int(m.WBTC,16))
        r=R();self.assertEqual(m.get_pool(r,m.USDT),m.WBTC)
        self.assertEqual(r.f,m.ALG_FACTORY)
        self.assertEqual(m.get_pool(r,m.USDT,100),m.WBTC)
        self.assertEqual(r.f,m.UNI_FACTORY)
    def test_sqrt_getter(self):
        self.assertEqual(m.parse_sqrt('0x'+m.word(123456)+m.word(0)*6,False),123456)
        self.assertEqual(m.parse_sqrt('0x'+m.word(123456)+m.word(0)*5,True),123456)
        with self.assertRaises(m.Halt):m.parse_sqrt('0x'+m.word(123456),True)
    def test_spread_zero(self):
        self.assertEqual(m.bps(123,123),0)
    def test_spread_rejects_zero(self):
        with self.assertRaises(m.Halt):m.bps(0,123)
    def test_pool_identity(self):
        class R:
            def eth_call(self,addr,selector):
                return {'0x0dfe1681':'0x'+m.word(int(m.USDT,16)),
                        '0xd21220a7':'0x'+m.word(int(m.WBTC,16)),
                        '0x1a686502':'0x'+m.word(123),
                        '0x3850c7bd':'0x'+m.word(55)*7,
                        '0xddca3f43':'0x'+m.word(500)}[selector]
        with self.assertRaises(m.Halt):m.check_pool(R(),m.WBTC,m.USDT,500)
    def test_pool_fee(self):
        class R:
            def eth_call(self,addr,selector):
                return {'0x0dfe1681':'0x'+m.word(int(m.WBTC,16)),
                        '0xd21220a7':'0x'+m.word(int(m.USDT,16)),
                        '0x1a686502':'0x'+m.word(123),
                        '0x3850c7bd':'0x'+m.word(55)*7,
                        '0xddca3f43':'0x'+m.word(100)}[selector]
        with self.assertRaises(m.Halt):m.check_pool(R(),m.WBTC,m.USDT,500)
    def test_pool_valid(self):
        class R:
            def eth_call(self,addr,selector):
                return {'0x0dfe1681':'0x'+m.word(int(m.WBTC,16)),
                        '0xd21220a7':'0x'+m.word(int(m.USDT,16)),
                        '0x1a686502':'0x'+m.word(123),
                        '0x3850c7bd':'0x'+m.word(55)*7,
                        '0xddca3f43':'0x'+m.word(500)}[selector]
        v=m.check_pool(R(),m.WBTC,m.USDT,500)
        self.assertEqual(v['active_liquidity_raw'],123)
        self.assertEqual(v['sqrt_price_x96'],'55')
    def test_no_synthetic_success(self):
        self.assertEqual(m.OUTPUT.name,'step11_22_wbtc_triage.json')
        self.assertEqual(m.MAX_RPC,42)
        self.assertEqual(m.BLOCK,96179799)
if __name__=='__main__':unittest.main()
