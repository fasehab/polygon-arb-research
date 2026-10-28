import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'eth_call_preflight.py'
spec = importlib.util.spec_from_file_location('preflight', SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
A = '0x'+'a'*40
B = '0x'+'b'*40
SEL = '0x12345678'
DATA = SEL + '00'*32

class PreflightTests(unittest.TestCase):
    def test_wrong_selector_rejected(self):
        with self.assertRaises(ValueError):
            p.check_inputs(A, B, DATA, '0x87654321')
    def test_valid_mock_readonly_success(self):
        methods = []
        def mock(method, args):
            methods.append(method)
            return {'eth_chainId':'0x89', 'eth_blockNumber':'0x123',
                    'eth_getCode':'0x60806040', 'eth_call':'0x'+'0'*24+B[2:]}[method]
        block = p.check_contract(mock,A,B,DATA,SEL)
        self.assertEqual(block,'0x123')
        self.assertEqual(methods, ['eth_chainId','eth_blockNumber','eth_getCode','eth_call','eth_call'])
        self.assertTrue(all(m != 'eth_sendRawTransaction' for m in methods))
    def test_rejects_no_code(self):
        def mock(m,params):
            return {'eth_chainId':'0x89','eth_blockNumber':'0x1','eth_getCode':'0x'}.get(m)
        with self.assertRaises(p.RPCError):
            p.check_contract(mock,A,B,DATA,SEL)
    def test_rejects_wrong_chain(self):
        with self.assertRaises(p.RPCError):
            p.check_contract(lambda m,args: '0x1', A,B,DATA,SEL)
    def test_owner_mismatch_is_blocked(self):
        def mock(m,args):
            return {'eth_chainId':'0x89','eth_blockNumber':'0x1',
                    'eth_getCode':'0x6060','eth_call':'0x'+'0'*24+A[2:]}[m]
        with self.assertRaises(p.RPCError):
            p.check_contract(mock,A,B,DATA,SEL)

if __name__ == '__main__': unittest.main()
