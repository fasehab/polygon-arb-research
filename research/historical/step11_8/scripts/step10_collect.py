#!/usr/bin/env python3
"""Step 10: READ-ONLY Polygon historical DAI/WETH V2 route screening.

Reads real state with eth_call at ONE pinned block. This is quote evidence,
NOT an eth_call of the executor, a gas measurement, or execution authorization.
No signer, mnemonic, wallet, account management, state override, or send RPC.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

CHAIN = 137
DEFAULT_BLOCK = 96179857
VAULT = '0xba12222222228d8ba445958a75a0704d566bf2c8'
DAI = '0x8f3cf7ad23cd3cadbd9735aff958023239c6a063'
WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
QUICK = '0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff'
SUSHI = '0x1b02da8cb0d097eb8d57a175b88c7d8b47997506'
QUICK_FACTORY = '0x5757371414417b8c6caad45baef941abc7d3ab32'
SUSHI_FACTORY = '0xc35dadb65012ec5796536bd9864ed8773abc74c4'
SEL_GETPAIR = 'e6a43905'
SEL_GETAMOUNTSOUT = 'd06ca61f'
SEL_RESERVES = '0902f1ac'
SEL_TOKEN0 = '0dfe1681'
SEL_TOKEN1 = 'd21220a7'
SEL_BALANCE = '70a08231'
SEL_COLLECTOR = 'd2946c2b'
SEL_FLASHFEE = 'd877845c'


def word(value):
    if not isinstance(value, int) or not 0 <= value < 2**256:
        raise ValueError('invalid unsigned ABI integer')
    return f'{value:064x}'


def addr(value):
    if not isinstance(value, str) or len(value) != 42 or not value.startswith('0x'):
        raise ValueError('invalid EVM address')
    int(value[2:], 16)
    return value.lower()


def aword(value):
    return word(int(addr(value), 16))


def words(value, min_count=1):
    if not isinstance(value, str) or not value.startswith('0x'):
        raise ValueError('invalid ABI return')
    data = value[2:]
    if len(data) < 64*min_count or len(data) % 64:
        raise ValueError('short or malformed ABI return')
    return [int(data[i:i+64], 16) for i in range(0, len(data), 64)]


def decoded_address(value):
    return '0x' + f'{words(value)[0] & ((1 << 160)-1):040x}'


def encode_quote(amount, token0, token1):
    # getAmountsOut(uint256,address[]): amount; offset=0x40; length=2; two addresses.
    return '0x' + SEL_GETAMOUNTSOUT + word(amount) + word(64) + word(2) + aword(token0) + aword(token1)


def decode_quote(value, amount):
    w = words(value, 4)
    if w[0] != 32 or w[1] != 2 or len(w) != 4 or w[2] != amount or w[3] <= 0:
        raise ValueError('malformed router quote or input mismatch')
    return w[3]


class Rpc:
    def __init__(self, url):
        if not url.startswith('https://'):
            raise ValueError('POLYGON_RPC_URL must be an HTTPS Polygon RPC URL')
        self.url = url
        self.counter = 0

    def call(self, method, params):
        self.counter += 1
        request = urllib.request.Request(self.url,
            data=json.dumps({'jsonrpc': '2.0', 'id': self.counter, 'method': method, 'params': params}).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
        if result.get('error'):
            raise RuntimeError('RPC rejected request (details intentionally withheld)')
        if not isinstance(result.get('result'), str):
            raise RuntimeError('RPC returned an unexpected non-string result')
        return result['result']


def at(rpc, to, calldata, block):
    return rpc.call('eth_call', [{'to': addr(to), 'data': calldata}, hex(block)])


def read_uint(rpc, to, selector, block):
    return words(at(rpc, to, '0x'+selector, block))[0]


def read_pair(rpc, factory, block):
    pair = decoded_address(at(rpc, factory, '0x'+SEL_GETPAIR+aword(DAI)+aword(WETH), block))
    if int(pair,16) == 0:
        raise ValueError('missing historical V2 pair')
    if rpc.call('eth_getCode', [pair, hex(block)]) == '0x':
        raise ValueError('V2 pair has no historical bytecode')
    t0 = decoded_address(at(rpc, pair, '0x'+SEL_TOKEN0, block))
    t1 = decoded_address(at(rpc, pair, '0x'+SEL_TOKEN1, block))
    if {t0,t1} != {DAI,WETH} or t0 == t1:
        raise ValueError('V2 pair tokens disagree with requested DAI/WETH')
    reserve_words = words(at(rpc, pair, '0x'+SEL_RESERVES, block), 3)
    if len(reserve_words) != 3 or any(v == 0 or v >= 2**112 for v in reserve_words[:2]):
        raise ValueError('missing or impossible pair reserves')
    return {'address': pair, 'token0':t0, 'token1':t1,
            'reserve0_raw':str(reserve_words[0]),'reserve1_raw':str(reserve_words[1])}


def quote(rpc, router, amount, inp, out, block):
    return decode_quote(at(rpc, router, encode_quote(amount,inp,out), block), amount)


def flash_fee(amount, wad):
    if wad < 0 or wad > 10**16:
        raise ValueError('unusual Balancer flash fee; manual review required')
    return (amount*wad + 10**18 - 1)//10**18


def collect(rpc, block, amounts):
    if int(rpc.call('eth_chainId', []), 16) != CHAIN:
        raise ValueError('RPC chain is not Polygon 137')
    if block != DEFAULT_BLOCK:
        raise ValueError('this baseline is verified ONLY at block 96179857; do not mix fork dates')
    live = int(rpc.call('eth_blockNumber', []), 16)
    if live < block:
        raise ValueError('RPC has not reached historical block')
    vault_balance = read_uint(rpc, DAI, SEL_BALANCE+aword(VAULT), block)
    collector = decoded_address(at(rpc, VAULT, '0x'+SEL_COLLECTOR, block))
    fee_wad = read_uint(rpc, collector, SEL_FLASHFEE, block)
    if fee_wad > 10**16:
        raise ValueError('unusual Balancer flash fee')
    qp = read_pair(rpc, QUICK_FACTORY, block)
    sp = read_pair(rpc, SUSHI_FACTORY, block)
    if qp['address'] == sp['address']:
        raise ValueError('both factories returned the same pool')
    records = []
    for amount in amounts:
        if amount <= 0 or amount >= 2**256:
            raise ValueError('invalid principal')
        for leg1, leg2, name in ((QUICK,SUSHI,'quick_then_sushi'),(SUSHI,QUICK,'sushi_then_quick')):
            first = quote(rpc, leg1, amount, DAI, WETH, block)
            end = quote(rpc, leg2, first, WETH, DAI, block)
            fee = flash_fee(amount, fee_wad)
            records.append({'route':name,'principal_raw':str(amount),'first_out_weth_raw':str(first),
                            'second_out_dai_raw':str(end),'flash_fee_raw':str(fee)})
    return {'schema_version':1,'evidence_kind':'unmodified_historical_v2_quotes_only',
            'chain_id':CHAIN,'block':block,'borrow_token':DAI,'intermediate_token':WETH,
            'vault':VAULT,'vault_dai_balance_raw':str(vault_balance),
            'fee_rate_wad':str(fee_wad),'quick_router':QUICK,'sushi_router':SUSHI,
            'quick_factory':QUICK_FACTORY,'sushi_factory':SUSHI_FACTORY,
            'quick_pair':qp,'sushi_pair':sp,'quotes':records,
            'safety':{'uses_state_overrides':False,'writes_or_broadcasts_transactions':False,
                      'proves_executor_eth_call':False,'proves_gas_or_net_profit':False},
            'limitations':['quotes are historical eth_call only, not a full executor simulation',
                           'gas, inclusion, balance-changing transfer behaviors and competition are unproven',
                           'a gross-positive quote requires a separate same-state fork test and gas proof',
                           'NEVER authorize signing or broadcast']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--block',type=int,default=DEFAULT_BLOCK)
    parser.add_argument('--dai',nargs='+',type=int,default=[1,5,10,25,50],
                        help='whole DAI flash-loan principal sizes (18 decimals)')
    parser.add_argument('--output',default='outputs/step10_historical_quotes.json')
    args=parser.parse_args()
    if len(args.dai)>12 or len(set(args.dai))!=len(args.dai) or not all(0<z<=1000 for z in args.dai):
        parser.error('provide 1..12 unique principal sizes, each 1..1000 whole DAI')
    url = os.environ.get('POLYGON_RPC_URL','')
    if not url:
        parser.error('POLYGON_RPC_URL must be configured securely in this shell')
    try:
        evidence=collect(Rpc(url), args.block, [x*10**18 for x in args.dai])
        out=Path(args.output)
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n',encoding='utf-8')
        print(f'STEP 10: saved {len(evidence["quotes"])} pinned-block real quote records to {out}')
        print('READ-ONLY — actual Mainnet historical state, no fork donations or RPC state overrides')
        print('NEVER SIGN OR BROADCAST. Run the independent Rust evaluator next.')
    except Exception as exc:
        # Never print provider URL or potentially sensitive network error text.
        print('STEP 10 FAIL-CLOSED: RPC evidence incomplete or invalid:', type(exc).__name__,file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':
    main()
