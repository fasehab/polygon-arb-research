#!/usr/bin/env python3
"""Step 11.18: bounded read-only depth/activity screening of Uni V3 500/3000 pools.

Historical END-OF-BLOCK quotations, not intrablock state, executable net P&L,
route matching, or a transaction authorization. Standard library only.
"""
import json
import os
import time
import urllib.request
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 80
BLOCK = 96179799
BLOCK_HASH = '0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4'
CHAIN_ID = 137
FACTORY = '0x1f98431c8ad98523631ae4a59f267346ea31f984'
QUOTER = '0x61ffe014ba17989e743c5f6cb21bf9697530b21e'
WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
STABLES = {'USDT': '0xc2132d05d31c914a87c6611c10748aeb04b58e8f',
           'USDCe': '0x2791bca1f2de4661ed88a30c99a7a9449aa84174'}
FEES = (500, 3000)
SIZES = (10, 100, 1000)  # human stable token units, 6 decimals
MAX_RPC = 44
DEADLINE = 180
MAX_BYTES = 200_000
SWAP_TOPIC = '0xc42079f94a6350d7e6235f291749ac2e768ac9dae5a1af2e6412d253403c8a'
OUTPUT = Path('outputs/step11_18_depth_first.json')


def abi_word(num):
    assert 0 <= int(num) < 2**256
    return f'{int(num):064x}'


def as_uint256(value):
    if not isinstance(value, str) or not value.startswith('0x') or len(value) != 66:
        raise ValueError('MALFORMED_UINT256')
    return int(value, 16)


def decode_address(value):
    num = as_uint256(value)
    if num >= 2**160:
        raise ValueError('BAD_ADDRESS_ENCODING')
    return '0x' + f'{num:040x}'


def parse_sqrt_slot0(payload):
    if not isinstance(payload, str) or not payload.startswith('0x') or len(payload) < 66:
        raise ValueError('BAD_SLOT0')
    sqrt = int(payload[2:66], 16)
    if not 0 < sqrt < 2**160:
        raise ValueError('INVALID_SQRT')
    return sqrt


def ideal_stable_to_weth(amount_stable_raw, sqrt_price_x96, token0, stable, fee):
    p_raw = (Decimal(sqrt_price_x96) / Decimal(2**96)) ** 2
    if token0 == stable:  # stable is token0; WETH is token1
        weth_per_stable_raw = p_raw
    elif token0 == WETH:  # WETH is token0; stable is token1
        weth_per_stable_raw = Decimal(1) / p_raw
    else:
        raise ValueError('TOKEN_ORDER_MISMATCH')
    return Decimal(amount_stable_raw) * weth_per_stable_raw * (1 - Decimal(fee) / 1_000_000)


def quote_impact_bps(output_weth_raw, ideal_output_weth_raw):
    if ideal_output_weth_raw <= 0 or output_weth_raw <= 0:
        raise ValueError('INVALID_QUOTE')
    return (1 - Decimal(output_weth_raw) / ideal_output_weth_raw) * 10_000


class RPC:
    def __init__(self, url):
        if not url.startswith('https://'):
            raise ValueError('POLYGON_RPC_URL_MUST_BE_HTTPS')
        self.url = url
        self.count = 0
        self.end = time.monotonic() + DEADLINE

    def call(self, method, params):
        if self.count >= MAX_RPC or time.monotonic() >= self.end:
            raise RuntimeError('RPC_OR_TIME_BUDGET_EXCEEDED')
        self.count += 1
        body = json.dumps({'jsonrpc': '2.0', 'id': self.count,
                           'method': method, 'params': params}).encode()
        req = urllib.request.Request(self.url, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=min(20, max(1, self.end-time.monotonic()))) as resp:
            raw = resp.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RuntimeError('RPC_RESPONSE_CAP_EXCEEDED')
        obj = json.loads(raw)
        if 'error' in obj:
            raise RuntimeError('RPC_ERROR_CODE:' + str(obj['error'].get('code')))
        if 'result' not in obj:
            raise RuntimeError('MISSING_RPC_RESULT')
        return obj['result']

    def eth_call(self, contract, data):
        return self.call('eth_call', [{'to': contract, 'data': data, 'gas': hex(6_000_000)}, hex(BLOCK)])

    def getter(self, contract, selector):
        return as_uint256(self.eth_call(contract, selector))


def decode_quote(value):
    if not isinstance(value, str) or len(value) != 258 or not value.startswith('0x'):
        raise ValueError('MALFORMED_QUOTER_RESULT')
    fields = [int(value[2+i*64:2+(i+1)*64], 16) for i in range(4)]
    if fields[0] <= 0 or fields[1] <= 0 or fields[1] >= 2**160:
        raise ValueError('INVALID_QUOTER_VALUES')
    return {'amount_out_raw': fields[0], 'sqrt_after': fields[1],
            'initialized_ticks_crossed': fields[2], 'quoter_gas_estimate': fields[3]}


def pool_getter(rpc, stable, fee):
    data = ('0x1698ee82' + abi_word(int(stable, 16))
            + abi_word(int(WETH, 16)) + abi_word(fee))
    return decode_address(rpc.eth_call(FACTORY, data))


def quote(rpc, stable, fee, amount_raw):
    data = ('0xc6a5026a' + abi_word(int(stable, 16))
            + abi_word(int(WETH, 16)) + abi_word(amount_raw)
            + abi_word(fee) + abi_word(0))
    return decode_quote(rpc.eth_call(QUOTER, data))


def run():
    rpc = RPC(os.environ.get('POLYGON_RPC_URL', ''))
    if int(rpc.call('eth_chainId', []), 16) != CHAIN_ID:
        raise RuntimeError('WRONG_CHAIN')
    block = rpc.call('eth_getBlockByNumber', [hex(BLOCK), False])
    if not isinstance(block, dict) or block.get('hash', '').lower() != BLOCK_HASH:
        raise RuntimeError('BLOCK_HASH_MISMATCH')
    rows = []
    print('STEP 11.18 — DEPTH-FIRST UNISWAP V3 500/3000 SCREEN (READ ONLY)')
    print('block:', BLOCK, 'hash_verified: True')
    for stable_name, stable in STABLES.items():
        for fee in FEES:
            item = {'pair': stable_name + '_WETH', 'fee_units': fee,
                    'scope': 'HISTORICAL_END_OF_BLOCK_ONLY', 'quotes': []}
            rows.append(item)
            pool = pool_getter(rpc, stable, fee)
            item['pool'] = pool
            if int(pool, 16) == 0:
                item['status'] = 'POOL_NOT_DEPLOYED_AT_BLOCK'
                print(stable_name, 'fee=',fee,'pool=NONE')
                continue
            t0 = decode_address(rpc.eth_call(pool, '0x0dfe1681'))
            t1 = decode_address(rpc.eth_call(pool, '0xd21220a7'))
            onchain_fee = rpc.getter(pool, '0xddca3f43')
            if {t0, t1} != {stable, WETH} or onchain_fee != fee:
                item['status'] = 'REJECT_IDENTITY_OR_FEE'
                print(stable_name, 'fee=',fee,'REJECT_IDENTITY_OR_FEE')
                continue
            sqrt = parse_sqrt_slot0(rpc.eth_call(pool, '0x3850c7bd'))
            liquidity = rpc.getter(pool, '0x1a686502')
            item.update({'token0': t0, 'token1': t1, 'active_liquidity_raw': str(liquidity),
                         'sqrtPriceX96': str(sqrt)})
            if liquidity == 0:
                item['status'] = 'ZERO_ACTIVE_LIQUIDITY'
                print(stable_name, 'fee=',fee,'ZERO_ACTIVE_LIQUIDITY')
                continue
            try:
                logs = rpc.call('eth_getLogs', [{
                    'address': pool, 'fromBlock': hex(BLOCK - 199),
                    'toBlock': hex(BLOCK), 'topics': [SWAP_TOPIC]
                }])
                item['swap_events_200_blocks'] = len(logs) if isinstance(logs, list) else None
            except RuntimeError as ex:
                if 'BUDGET' in str(ex):
                    raise
                item['swap_events_200_blocks'] = None
                item['activity_note'] = str(ex)
            impacts = {}
            for size in SIZES:
                q = {'stable_input_units': size}
                item['quotes'].append(q)
                try:
                    result = quote(rpc, stable, fee, size * 1_000_000)
                    ideal = ideal_stable_to_weth(size * 1_000_000, sqrt, t0, stable, fee)
                    impact = quote_impact_bps(result['amount_out_raw'], ideal)
                    if impact < Decimal('-0.1'):
                        raise ValueError('QUOTE_EXCEEDS_FEE_ADJUSTED_SPOT')
                    q.update({'status': 'QUOTED', 'weth_out_raw': str(result['amount_out_raw']),
                              'deterioration_vs_fee_adjusted_spot_bps': str(impact),
                              'ticks_crossed': result['initialized_ticks_crossed'],
                              'quoter_gas_estimate_not_transaction_gas': result['quoter_gas_estimate']})
                    impacts[size] = impact
                except (RuntimeError, ValueError) as ex:
                    if 'BUDGET' in str(ex):
                        raise
                    q.update({'status': 'UNAVAILABLE', 'reason': str(ex)})
            promising = (100 in impacts and 1000 in impacts
                         and impacts[100] <= 10 and impacts[1000] <= 30)
            item['status'] = 'PROMISING_DEPTH_ONLY' if promising else 'INSUFFICIENT_DEPTH_AT_TESTED_SIZES'
            disp = lambda size: f'{impacts[size]:.4f}' if size in impacts else 'N/A'
            print(f'{stable_name}_WETH fee={fee} swaps200={item.get("swap_events_200_blocks")} '
                  f'deterioration_100_bps={disp(100)} '
                  f'deterioration_1000_bps={disp(1000)} verdict={item["status"]}')
    doc = {'step': '11.18', 'block': BLOCK, 'block_hash': BLOCK_HASH, 'chain_id': CHAIN_ID,
           'scope': 'END_OF_BLOCK_ONLY_NOT_INTRABLOCK',
           'method': 'Historical UniswapV3 QuoterV2 exact-input stable-to-WETH quotations',
           'safety': 'READ_ONLY_NO_SIGNING_NO_BROADCAST_NO_NET_PROFIT_CLAIM',
           'note': 'Deterioration is measured against fee-adjusted spot, not full gas or round-trip PnL. '
                   '100/1000 stable token units are indicative trade size. Quoter gas is not executor gas.',
           'pools': rows, 'rpc_calls': rpc.count, 'rpc_limit': MAX_RPC,
           'verdict': 'DEPTH_SCREEN_ONLY_NO_EXECUTION_AUTHORIZATION'}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(doc, indent=2) + '\n')
    print('rpc_calls:', rpc.count, '/', MAX_RPC)
    print('saved:', OUTPUT)
    print('NO INTRABLOCK PROOF / NO TWO-POOL PROFIT / NO SIGNING / NO BROADCAST')


if __name__ == '__main__':
    run()
