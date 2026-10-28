#!/usr/bin/env python3
"""Step 11.19: one historical end-of-block QuickSwap Algebra vs Uniswap V3 quote screen.

READ ONLY. This is not historical intrablock state replay, atomic execution, or net profit proof.
Uniswap QuoterV2: quoteExactInputSingle((address,address,uint256,uint24,uint160))
Algebra Polygon Quoter: quoteExactInputSingle(address,address,uint256,uint160)
"""
import argparse
from decimal import Decimal, getcontext
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

getcontext().prec = 70
BLOCK = 96179799
BLOCK_HASH = '0xfccfb30b9570d2765591c262ec13e89550ba0a2f5904e6e8874423040491e3f4'
USDT = '0xc2132d05d31c914a87c6611c10748aeb04b58e8f'
WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
UNI_POOL = '0xbb98b3d2b18aef63a3178023a920971cf5f29be4'
UNI_QUOTER = '0x61ffe014ba17989e743c5f6cb21bf9697530b21e'
ALG_QUOTER = '0xa15f0d7377b2a0c0c10db057f641bed21028fc89'
UNI_FEE = 500
SWAP_TOPIC = '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67'
QUOTER_V2_SELECTOR = '0xc6a5026a'
ALG_QUOTER_SELECTOR = '0x2d9ebd1d'
MAX_REQUESTS = 30
MAX_BYTES = 200000
WALL_SECONDS = 180
SIZES = (10, 100, 1000)
OUTPUT = Path('outputs/step11_19_cross_venue_quotes.json')

class Halt(Exception):
    pass


def abi_word(value):
    if not isinstance(value, int) or not 0 <= value < 2**256:
        raise Halt('BAD_ABI_WORD')
    return f'{value:064x}'


def check_address(address):
    if (not isinstance(address, str) or len(address) != 42 or
            not address.startswith('0x') or any(c not in '0123456789abcdefABCDEF' for c in address[2:])):
        raise Halt('BAD_ADDRESS')
    return address.lower()


def decode_words(blob, n):
    if not isinstance(blob, str) or not blob.startswith('0x') or len(blob) != 2 + n*64:
        raise Halt('BAD_ABI_RESPONSE_LENGTH')
    try:
        return [int(blob[2+i*64:2+(i+1)*64], 16) for i in range(n)]
    except ValueError:
        raise Halt('BAD_ABI_RESPONSE_HEX') from None


def decode_address(blob):
    value = decode_words(blob, 1)[0]
    if value == 0 or value >= 2**160:
        raise Halt('BAD_GETTER_ADDRESS')
    return '0x' + f'{value:040x}'


class RPC:
    def __init__(self, url, transport=None):
        if not isinstance(url, str) or not url.startswith('https://') or any(x.isspace() for x in url):
            raise Halt('HTTPS_RPC_REQUIRED')
        self.url = url
        self.transport = transport
        self.count = 0
        self.end = time.monotonic() + WALL_SECONDS

    def call(self, method, params):
        if self.count >= MAX_REQUESTS:
            raise Halt('RPC_REQUEST_CAP')
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise Halt('WALL_CLOCK_BUDGET')
        self.count += 1
        if self.transport is not None:
            return self.transport(method, params)
        body = json.dumps({'jsonrpc':'2.0','id':self.count,
                           'method':method,'params':params}).encode()
        req = urllib.request.Request(self.url, body, headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=max(1, min(20,remaining))) as r:
                raw = r.read(MAX_BYTES+1)
        except urllib.error.HTTPError as ex:
            # Fail safely: never include RPC URL or credentials in logs.
            raise Halt('RPC_HTTP_ERROR_' + str(ex.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise Halt('RPC_NETWORK_OR_TIMEOUT') from None
        if len(raw) > MAX_BYTES:
            raise Halt('RPC_RESPONSE_TOO_LARGE')
        try:
            response = json.loads(raw)
        except (ValueError,UnicodeError):
            raise Halt('RPC_INVALID_JSON') from None
        if not isinstance(response,dict):
            raise Halt('RPC_INVALID_BODY')
        if 'error' in response:
            raise Halt('RPC_REJECTED_' + str(response['error'].get('code')))
        if 'result' not in response:
            raise Halt('RPC_MISSING_RESULT')
        return response['result']

    def eth_call(self, address, data):
        return self.call('eth_call',[{'to':check_address(address), 'data':data,
                                      'gas':hex(6_000_000)},hex(BLOCK)])


def calldata_uni(token_in, token_out, amount):
    return QUOTER_V2_SELECTOR + ''.join(abi_word(v) for v in (
        int(check_address(token_in),16),int(check_address(token_out),16),amount,UNI_FEE,0))


def calldata_algebra(token_in, token_out, amount):
    return ALG_QUOTER_SELECTOR + ''.join(abi_word(v) for v in (
        int(check_address(token_in),16),int(check_address(token_out),16),amount,0))


def quote_uni(rpc, token_in, token_out, amount):
    words = decode_words(rpc.eth_call(UNI_QUOTER,calldata_uni(token_in,token_out,amount)),4)
    amt,sqrt_after,ticks,gas = words
    if amt<=0 or not 0<sqrt_after<2**160 or ticks>=2**32 or gas<=0:
        raise Halt('UNI_QUOTE_INVALID')
    return {'amount_out_raw':amt,'sqrt_after':str(sqrt_after),
            'initialized_ticks_crossed':ticks,'quoter_gas_not_execution_gas':gas}


def quote_algebra(rpc, token_in, token_out, amount):
    amount_out,fee_units = decode_words(
        rpc.eth_call(ALG_QUOTER,calldata_algebra(token_in,token_out,amount)),2)
    if amount_out<=0 or fee_units>=2**16:
        raise Halt('ALGEBRA_QUOTE_INVALID')
    return {'amount_out_raw':amount_out,'reported_fee_units':fee_units}


def check_pool_identities(rpc, algebra_address):
    for pool in (UNI_POOL, algebra_address):
        tokens = [decode_address(rpc.eth_call(pool, selector)) for selector in
                  ('0x0dfe1681','0xd21220a7')]
        if len(set(tokens))!=2 or set(tokens)!={USDT,WETH}:
            raise Halt('POOL_TOKEN_MISMATCH')
    if decode_words(rpc.eth_call(UNI_POOL,'0xddca3f43'),1)[0] != UNI_FEE:
        raise Halt('UNI_POOL_FEE_MISMATCH')


def score_route(rpc, size, first):
    amount_in = size * 1_000_000
    if first=='algebra':
        leg1 = quote_algebra(rpc, USDT,WETH,amount_in)
        leg2 = quote_uni(rpc, WETH,USDT,leg1['amount_out_raw'])
    elif first=='uniswap_500':
        leg1 = quote_uni(rpc, USDT,WETH,amount_in)
        leg2 = quote_algebra(rpc, WETH,USDT,leg1['amount_out_raw'])
    else:
        raise Halt('BAD_ROUTE')
    final_raw = leg2['amount_out_raw']
    return {'input_usdt_units':size,'route':first+'->'+('uniswap_500' if first=='algebra' else 'algebra'),
            'leg1':{k:str(v) for k,v in leg1.items()},
            'leg2':{k:str(v) for k,v in leg2.items()},
            'final_usdt':str(Decimal(final_raw)/1_000_000),
            'delta_usdt_before_gas':str(Decimal(final_raw-amount_in)/1_000_000),
            'delta_raw':str(final_raw-amount_in),
            'status':'END_OF_BLOCK_SEQUENTIAL_QUOTE_ONLY'}


def run(rpc, algebra_address):
    check_address(algebra_address)
    if int(rpc.call('eth_chainId',[]),16)!=137:
        raise Halt('WRONG_CHAIN')
    block=rpc.call('eth_getBlockByNumber',[hex(BLOCK),False])
    if not isinstance(block,dict) or str(block.get('hash','')).lower()!=BLOCK_HASH:
        raise Halt('HISTORICAL_BLOCK_HASH_MISMATCH')
    for address in (UNI_QUOTER,ALG_QUOTER,UNI_POOL,algebra_address):
        code=rpc.call('eth_getCode',[address,hex(BLOCK)])
        if not isinstance(code,str) or code.lower() in ('0x','0x0','0x00'):
            raise Halt('CONTRACT_MISSING_AT_BLOCK')
    check_pool_identities(rpc,algebra_address)
    # Correct, independently verified V3 Swap topic; Step 11.18 used a bad topic.
    activity = {}
    for name, address in (('algebra',algebra_address),('uniswap_500',UNI_POOL)):
        try:
            logs=rpc.call('eth_getLogs',[{'address':address,'topics':[SWAP_TOPIC],
                                          'fromBlock':hex(BLOCK-19),'toBlock':hex(BLOCK)}])
            if not isinstance(logs,list) or len(logs)>2000:
                raise Halt('BAD_LOG_RESPONSE')
            activity[name]={'last_20_blocks_swap_logs':len(logs),'status':'OBSERVED'}
        except Halt as exc:
            if str(exc) in ('RPC_REQUEST_CAP','WALL_CLOCK_BUDGET'):
                raise
            activity[name]={'last_20_blocks_swap_logs':None,'status':'UNVERIFIED',
                            'reason':str(exc)}
    rows=[]
    for size in SIZES:
        for venue in ('algebra','uniswap_500'):
            try:
                record=score_route(rpc,size,venue)
            except Halt as err:
                # Every rejected quote remains visible, never reported as a zero return.
                record={'input_usdt_units':size,'route':venue+'->'+(
                    'uniswap_500' if venue=='algebra' else 'algebra'),
                    'status':'QUOTE_UNAVAILABLE','reason':str(err)}
                if str(err) in ('RPC_REQUEST_CAP','WALL_CLOCK_BUDGET'):
                    raise
            rows.append(record)
    positive=[x for x in rows if x.get('status')=='END_OF_BLOCK_SEQUENTIAL_QUOTE_ONLY'
              and int(x['delta_raw'])>0]
    return {'step':'11.19','chain_id':137,'block':BLOCK,'block_hash':BLOCK_HASH,
            'algebra_pool':algebra_address,'uniswap_pool':UNI_POOL,
            'algebra_quoter':ALG_QUOTER,'uniswap_quoter':UNI_QUOTER,
            'scope':'END_OF_BLOCK_ONLY_NOT_INTRABLOCK',
            'method':'TWO_SEPARATE_POOL_ETH_CALL_QUOTES_WITH_NO_LIVE_EXECUTION',
            'quotes':rows,'swap_activity_last_20_blocks':activity,
            'positive_quote_count':len(positive),
            'verdict':'SOME_POSITIVE_QUOTES_NEED_INTRABLOCK_AND_FORK_REPLAY' if positive
                      else 'NO_POSITIVE_CROSS_VENUE_QUOTES_AT_TESTED_SIZES',
            'profit_proven':False,'gas_cost_included':False,'atomic_execution_tested':False,
            'signing':False,'broadcast':False,'rpc_calls':rpc.count,'max_rpc':MAX_REQUESTS}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--anchor',default='outputs/step11_8_boundary.json')
    ap.add_argument('--output',default=str(OUTPUT))
    args=ap.parse_args()
    # Imported from the existing Step 11.9 install for schema and historical pool identity validation.
    from step11_9_block_diff_probe import load_anchor
    anchor=load_anchor(args.anchor)
    rpc=RPC(os.environ.get('POLYGON_RPC_URL',''))
    doc=run(rpc,anchor['algebra'])
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(doc,indent=2)+'\n')
    print('STEP 11.19 — HISTORICAL ALGEBRA / UNISWAP V3 500 TICK-AWARE ROUNDTRIP — READ ONLY')
    print('block:',BLOCK,'hash_verified: True')
    print('swap_activity_last_20_blocks:',doc['swap_activity_last_20_blocks'])
    for q in doc['quotes']:
        if q['status']=='END_OF_BLOCK_SEQUENTIAL_QUOTE_ONLY':
            print(f"size={q['input_usdt_units']} route={q['route']} delta_usdt_before_gas={q['delta_usdt_before_gas']} "
                  f"algebra_fee_units={q['leg1'].get('reported_fee_units',q['leg2'].get('reported_fee_units','N/A'))}")
        else:
            print(f"size={q['input_usdt_units']} route={q['route']} UNAVAILABLE={q['reason']}")
    print('verdict:',doc['verdict'],'rpc_calls:',doc['rpc_calls'],'/',MAX_REQUESTS)
    print('saved:',target)
    print('NO INTRABLOCK PROOF / NO GAS OR PROFIT PROOF / NO SIGNING / NO BROADCAST')

if __name__=='__main__':
    try:
        main()
    except Halt as e:
        raise SystemExit('FAIL_CLOSED: '+str(e)) from None
