import unittest
import copy
import json
from unittest.mock import patch
from step11_2_v3_collect import (V3Rpc, QuoteRevert, EvidenceError, ProviderError,
    quote_alg, quote_uni, collect, valid_code, ALG_FACTORY, ALG_QUOTER,
    UNI_FACTORY, UNI_QUOTER_V2, ALG_QUOTE, UNI_QUOTE_V2, ALG_POOL_BY_PAIR, UNI_GET_POOL)
from step11_collect import word, TOKENS, PAIRS

class TestV3(unittest.TestCase):
 def test_selectors_are_real_abi_selectors(self):
  self.assertEqual((ALG_QUOTE,UNI_QUOTE_V2,ALG_POOL_BY_PAIR,UNI_GET_POOL),
                   ('2d9ebd1d','c6a5026a','d9a641e1','1698ee82'))
 def test_quoter_code_lengths(self):
  self.assertEqual(len('0x'+ALG_QUOTE+word(1)*4),266)
  self.assertEqual(len('0x'+UNI_QUOTE_V2+word(1)*5),330)
 def test_decode_algebra_two_words(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x'+word(520)+word(250))
  self.assertEqual(quote_alg(rpc,TOKENS['DAI'][0],TOKENS['WETH'][0],100,100), (520,250))
 def test_reject_unsafe_algebra_fee(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x'+word(520)+word(10000))
  with self.assertRaises(EvidenceError):quote_alg(rpc,TOKENS['DAI'][0],TOKENS['WETH'][0],100,100)
 def test_decode_uniswap_four_words(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x'+''.join(map(word,[520,2**96,0,45000])))
  self.assertEqual(quote_uni(rpc,TOKENS['DAI'][0],TOKENS['WETH'][0],100,500,100), (520,45000))
 def test_reject_malformed_uniswap(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x'+word(999))
  with self.assertRaises(EvidenceError):quote_uni(rpc,TOKENS['DAI'][0],TOKENS['WETH'][0],100,500,100)
 def test_rpc_allowlist(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x1')
  with self.assertRaises(EvidenceError):rpc.call('eth_sendRawTransaction',['anything'])
 def test_missing_code_fails(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x')
  with self.assertRaises(EvidenceError):valid_code(rpc,ALG_FACTORY,123)
 def test_bad_url(self):
  with self.assertRaises(EvidenceError):V3Rpc('http://rpc.invalid')
 def test_zero_algebra_quote(self):
  rpc=V3Rpc('https://rpc.invalid',transport=lambda m,p:'0x'+word(0)+word(250))
  with self.assertRaises(EvidenceError):quote_alg(rpc,TOKENS['DAI'][0],TOKENS['WETH'][0],100,100)
 def test_readonly_fallback_restarts_block(self):
  calls=[]
  def block(rpc,num,sizes,tiers):
   calls.append((id(rpc),num))
   if rpc is primary: raise ProviderError('rate limited')
   return {'number':num,'hash':'0x'+'a'*64,'fee_rate_wad':'0','pairs':[]}
  primary=object();fallback=object()
  with patch('step11_2_v3_collect.collect_block',side_effect=block):
   o=collect(primary,fallback,[100],[1],[500])
  self.assertEqual(calls,[(id(primary),100),(id(fallback),100)])
  self.assertEqual(o['blocks'][0]['provider_role'],'fallback')
 def test_no_fallback_aborts(self):
  with patch('step11_2_v3_collect.collect_block',side_effect=ProviderError('bad')):
   with self.assertRaises(ProviderError):collect(object(),None,[100],[1],[500])
 def test_repeated_blocks_rejected(self):
  with self.assertRaises(EvidenceError):collect(object(),None,[100,100],[1],[500])
 def test_repeated_sizes_rejected(self):
  with self.assertRaises(EvidenceError):collect(object(),None,[100],[1,1],[500])
 def test_invalid_tiers_rejected(self):
  with self.assertRaises(EvidenceError):collect(object(),None,[100],[1],[100])
 def test_positive_never_execution_approved(self):
  with patch('step11_2_v3_collect.collect_block',return_value={'number':100,'hash':'0x'+'a'*64,'fee_rate_wad':'0','pairs':[]}):
   x=collect(object(),None,[100],[1],[500])
  self.assertEqual(x['safety']['executor_supports_v3'],False)
  self.assertEqual(x['safety']['writes_or_broadcasts_transactions'],False)

if __name__=='__main__':unittest.main()
