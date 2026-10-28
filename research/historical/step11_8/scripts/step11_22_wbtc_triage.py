#!/usr/bin/env python3
"""Step 11.22: bounded, read-only WBTC pair discovery at one historical Polygon state.

Pool existence, identity, endblock price/liquidity, and 200-block Swap activity.
NOT a transaction-boundary comparison, tick-aware quote, or profit proof.
No signing, transaction sending, or external Python dependencies.
"""
import json
import os
import time
import urllib.request
from collections import Counter
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 85
BLOCK = 96179799
BLOCK_HASH = '0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4'
BEGIN = BLOCK - 199
ALG_FACTORY = '0x411b0facc3489691f28ad58c47006af5e3ab3a28'
UNI_FACTORY = '0x1f98431c8ad98523631ae4a59f267346ea31f984'
WBTC = '0x1bfd67037b42cf73acf2047067bd4f2c47d9bfd6'
USDT = '0xc2132d05d31c914a87c6611c10748aeb04b58e8f'
USDCe = '0x2791bca1f2de4661ed88a30c99a7a9449aa84174'
PAIRS = {'USDT_WBTC': USDT, 'USDCe_WBTC': USDCe}
SWAP_TOPIC = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
MAX_RPC = 42
MAX_REPLY = 2_000_000
TIME_BUDGET = 140
OUTPUT = Path('outputs/step11_22_wbtc_triage.json')
ZERO = '0x' + '0'*40

class Halt(RuntimeError):
    pass

def word(value):
    if not isinstance(value, int) or not 0 <= value < 2**256:
        raise Halt('BAD_ABI_WORD')
    return f'{value:064x}'

def address(value, allow_zero=False):
    if not isinstance(value, str) or len(value) != 42 or value[:2] != '0x' or any(c not in '0123456789abcdefABCDEF' for c in value[2:]):
        raise Halt('BAD_ADDRESS')
    ret = value.lower()
    if not allow_zero and ret == ZERO:
        raise Halt('ZERO_POOL')
    return ret

def abi_uint(result):
    if not isinstance(result, str) or not result.startswith('0x') or len(result) != 66:
        raise Halt('MALFORMED_UINT256')
    try:
        return int(result, 16)
    except ValueError:
        raise Halt('MALFORMED_UINT256') from None

def abi_addr(result):
    val = abi_uint(result)
    if val >= 2**160:
        raise Halt('BAD_ADDRESS_PADDING')
    return address('0x'+f'{val:040x}', allow_zero=True)

def pool_pair_data(stable, tier=None):
    if tier is None:
        return '0xd9a641e1' + word(int(stable, 16)) + word(int(WBTC, 16))
    return '0x1698ee82' + word(int(stable, 16)) + word(int(WBTC, 16)) + word(tier)

def parse_sqrt(raw, algebra):
    if not isinstance(raw, str) or not raw.startswith('0x') or (len(raw)-2)%64 != 0:
        raise Halt('BAD_PRICE_GETTER')
    words = (len(raw)-2)//64
    if algebra and words not in (6, 7, 8):
        raise Halt('UNKNOWN_ALGEBRA_ABI')
    if not algebra and words != 7:
        raise Halt('BAD_UNISWAP_SLOT0')
    v = int(raw[2:66], 16)
    if not 0 < v < 2**160:
        raise Halt('BAD_SQRT_PRICE')
    return v

def bps(x, y):
    if not (type(x) is int and type(y) is int and 0 < x < 2**160 and 0 < y < 2**160):
        raise Halt('BAD_PAIR_PRICE')
    rel = (Decimal(x)/Decimal(y))**2
    return (max(rel, 1/rel) - 1)*10000

class RPC:
    def __init__(self, url, transport=None):
        if transport is None and (not url.startswith('https://') or any(c.isspace() for c in url)):
            raise Halt('POLYGON_RPC_URL_MUST_BE_HTTPS')
        self.url, self.transport, self.count = url, transport, 0
        self.deadline = time.monotonic() + TIME_BUDGET

    def call(self, method, params):
        if self.count >= MAX_RPC or time.monotonic() >= self.deadline:
            raise Halt('RPC_OR_TIME_LIMIT_EXCEEDED')
        self.count += 1
        if self.transport:
            return self.transport(method, params)
        req = urllib.request.Request(self.url, data=json.dumps({'jsonrpc':'2.0','id':self.count,'method':method,'params':params}).encode(), headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=min(18,max(1,self.deadline-time.monotonic()))) as response:
                raw = response.read(MAX_REPLY+1)
        except Exception as exc:
            raise Halt('RPC_HTTP_OR_NETWORK_FAILURE') from exc
        if len(raw)>MAX_REPLY:
            raise Halt('RPC_RESPONSE_TOO_LARGE')
        try:
            doc = json.loads(raw)
        except ValueError:
            raise Halt('INVALID_RPC_JSON') from None
        if not isinstance(doc,dict) or 'error' in doc or 'result' not in doc:
            code = doc.get('error',{}).get('code') if isinstance(doc,dict) and isinstance(doc.get('error'),dict) else None
            raise Halt('RPC_REJECTED_'+str(code))
        return doc['result']

    def eth_call(self, target, selector, block=BLOCK):
        return self.call('eth_call', [{'to':target,'data':selector},hex(block)])


def get_pool(rpc, stable, tier=None):
    return abi_addr(rpc.eth_call(ALG_FACTORY if tier is None else UNI_FACTORY, pool_pair_data(stable,tier)))

def check_pool(rpc, pool, stable, tier):
    if pool == ZERO:
        return {'exists':False, 'address':pool, 'fee_units':tier, 'active_liquidity_raw':None, 'sqrt_price_x96':None, 'swap_logs':0}
    t0=abi_addr(rpc.eth_call(pool,'0x0dfe1681'))
    t1=abi_addr(rpc.eth_call(pool,'0xd21220a7'))
    if (t0,t1)!=tuple(sorted((stable, WBTC))):
        raise Halt('POOL_TOKEN_IDENTITY_OR_ORDER_MISMATCH')
    liq=abi_uint(rpc.eth_call(pool,'0x1a686502'))
    if liq >= 2**128:
        raise Halt('INVALID_ACTIVE_LIQUIDITY')
    if tier is not None and abi_uint(rpc.eth_call(pool,'0xddca3f43'))!=tier:
        raise Halt('UNISWAP_FEE_MISMATCH')
    raw = rpc.eth_call(pool, '0xe76c01e4' if tier is None else '0x3850c7bd')
    sqrt=parse_sqrt(raw, tier is None)
    return {'exists':True,'address':pool,'token0':t0,'token1':t1, 'fee_units':tier,
            'active_liquidity_raw':liq, 'sqrt_price_x96':str(sqrt), 'swap_logs':0}

def perform(rpc):
    if int(rpc.call('eth_chainId',[]),16)!=137:
        raise Halt('WRONG_CHAIN')
    block=rpc.call('eth_getBlockByNumber',[hex(BLOCK),False])
    if not isinstance(block,dict) or block.get('hash','').lower()!=BLOCK_HASH:
        raise Halt('HISTORICAL_BLOCK_HASH_MISMATCH')
    doc={'schema_version':1,'study':'step11_22_wbtc_pool_triage','block':BLOCK,'block_hash':BLOCK_HASH,
         'pairs':{},'execution_enabled':False,'profit_proven':False,'state_kind':'END_OF_BLOCK_ONLY',
         'full_block_traces':0}
    for name, stable in PAIRS.items():
        addr={'algebra':get_pool(rpc,stable),'uniswap_100':get_pool(rpc,stable,100), 'uniswap_500':get_pool(rpc,stable,500)}
        venues={v:check_pool(rpc,p,stable,None if v=='algebra' else int(v.rsplit('_',1)[1])) for v,p in addr.items()}
        active=[row['address'] for row in venues.values() if row['exists']]
        if active:
            logs=rpc.call('eth_getLogs',[{'fromBlock':hex(BEGIN),'toBlock':hex(BLOCK),
              'address':active,'topics':[SWAP_TOPIC]}])
            if not isinstance(logs,list) or len(logs)>2000:
                raise Halt('BAD_OR_EXCESSIVE_SWAP_LOGS')
            counts=Counter()
            for row in logs:
                addr_log=address(row.get('address'))
                if addr_log not in active or not isinstance(row.get('topics'),list) or not row['topics'] or row['topics'][0].lower()!=SWAP_TOPIC:
                    raise Halt('INVALID_SWAP_LOG')
                counts[addr_log]+=1
            for p in venues.values():
                if p['exists']:
                    p['swap_logs']=counts[p['address']]
        comparisons={}
        alg=venues['algebra']
        for v in ('uniswap_100','uniswap_500'):
            other=venues[v]
            if alg['exists'] and other['exists']:
                spread=bps(int(alg['sqrt_price_x96']),int(other['sqrt_price_x96']))
                fee=other['fee_units']/100
                comparisons[v]={'snapshot_spread_bps':format(spread,'.8f'),
                                'known_uniswap_fee_bps':fee,
                                'survives_one_leg_fee_spot_only':spread>fee,
                                'same_boundary_verified':False, 'tick_quoted':False}
        doc['pairs'][name]={'pools':venues,'comparisons':comparisons}
    doc['rpc_calls']=rpc.count
    return doc

def main():
    rpc=RPC(os.environ.get('POLYGON_RPC_URL',''))
    try:
        report=perform(rpc)
        OUTPUT.parent.mkdir(exist_ok=True,parents=True)
        OUTPUT.write_text(json.dumps(report,indent=2)+'\n')
        print('STEP 11.22 — WBTC CROSS-VENUE POOL TRIAGE (READ ONLY)')
        print('block:', BLOCK, 'hash_verified: True')
        for name, item in report['pairs'].items():
            print('pair:', name)
            for v,p in item['pools'].items():
                print(' venue:', v, 'pool:',p['address'], 'exists:',p['exists'], 'active_liquidity_raw:',p['active_liquidity_raw'], 'swap_logs_200:',p['swap_logs'])
            for v,c in item['comparisons'].items():
                print(' spot_vs_'+v+'_bps:',c['snapshot_spread_bps'], 'known_uniswap_fee_bps:',c['known_uniswap_fee_bps'], 'one_leg_fee_only:',c['survives_one_leg_fee_spot_only'])
        print('rpc_calls:',rpc.count,'/',MAX_RPC,'block_traces:0')
        print('saved:',OUTPUT)
        print('NO INTRABLOCK PROOF / NO TICK QUOTES / NO SIGNING / NO BROADCAST')
    except Halt as e:
        print('STEP 11.22 FAILED CLOSED:',str(e),'rpc_calls:',rpc.count)
        raise SystemExit(1)

if __name__=='__main__':
    main()
