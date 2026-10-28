#!/usr/bin/env python3
"""STEP 11: multi-pair/multi-block unmodified Polygon V2 historical collector.

Read-only RPC methods: eth_chainId, eth_blockNumber, eth_getBlockByNumber,
eth_getCode, eth_call. Does NOT sign, send, impersonate or override state.
Run against an HTTPS archive-capable Polygon RPC. Read-only fallback restarts
an entire block from scratch; never mixes two providers within the block.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path

# Previously shortlisted research candidates, NOT execution-authorized tokens.
DAI = '0x8f3cf7ad23cd3cadbd9735aff958023239c6a063'
WETH = '0x7ceb23fd6bc0add59e62ac25578270cff1b9f619'
USDCe = '0x2791bca1f2de4661ed88a30c99a7a9449aa84174'
TOKENS = {'DAI': (DAI, 18), 'WETH': (WETH, 18), 'USDCe': (USDCe, 6)}
QUICK = '0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff'
SUSHI = '0x1b02da8cb0d097eb8d57a175b88c7d8b47997506'
QUICK_FACTORY = '0x5757371414417b8c6caad45baef941abc7d3ab32'
SUSHI_FACTORY = '0xc35dadb65012ec5796536bd9864ed8773abc74c4'
VAULT = '0xba12222222228d8ba445958a75a0704d566bf2c8'
# Stablecoin-denominated sizes avoid assuming 1 WETH is equal to 1 DAI.
PAIRS = [('DAI_WETH', 'DAI', 'WETH'), ('USDCe_WETH', 'USDCe', 'WETH'),
         ('DAI_USDCe', 'DAI', 'USDCe')]
BLOCKS = [96179857, 96179797, 96179737]
SIZES = [1, 5, 25]
MAX_BLOCKS, MAX_SIZES = 8, 5
# Required base ABI selectors
GETPAIR='e6a43905'; QUOTE='d06ca61f'; RESERVES='0902f1ac'
TOKEN0='0dfe1681'; TOKEN1='d21220a7'; BALANCE='70a08231'
COLLECTOR='d2946c2b'; FLASH_FEE='d877845c'; DECIMALS='313ce567'; FACTORY='c45a0155'

class EvidenceError(Exception):
    pass
class ProviderError(EvidenceError):
    pass


def word(value):
    if type(value) is not int or value < 0 or value >= 2**256:
        raise EvidenceError('invalid ABI integer')
    return f'{value:064x}'


def address(value):
    if not isinstance(value, str) or len(value) != 42 or value[:2] != '0x':
        raise EvidenceError('invalid EVM address')
    try: int(value[2:], 16)
    except ValueError: raise EvidenceError('invalid EVM address') from None
    return value.lower()


def address_word(value):
    return word(int(address(value), 16))


def decode_words(value, minimum=1):
    if not isinstance(value,str) or not value.startswith('0x'):
        raise EvidenceError('invalid ABI response')
    data=value[2:]
    if len(data)<64*minimum or len(data)%64:
        raise EvidenceError('short or malformed ABI response')
    try: return [int(data[i:i+64],16) for i in range(0,len(data),64)]
    except ValueError: raise EvidenceError('nonhex ABI response') from None


def decode_address(value):
    w=decode_words(value)
    if len(w)!=1 or w[0]>=2**160:
        raise EvidenceError('invalid ABI address response')
    return '0x'+f'{w[0]:040x}'


def as_uint(value):
    w=decode_words(value)
    if len(w)!=1: raise EvidenceError('malformed uint256 response')
    return w[0]


def encode_quote(amount, token_in, token_out):
    return '0x'+QUOTE+word(amount)+word(64)+word(2)+address_word(token_in)+address_word(token_out)


def decode_quote(value, amount):
    w=decode_words(value,4)
    if len(w)!=4 or w[0]!=32 or w[1]!=2 or w[2]!=amount or w[3]==0:
        raise EvidenceError('router quote malformed, wrong input or zero output')
    return w[3]


def blockhash(value):
    if not isinstance(value,str) or len(value)!=66 or not value.startswith('0x'):
        raise EvidenceError('invalid historical block hash')
    try: int(value[2:],16)
    except ValueError: raise EvidenceError('invalid historical block hash') from None
    if int(value[2:],16)==0: raise EvidenceError('zero historical block hash')
    return value.lower()


class Rpc:
    """Simple strictly read-only JSON-RPC transport; never logs provider credentials."""
    ALLOWED = {'eth_chainId', 'eth_blockNumber', 'eth_getBlockByNumber','eth_getCode','eth_call'}
    def __init__(self, url, transport=None):
        if not isinstance(url,str) or not url.startswith('https://') or any(c.isspace() or c in "'\"" for c in url):
            raise EvidenceError('HTTPS RPC URL missing or malformed')
        url_parts=urlsplit(url)
        if not url_parts.netloc or url_parts.username is not None or url_parts.password is not None:
            raise EvidenceError('invalid HTTPS RPC host')
        self.url=url
        self.counter=0
        self.transport=transport
    def call(self,method,params):
        if method not in self.ALLOWED: raise EvidenceError('RPC method not allowed')
        self.counter+=1
        if self.transport:
            return self.transport(method,params)
        body=json.dumps({'jsonrpc':'2.0','id':self.counter,'method':method,'params':params}).encode()
        try:
            req=urllib.request.Request(self.url,data=body,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=25) as response:
                payload=json.loads(response.read(4_000_000))
        except Exception:
            raise ProviderError('RPC transport/request failed; endpoint and credentials suppressed') from None
        if not isinstance(payload,dict) or payload.get('error') is not None or 'result' not in payload:
            raise ProviderError('RPC error, missing result or provider rate limit')
        return payload['result']


def call_at(rpc,to,data,number):
    result=rpc.call('eth_call',[{'to':address(to),'data':data},hex(number)])
    if not isinstance(result,str): raise EvidenceError('eth_call result not hex string')
    return result


def uint_at(rpc,to,calldata,number):
    return as_uint(call_at(rpc,to,'0x'+calldata,number))


def get_hash(rpc,number):
    result=rpc.call('eth_getBlockByNumber',[hex(number),False])
    if not isinstance(result,dict) or result.get('number')!=hex(number):
        raise EvidenceError('block number missing/mismatched')
    return blockhash(result.get('hash'))


def read_pair(rpc,factory,token_a,token_b,block):
    pair=decode_address(call_at(rpc,factory,'0x'+GETPAIR+address_word(token_a)+address_word(token_b),block))
    if int(pair,16)==0: return None
    code=rpc.call('eth_getCode',[pair,hex(block)])
    if not isinstance(code,str) or not code.startswith('0x') or len(code)<=2:
        raise EvidenceError('nonzero pool returned without code')
    try: int(code[2:],16)
    except ValueError: raise EvidenceError('malformed pair bytecode') from None
    a=decode_address(call_at(rpc,pair,'0x'+TOKEN0,block))
    b=decode_address(call_at(rpc,pair,'0x'+TOKEN1,block))
    if {a,b}!={token_a,token_b} or a==b:
        raise EvidenceError('pool contract token order mismatch')
    w=decode_words(call_at(rpc,pair,'0x'+RESERVES,block),3)
    if len(w)!=3 or not all(0<x<2**112 for x in w[:2]):
        raise EvidenceError('invalid pool reserve data')
    return {'address':pair,'token0':a,'token1':b,
            'reserve0_raw':str(w[0]),'reserve1_raw':str(w[1])}


def flash_fee(amount,wad):
    if wad<0 or wad>10**16: raise EvidenceError('unusual Vault flash fee')
    return (amount*wad+10**18-1)//10**18


def collect_block(rpc,number,sizes):
    """One provider owns the ENTIRE block. Raises rather than producing partial evidence."""
    if int(rpc.call('eth_chainId',[]),16)!=137:
        raise EvidenceError('wrong chain ID')
    tip=int(rpc.call('eth_blockNumber',[]),16)
    if number<1 or number>tip-128:
        raise EvidenceError('block not sufficiently historical/finalized')
    h_before=get_hash(rpc,number)
    qfac=decode_address(call_at(rpc,QUICK,'0x'+FACTORY,number))
    sfac=decode_address(call_at(rpc,SUSHI,'0x'+FACTORY,number))
    if (qfac,sfac)!=(QUICK_FACTORY,SUSHI_FACTORY):
        raise EvidenceError('router factory mismatches historical constants')
    for label,(addr,dec) in TOKENS.items():
        if uint_at(rpc,addr,DECIMALS,number)!=dec:
            raise EvidenceError('token decimals mismatch: '+label)
    collector=decode_address(call_at(rpc,VAULT,'0x'+COLLECTOR,number))
    if int(collector,16)==0: raise EvidenceError('Vault collector is zero address')
    fee_wad=uint_at(rpc,collector,FLASH_FEE,number)
    if fee_wad>10**16: raise EvidenceError('Vault fee out of safety cap')
    entries=[]
    for pair_id, borrowed, mid in PAIRS:
        a,dec=TOKENS[borrowed]
        b,_=TOKENS[mid]
        vault_balance=uint_at(rpc,a,BALANCE+address_word(VAULT),number)
        qp=read_pair(rpc,QUICK_FACTORY,a,b,number)
        sp=read_pair(rpc,SUSHI_FACTORY,a,b,number)
        status='OK'
        if qp is None: status='SKIP_MISSING_QUICK_POOL'
        elif sp is None: status='SKIP_MISSING_SUSHI_POOL'
        elif qp['address']==sp['address']: raise EvidenceError('both DEXes report same pair contract')
        records=[]
        if status=='OK':
            try:
                for qty in sizes:
                    principal=qty*10**dec
                    for route,r1,r2 in [('quick_then_sushi',QUICK,SUSHI),('sushi_then_quick',SUSHI,QUICK)]:
                        first=decode_quote(call_at(rpc,r1,encode_quote(principal,a,b),number),principal)
                        returned=decode_quote(call_at(rpc,r2,encode_quote(first,b,a),number),first)
                        records.append({'route':route,'principal_raw':str(principal),
                          'first_out_raw':str(first),'returned_raw':str(returned),
                          'flash_fee_raw':str(flash_fee(principal,fee_wad))})
            except ProviderError:
                # Provider errors invalidate all of this block; restart from fallback, not partial data.
                raise
            except EvidenceError:
                # Quoting errors are not profitable candidates; retain a visible rejected pair.
                status='SKIP_QUOTE_INCOMPLETE'
                records=[]
        entries.append({'pair_id':pair_id,'borrow_symbol':borrowed,'mid_symbol':mid,
                        'vault_balance_raw':str(vault_balance),'status':status,
                        'quick_pool':qp,'sushi_pool':sp,'quotes':records})
    h_after=get_hash(rpc,number)
    if h_before!=h_after: raise EvidenceError('block hash changed during scanning')
    return {'number':number,'hash':h_before,'fee_rate_wad':str(fee_wad),'pairs':entries}


def collect(primary, fallback, blocks, sizes):
    if not (1<=len(blocks)<=MAX_BLOCKS and len(set(blocks))==len(blocks)):
        raise EvidenceError('invalid/duplicate block list')
    if not (1<=len(sizes)<=MAX_SIZES and len(set(sizes))==len(sizes) and all(0<x<=1000 for x in sizes)):
        raise EvidenceError('invalid/duplicate trade-size list')
    output=[]
    for number in blocks:
        try:
            block=collect_block(primary,number,sizes)
            block['provider_role']='primary'
        except (EvidenceError,ProviderError):
            if fallback is None: raise
            block=collect_block(fallback,number,sizes)
            block['provider_role']='fallback'
        output.append(block)
    return {
        'schema_version':1, 'evidence_kind':'historical_v2_multi_pair_quotes_no_execution',
        'chain_id':137, 'vault':VAULT,'quick_router':QUICK,'sushi_router':SUSHI,
        'quick_factory':QUICK_FACTORY,'sushi_factory':SUSHI_FACTORY,
        'tokens':[{'symbol':k,'address':v[0],'decimals':v[1]} for k,v in TOKENS.items()],
        'sizes_whole_units':sizes,'blocks':output,
        'safety':{'uses_state_overrides':False,'writes_or_broadcasts_transactions':False,
           'proves_executor_eth_call':False,'proves_gas_or_net_profit':False,
           'proves_mempool_or_inclusion':False},
        'limitations':['historical read-only router quotes only',
          'not same-state executor eth_call, no measured gas, no inclusion guarantee',
          'token shortlist is research-only, fee-on-transfer unknowns remain possible',
          'RPC evidence is unauthenticated JSON; verify independently before fork replay',
          'NO SIGNER, NO BROADCAST, NO EXECUTION AUTHORIZATION']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--blocks',nargs='+',type=int,default=BLOCKS)
    p.add_argument('--sizes',nargs='+',type=int,default=SIZES,help='whole borrowed DAI or USDC.e units')
    p.add_argument('--output',default='outputs/step11_historical_scan.json')
    args=p.parse_args()
    if not (1<=len(args.blocks)<=MAX_BLOCKS and len(set(args.blocks))==len(args.blocks)):
        p.error('1..8 distinct historical block numbers required')
    if not (1<=len(args.sizes)<=MAX_SIZES and len(set(args.sizes))==len(args.sizes) and all(0<x<=1000 for x in args.sizes)):
        p.error('1..5 distinct positive sizes (whole units, at most 1000) required')
    primary_url=os.environ.get('POLYGON_RPC_URL','')
    fallback_url=os.environ.get('POLYGON_RPC_FALLBACK_URL','')
    if not primary_url: p.error('Set POLYGON_RPC_URL to a Polygon HTTPS read-RPC; no key displayed')
    try:
        primary=Rpc(primary_url)
        fallback=Rpc(fallback_url) if fallback_url else None
        evidence=collect(primary,fallback,args.blocks,args.sizes)
        out=Path(args.output)
        out.parent.mkdir(parents=True,exist_ok=True)
        # atomic write: only completed, valid evidence is persisted
        tmp=out.with_suffix(out.suffix+'.tmp')
        tmp.write_text(json.dumps(evidence,indent=2,sort_keys=True)+'\n')
        tmp.replace(out)
        ok=sum(p['status']=='OK' for b in evidence['blocks'] for p in b['pairs'])
        skipped=sum(p['status']!='OK' for b in evidence['blocks'] for p in b['pairs'])
        records=sum(len(p['quotes']) for b in evidence['blocks'] for p in b['pairs'])
        print(f'STEP 11 READ-ONLY: blocks={len(args.blocks)} ready_pairs={ok} skipped_pairs={skipped} quotes={records}')
        for b in evidence['blocks']:
            print(f'block={b["number"]} hash={b["hash"]} provider_role={b["provider_role"]}')
            for entry in b['pairs']:
                print(f'  pair={entry["pair_id"]} status={entry["status"]} quotes={len(entry["quotes"])}')
        print(f'EVIDENCE SAVED: {out}')
        print('NO SIGNING, NO BROADCAST. Positive quotes remain UNASSESSED.')
    except Exception as exc:
        # Do not expose provider credentials, response bodies or untrusted exception strings.
        print(f'STEP 11 FAIL-CLOSED: evidence unavailable/invalid ({type(exc).__name__}). No new report produced.',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':main()
