#!/usr/bin/env python3
"""Read-only Polygon Uniswap V3 fee-100 depth & activity feasibility screen."""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from decimal import Decimal, localcontext
from pathlib import Path

WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
EXPECTED_BLOCK = 96179799
EXPECTED_HASH = '0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4'
SWAP_TOPIC = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
Q96 = Decimal(1 << 96)
POOL_PAIRS = {
    'USDT_WETH': {
        'stable': 'USDT', 'stable_addr': '0xc2132d05d31c914a87c6611c10748aeb04b58e8f', 'stable_position': 'token1',
        'fee100': '0x3840d6a1b96292c8e44991b5605e03245584585b',
        'fee500': '0xbb98b3d2b18aef63a3178023a920971cf5f29be4',
    },
    'USDCe_WETH': {
        'stable': 'USDC.e', 'stable_addr': '0x2791bca1f2de4661ed88a30c99a7a9449aa84174', 'stable_position': 'token0',
        'fee100': '0x04537f43f6add7b1b60cab199c7a910024ee0594',
        'fee500': '0x45dda9cb7c25131df268515131f647d726f50608',
    },
}


def decode_slot0(raw):
    if not isinstance(raw, str) or not raw.startswith('0x') or len(raw) < 2 + 64*2 or (len(raw)-2) % 64:
        raise ValueError('invalid slot0 response')
    words = [int(raw[i:i+64], 16) for i in range(2, len(raw), 64)]
    sqrt_x96 = words[0]
    tick = words[1]
    if tick & (1 << 255):
        tick -= 1 << 256
    if not (4295128739 <= sqrt_x96 < 1461446703485210103287273052203988822378723970342):
        raise ValueError('sqrt price outside Uniswap V3 range')
    if not (-887272 <= tick <= 887272):
        raise ValueError('invalid tick')
    return sqrt_x96, tick


def price_and_local_depth(sqrt_x96, liquidity, stable_position):
    if liquidity <= 0:
        raise ValueError('zero active liquidity')
    with localcontext() as ctx:
        ctx.prec = 75
        sqrtp = Decimal(sqrt_x96) / Q96
        # USDT/WETH: WETH=token0, stable=token1; USDC.e/WETH: stable=token0, WETH=token1.
        # Calculate stablecoin units per WETH with exact token-decimal normalization.
        if stable_position == 'token1':
            stable_per_weth = sqrtp*sqrtp * (Decimal(10) ** 12)
            stable_in_for_1bp = (Decimal(liquidity) * sqrtp * (Decimal('1.0001').sqrt() - 1)) / Decimal(10**6)
        elif stable_position == 'token0':
            stable_per_weth = (Decimal(10) ** 12) / (sqrtp*sqrtp)
            stable_in_for_1bp = (Decimal(liquidity) * (Decimal('1.0001').sqrt() - 1) / sqrtp) / Decimal(10**6)
        else:
            raise ValueError('Unknown token ordering')
        # Local approximation only: any initialized tick crossing changes active liquidity.
        return stable_per_weth, stable_in_for_1bp


class RPC:
    def __init__(self, url, budget=12, seconds=115):
        if not url.startswith('https://'):
            raise ValueError('POLYGON_RPC_URL must be an HTTPS URL')
        self.url = url
        self.count = 0
        self.budget = budget
        self.start = time.monotonic()
        self.seconds = seconds

    def call(self, method, params):
        if self.count >= self.budget:
            raise RuntimeError('RPC_REQUEST_BUDGET_EXCEEDED')
        if time.monotonic() - self.start > self.seconds:
            raise RuntimeError('WALL_CLOCK_LIMIT_EXCEEDED')
        self.count += 1
        payload = json.dumps({'jsonrpc':'2.0','id':self.count,'method':method,'params':params}).encode()
        req = urllib.request.Request(self.url, data=payload, headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read(2_000_001)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            raise RuntimeError(f'RPC_HTTP_OR_TIMEOUT:{type(e).__name__}') from e
        if len(raw) > 2_000_000:
            raise RuntimeError('RPC_RESPONSE_SIZE_LIMIT_EXCEEDED')
        response = json.loads(raw)
        if not isinstance(response, dict) or 'error' in response or 'result' not in response:
            error = response.get('error', {}) if isinstance(response, dict) else {}
            raise RuntimeError('RPC_ERROR:'+str(error.get('code', 'MALFORMED')))
        return response['result']


def uint_getter(rpc, address, selector, block):
    res = rpc.call('eth_call', [{'to':address, 'data':selector}, hex(block)])
    if not isinstance(res, str) or len(res) != 66:
        raise ValueError('invalid uint getter')
    return int(res, 16)


def snapshot(rpc, address, block, stable_position):
    res = rpc.call('eth_call', [{'to':address, 'data':'0x3850c7bd'}, hex(block)])
    sqrt_x96, tick = decode_slot0(res)
    liquidity = uint_getter(rpc, address, '0x1a686502', block)
    p, local = price_and_local_depth(sqrt_x96, liquidity, stable_position)
    return {'pool':address, 'tick':tick, 'sqrtPriceX96':str(sqrt_x96),
            'active_liquidity':str(liquidity), 'stable_position':stable_position, 'stable_per_weth':str(p),
            'indicative_stable_input_for_1bp_up_move':str(local)}


def fee_100_vs_500_ceiling(price100, price500):
    with localcontext() as ctx:
        ctx.prec = 65
        ratio = price100 / price500
        advantage = max(ratio, Decimal(1)/ratio)
        gross = (advantage-1) * 10_000
        # Spot-only optimistic direction; ignores price movement on both swaps and costs.
        ceiling = (advantage * Decimal('0.9999') * Decimal('0.9995') - 1) * 10_000
        return gross, ceiling


def run(args):
    if args.block != EXPECTED_BLOCK or args.from_block != 96179600 or args.to_block != 96179799:
        raise ValueError('Only validated fixed 200-block research window is allowed')
    rpc = RPC(os.environ.get('POLYGON_RPC_URL', ''))
    chain = rpc.call('eth_chainId', [])
    if int(chain, 16) != 137:
        raise RuntimeError('WRONG_CHAIN')
    block = rpc.call('eth_getBlockByNumber', [hex(args.block), False])
    if not isinstance(block, dict) or block.get('hash','').lower() != EXPECTED_HASH:
        raise RuntimeError('HISTORICAL_BLOCK_HASH_MISMATCH')
    report = {'step':'11.16', 'block':args.block, 'block_hash':EXPECTED_HASH,
              'scan_from':args.from_block, 'scan_to':args.to_block,
              'pairs':{}, 'verdict':'DEPTH_ACTIVITY_SCREEN_ONLY_NO_EXECUTION'}
    for pair, meta in POOL_PAIRS.items():
        required_position = 'token0' if int(meta['stable_addr'], 16) < int(WETH, 16) else 'token1'
        if meta['stable_position'] != required_position:
            raise ValueError('TOKEN_ORDER_CONFIGURATION_MISMATCH')
        low = snapshot(rpc, meta['fee100'], args.block, meta['stable_position'])
        high = snapshot(rpc, meta['fee500'], args.block, meta['stable_position'])
        logs = rpc.call('eth_getLogs', [{'fromBlock':hex(args.from_block),
            'toBlock':hex(args.to_block), 'address':meta['fee100'], 'topics':[SWAP_TOPIC]}])
        if not isinstance(logs, list) or len(logs) > 500:
            raise ValueError('unexpected number of swap logs')
        selected = []
        for log in logs:
            if (log.get('address','').lower() != meta['fee100'] or
                log.get('topics',[None])[0].lower() != SWAP_TOPIC or
                not args.from_block <= int(log['blockNumber'], 16) <= args.to_block):
                raise ValueError('inconsistent swap log')
            selected.append({'block':int(log['blockNumber'],16), 'tx':log['transactionHash'],
                             'log_index':int(log['logIndex'],16)})
        price100 = Decimal(low['stable_per_weth'])
        price500 = Decimal(high['stable_per_weth'])
        spread, ceiling = fee_100_vs_500_ceiling(price100, price500)
        report['pairs'][pair] = {
            '100_pool':low, '500_pool':high,
            '100_fee_swap_logs_200_blocks':len(selected),
            '100_fee_distinct_swap_txs':len({x['tx'] for x in selected}),
            'sample_swap_txs':selected[:3],
            'block_end_spot_spread_bps':str(spread),
            'block_end_100_500_fee_only_ceiling_bps':str(ceiling),
            'interpretation':'Block-end screening only; non-executable; local depth assumes no tick crossing'
        }
        print(pair, 'fee100 swaps:',len(selected),
              'price:',format(price100, '.4f'),
              '1bp local input:',format(Decimal(low['indicative_stable_input_for_1bp_up_move']),'.6f'),
              'spot spread bps:',format(spread,'.6f'),
              'fee-only ceiling bps:',format(ceiling,'.6f'))
    report['rpc_calls'] = rpc.count
    p = Path(args.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2)+'\n')
    print('STEP 11.16 — 100-FEE ACTIVITY AND LOCAL DEPTH — READ ONLY')
    print('verdict:', report['verdict'], 'rpc_calls:',rpc.count,'/ 12', 'block_traces: 0')
    print('saved:', str(p))
    print('NO INTRABLOCK PROOF / NO TICK REPLAY / NO EXECUTION / NO SIGNING / NO BROADCAST')


if __name__ == '__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--block', type=int, default=96179799)
    a.add_argument('--from-block', type=int, default=96179600)
    a.add_argument('--to-block', type=int, default=96179799)
    a.add_argument('--output', default='outputs/step11_16_depth_activity.json')
    try:
        run(a.parse_args())
    except Exception as exc:
        print('FAIL_CLOSED:',type(exc).__name__,str(exc)[:180], file=sys.stderr)
        sys.exit(1)
