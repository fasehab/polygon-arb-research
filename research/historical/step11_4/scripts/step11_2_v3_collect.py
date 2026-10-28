#!/usr/bin/env python3
"""STEP 11.2 Polygon PoS V3 read-only historical quoter experiment.

QuickSwap Algebra V3 <-> Uniswap V3 (500/3000). NOT a V2 calculator.
Only JSON-RPC reads, no accounts, signatures, overrides, transaction submission.
Quote revert skips a specific route, transport failures restart a whole block on fallback.
Quoter gas is NOT executor gas. Research only; V2 executor cannot trade V3.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from step11_collect import (EvidenceError, ProviderError, Rpc, TOKENS, PAIRS, VAULT,
    COLLECTOR, FLASH_FEE, BALANCE, DECIMALS, TOKEN0, TOKEN1,
    address, address_word, decode_address, decode_words, as_uint, word,
    call_at, uint_at, get_hash, flash_fee, blockhash)

ALG_FACTORY='0x411b0facc3489691f28ad58c47006af5e3ab3a28'
ALG_QUOTER='0xa15f0d7377b2a0c0c10db057f641bed21028fc89'
UNI_FACTORY='0x1f98431c8ad98523631ae4a59f267346ea31f984'
UNI_QUOTER_V2='0x61ffe014ba17989e743c5f6cb21bf9697530b21e'
ALG_POOL_BY_PAIR='d9a641e1'
UNI_GET_POOL='1698ee82'
ALG_QUOTE='2d9ebd1d'
UNI_QUOTE_V2='c6a5026a'
POOL_LIQUIDITY='1a686502'
POOL_FEE='ddca3f43'
MAX_BLOCKS=6
MAX_SIZES=4
TIERS=(500,3000)

class QuoteRevert(Exception):pass

class V3Rpc(Rpc):
    def call(self,method,params):
        if method not in self.ALLOWED:
            raise EvidenceError('unauthorized RPC method')
        self.counter += 1
        if self.transport:
            return self.transport(method,params)
        req=urllib.request.Request(self.url,
            data=json.dumps({'jsonrpc':'2.0','id':self.counter,'method':method,'params':params}).encode(),
            headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=35) as response:
                obj=json.loads(response.read(4_000_000))
        except Exception:
            raise ProviderError('RPC transport failure; credentials hidden') from None
        if not isinstance(obj,dict):
            raise ProviderError('invalid RPC response')
        if 'error' in obj and obj['error'] is not None:
            # Reverts are regular on-chain quote failures. Never assume other RPC
            # errors (rate-limits, missing state, timeouts) are expected reverts.
            e=obj['error']; code=e.get('code') if isinstance(e,dict) else None
            to = params[0].get('to','').lower() if method=='eth_call' and params and isinstance(params[0],dict) else ''
            if method=='eth_call' and to in (ALG_QUOTER, UNI_QUOTER_V2) and code in (3,-32000) and \
               any(key in str(e.get('message','')).lower() for key in ('revert','execution reverted')):
                raise QuoteRevert('quoter reverted')
            raise ProviderError('RPC returned error; credentials hidden')
        if 'result' not in obj or obj['result'] is None:
            raise ProviderError('RPC missing result')
        return obj['result']


def valid_code(rpc,contract,block):
    data=rpc.call('eth_getCode',[contract,hex(block)])
    if not isinstance(data,str) or not data.startswith('0x') or len(data)<=2 or len(data)%2:
        raise EvidenceError('required contract missing code at historical block')
    try:int(data[2:],16)
    except ValueError:raise EvidenceError('invalid bytecode') from None


def read_pool(rpc,factory,token_a,token_b,tier,block,kind):
    if kind=='algebra':
        result=call_at(rpc,factory,'0x'+ALG_POOL_BY_PAIR+address_word(token_a)+address_word(token_b),block)
    else:
        result=call_at(rpc,factory,'0x'+UNI_GET_POOL+address_word(token_a)+address_word(token_b)+word(tier),block)
    pool=decode_address(result)
    if int(pool,16)==0:return None
    valid_code(rpc,pool,block)
    t0=decode_address(call_at(rpc,pool,'0x'+TOKEN0,block))
    t1=decode_address(call_at(rpc,pool,'0x'+TOKEN1,block))
    if {t0,t1}!={token_a,token_b} or t0==t1 or int(t0,16)>int(t1,16):
        raise EvidenceError('V3 pool token identity/order mismatch')
    liq=uint_at(rpc,pool,POOL_LIQUIDITY,block)
    if liq>=2**128: raise EvidenceError('V3 pool liquidity overflow')
    if kind=='uniswap':
        observed_fee=uint_at(rpc,pool,POOL_FEE,block)
        if observed_fee!=tier: raise EvidenceError('Uniswap pool fee-tier mismatch')
    return {'address':pool,'token0':t0,'token1':t1,'liquidity_raw':str(liq),
            'fee_tier':tier if kind=='uniswap' else None}


def quote_alg(rpc,a,b,amount,block):
    output=call_at(rpc,ALG_QUOTER,'0x'+ALG_QUOTE+address_word(a)+address_word(b)+word(amount)+word(0),block)
    data=decode_words(output,2)
    if len(data)!=2 or data[0]==0 or data[1]>=10000:
        raise EvidenceError('Algebra quote malformed')
    return data[0],data[1]


def quote_uni(rpc,a,b,amount,tier,block):
    output=call_at(rpc,UNI_QUOTER_V2,'0x'+UNI_QUOTE_V2+
        address_word(a)+address_word(b)+word(amount)+word(tier)+word(0),block)
    data=decode_words(output,4)
    if len(data)!=4 or data[0]==0 or not 0<data[1]<2**160 or data[2]>=2**32 or data[3]==0:
        raise EvidenceError('Uniswap QuoterV2 output malformed')
    return data[0],data[3]


def collect_block(rpc,number,sizes,tiers):
    if int(rpc.call('eth_chainId',[]),16)!=137:raise EvidenceError('wrong chain')
    if int(rpc.call('eth_blockNumber',[]),16)<number:raise EvidenceError('block in future')
    h=get_hash(rpc,number)
    for c in (ALG_FACTORY,ALG_QUOTER,UNI_FACTORY,UNI_QUOTER_V2,VAULT):
        valid_code(rpc,c,number)
    for symbol,(addr,dec) in TOKENS.items():
        valid_code(rpc,addr,number)
        if uint_at(rpc,addr,DECIMALS,number)!=dec:raise EvidenceError('unexpected token decimals '+symbol)
    collector=decode_address(call_at(rpc,VAULT,'0x'+COLLECTOR,number))
    if int(collector,16)==0:raise EvidenceError('missing flash fee collector')
    fee_wad=uint_at(rpc,collector,FLASH_FEE,number)
    if fee_wad>10**16:raise EvidenceError('flash loan fee outside cap')
    results=[]
    for pair_id,borrowed,mid in PAIRS:
        a,dec=TOKENS[borrowed]; b,_=TOKENS[mid]
        balance=uint_at(rpc,a,BALANCE+address_word(VAULT),number)
        alg=read_pool(rpc,ALG_FACTORY,a,b,0,number,'algebra')
        uni={str(tier):read_pool(rpc,UNI_FACTORY,a,b,tier,number,'uniswap') for tier in tiers}
        quotes=[]
        for tier in tiers:
            partner=uni[str(tier)]
            for size in sizes:
                principal=size*10**dec
                for direction in ('algebra_then_uniswap','uniswap_then_algebra'):
                    status='OK'
                    first_out=None;returned=None;alg_fee=None;uni_gas=None
                    if alg is None or partner is None:
                        status='SKIP_MISSING_POOL'
                    elif int(alg['liquidity_raw'])==0 or int(partner['liquidity_raw'])==0:
                        status='SKIP_ZERO_ACTIVE_LIQUIDITY'
                    else:
                        try:
                            if direction=='algebra_then_uniswap':
                                first_out,alg_fee=quote_alg(rpc,a,b,principal,number)
                                returned,uni_gas=quote_uni(rpc,b,a,first_out,tier,number)
                            else:
                                first_out,uni_gas=quote_uni(rpc,a,b,principal,tier,number)
                                returned,alg_fee=quote_alg(rpc,b,a,first_out,number)
                        except QuoteRevert:
                            status='SKIP_QUOTER_REVERT'
                    quotes.append({'tier':tier,'direction':direction,'size_units':size,
                        'status':status,'principal_raw':str(principal),
                        'flash_fee_raw':str(flash_fee(principal,fee_wad)),
                        'first_out_raw':str(first_out) if first_out is not None and status=='OK' else None,
                        'returned_raw':str(returned) if returned is not None and status=='OK' else None,
                        'algebra_dynamic_fee_raw':str(alg_fee) if status=='OK' else None,
                        'uniswap_quoter_gas_hint':str(uni_gas) if status=='OK' else None})
        results.append({'pair_id':pair_id,'borrow_symbol':borrowed,'mid_symbol':mid,
            'vault_balance_raw':str(balance),'algebra_pool':alg,'uniswap_pools':uni,'quotes':quotes})
    if get_hash(rpc,number)!=h:raise EvidenceError('block hash changed')
    return {'number':number,'hash':h,'fee_rate_wad':str(fee_wad),'pairs':results}


def collect(primary,fallback,blocks,sizes,tiers):
    if not (1<=len(blocks)<=MAX_BLOCKS and len(set(blocks))==len(blocks) and all(b>0 for b in blocks)):
        raise EvidenceError('invalid block list')
    if not (1<=len(sizes)<=MAX_SIZES and len(set(sizes))==len(sizes) and all(0<n<=1000 for n in sizes)):
        raise EvidenceError('invalid size list')
    if not tiers or len(set(tiers))!=len(tiers) or not set(tiers)<=set(TIERS):
        raise EvidenceError('unsupported fee tiers')
    out=[]
    for num in blocks:
        try:
            x=collect_block(primary,num,sizes,tiers);x['provider_role']='primary'
        except (ProviderError,EvidenceError):
            if fallback is None:raise
            x=collect_block(fallback,num,sizes,tiers);x['provider_role']='fallback'
        out.append(x)
    return {'schema_version':1,'evidence_kind':'historical_polygon_v3_algebra_uniswap_readonly',
        'chain_id':137,'algebra_factory':ALG_FACTORY,'algebra_quoter':ALG_QUOTER,
        'uniswap_factory':UNI_FACTORY,'uniswap_quoter':UNI_QUOTER_V2,
        'vault':VAULT,'tokens':[{'symbol':s,'address':addr,'decimals':dec} for s,(addr,dec) in TOKENS.items()],
        'sizes_whole_units':sizes,'tiers':tiers,'blocks':out,
        'safety':{'uses_state_overrides':False,'writes_or_broadcasts_transactions':False,
            'proves_executor_eth_call':False,'proves_gas_or_net_profit':False,
            'proves_mempool_or_inclusion':False,'executor_supports_v3':False},
        'limitations':['QuickSwap Algebra V3 and Uniswap V3 use distinct interfaces',
            'quoter output and quoter gas hint are not actual executor simulation',
            'borrowed-token and pool selection are research-only',
            'V2-only Solidity executor cannot trade V3; no on-chain execution permitted',
            'RPC JSON is not cryptographic proof; independent fork replay required']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--blocks',type=int,nargs='+',default=[96179857,96174857])
    p.add_argument('--sizes',type=int,nargs='+',default=[1,5])
    p.add_argument('--tiers',type=int,nargs='+',default=[500,3000])
    p.add_argument('--output',default='outputs/step11_2_v3_quotes.json')
    a=p.parse_args()
    url=os.getenv('POLYGON_RPC_URL','')
    if not url:p.error('Set POLYGON_RPC_URL to HTTPS Polygon read RPC; credentials must not be pasted into chat')
    try:
        primary=V3Rpc(url)
        fallback=V3Rpc(os.environ['POLYGON_RPC_FALLBACK_URL']) if os.getenv('POLYGON_RPC_FALLBACK_URL') else None
        report=collect(primary,fallback,a.blocks,a.sizes,a.tiers)
        out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
        tmp=out.with_suffix(out.suffix+'.tmp')
        tmp.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n');tmp.replace(out)
        quotes=[q for b in report['blocks'] for pp in b['pairs'] for q in pp['quotes']]
        positive=sum(q['status']=='OK' and int(q['returned_raw'])>int(q['principal_raw'])+int(q['flash_fee_raw']) for q in quotes)
        print(f'STEP 11.2 V3 READ-ONLY: blocks={len(report["blocks"])} evaluated={len(quotes)} complete={sum(q["status"]=="OK" for q in quotes)} skipped={sum(q["status"]!="OK" for q in quotes)} gross_positive_unassessed={positive}')
        for b in report['blocks']:
            print(f'block={b["number"]} hash={b["hash"]} provider={b["provider_role"]}')
            for pp in b['pairs']:
                rs=[q for q in pp['quotes'] if q['status']=='OK']
                print(f'  pair={pp["pair_id"]} completed={len(rs)} missing_or_reverted={len(pp["quotes"])-len(rs)}')
        print('EVIDENCE SAVED:',out)
        print('ALWAYS BLOCK: V3 not supported by existing V2 Solidity executor; zero signing or broadcasting.')
    except (EvidenceError,ProviderError,ValueError) as err:
        print('STEP 11.2 FAIL-CLOSED: invalid/incomplete RPC evidence ('+type(err).__name__+')',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':main()
