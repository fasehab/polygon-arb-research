#!/usr/bin/env python3
"""Step 9: Collect RPC-backed historical fee + two WPOL/DAI reference pools
and current Polygon gas price, then write a deliberately NON-AUTHORIZING
analysis input for the Rust economics reporter.

No transaction signing, no wallet/private-key inputs, no broadcast RPC calls.
Uses only Python stdlib. Never prints your Alchemy HTTPS endpoint.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

VAULT = "0xba12222222228d8ba445958a75a0704d566bf2c8"
DAI = "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063"
WPOL = "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270"
QUICK_FACTORY = "0x5757371414417b8c6caad45baef941abc7d3ab32"
SUSHI_FACTORY = "0xc35dadb65012ec5796536bd9864ed8773abc74c4"
# Verified Keccak-256 Ethereum selectors (not NIST SHA3-256).
SEL_COLLECTOR = "d2946c2b"
SEL_FLASH_FEE = "d877845c"
SEL_GET_PAIR = "e6a43905"
SEL_TOKEN0 = "0dfe1681"
SEL_TOKEN1 = "d21220a7"
SEL_RESERVES = "0902f1ac"


def word_address(address):
    if len(address) != 42 or not address.startswith("0x"):
        raise ValueError("invalid EVM address")
    int(address[2:], 16)
    return address[2:].lower().zfill(64)


def decode_address(hextype):
    body = hextype.removeprefix("0x")
    if len(body) != 64:
        raise ValueError("bad ABI address return")
    return "0x" + body[-40:].lower()


def decode_uint(hextype):
    body = hextype.removeprefix("0x")
    if not body or len(body) % 64:
        raise ValueError("unexpected ABI uint length")
    return int(body[:64], 16)


class Rpc:
    def __init__(self, url):
        if not url.startswith("https://"):
            raise ValueError("POLYGON_RPC_URL must use https://")
        self.url = url
        self.counter = 0

    def call(self, method, params):
        self.counter += 1
        payload = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
        data = json.dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.loads(response.read().decode())
        if result.get("error"):
            # Do not echo user endpoint in errors. RPC error fields may include
            # provider hints, which is acceptable, but avoid query parameters.
            raise RuntimeError(str(result["error"].get("message", "RPC error"))[:250])
        if not isinstance(result.get("result"), str):
            raise ValueError(f"{method}: expected result string")
        return result["result"]

    def eth_call(self, to, calldata, block):
        return self.call("eth_call", [{"to": to, "data": calldata}, hex(block)])


def historical_fee(rpc, block):
    collector = decode_address(rpc.eth_call(VAULT, "0x" + SEL_COLLECTOR, block))
    if int(collector[2:], 16) == 0:
        raise ValueError("Balancer protocol fees collector address is zero")
    rate = decode_uint(rpc.eth_call(collector, "0x" + SEL_FLASH_FEE, block))
    if rate > 10**18:
        raise ValueError("Balancer flash fee out of 0..1e18 range")
    return rate, collector


def reserve_reference(rpc, factory, block):
    get_pair = "0x" + SEL_GET_PAIR + word_address(WPOL) + word_address(DAI)
    pair = decode_address(rpc.eth_call(factory, get_pair, block))
    if int(pair[2:], 16) == 0:
        raise ValueError("no direct WPOL/DAI pair")
    t0 = decode_address(rpc.eth_call(pair, "0x" + SEL_TOKEN0, block))
    t1 = decode_address(rpc.eth_call(pair, "0x" + SEL_TOKEN1, block))
    if set([t0, t1]) != set([WPOL, DAI]) or t0 == t1:
        raise ValueError("incorrect pair token orientation")
    resdata = rpc.eth_call(pair, "0x" + SEL_RESERVES, block).removeprefix("0x")
    if len(resdata) < 192:
        raise ValueError("getReserves ABI is too short")
    r0 = int(resdata[:64], 16)
    r1 = int(resdata[64:128], 16)
    native, dai = (r0, r1) if t0 == WPOL else (r1, r0)
    if native == 0 or dai == 0:
        raise ValueError("zero-liquidity conversion pair")
    return {"native_reserve": str(native), "borrowed_reserve": str(dai)}, pair


def assert_raw_uint(v):
    if v < 0 or v >= 2**256:
        raise ValueError("amount outside uint256")
    return str(v)


def collect(rpc, args):
    if int(rpc.call("eth_chainId", []), 16) != 137:
        raise ValueError("endpoint is not Polygon chain 137")
    latest_block = int(rpc.call("eth_blockNumber", []), 16)
    if args.block > latest_block:
        raise ValueError("historical fork block is in the future")
    # A dry archival probe also ensures the provider can serve old eth_calls.
    fee_rate = None
    pair_q = None
    pair_s = None
    messages = []
    try:
        fee_rate, _ = historical_fee(rpc, args.block)
    except Exception as exc:
        messages.append(f"historical Balancer fee read unavailable ({type(exc).__name__}); inspect archive RPC support")
    try:
        pair_q, _ = reserve_reference(rpc, QUICK_FACTORY, args.block)
    except Exception as exc:
        messages.append(f"QuickSwap gas conversion unavailable ({type(exc).__name__}); pool missing or RPC unavailable")
    try:
        pair_s, _ = reserve_reference(rpc, SUSHI_FACTORY, args.block)
    except Exception as exc:
        messages.append(f"SushiSwap gas conversion unavailable ({type(exc).__name__}); pool missing or RPC unavailable")
    gas_price = None
    try:
        gas_price = int(rpc.call("eth_gasPrice", []), 16)
        if gas_price <= 0:
            raise ValueError("zero eth_gasPrice")
    except Exception as exc:
        messages.append(f"live gas price unavailable ({type(exc).__name__})")
    gas_used = args.gas_used
    principal = args.principal_raw
    received = args.received_raw
    if gas_used <= 0 or not (0 < principal < 2**256) or not (0 < received < 2**256):
        raise ValueError("positive measured fork gas, principal and received output are required")
    # We do not synthesize actual callback fees from rate when unknown; caller
    # must supply observed fee from the fork trace (0 at historical demo block).
    return {
        "schema_version": 1,
        "scenario": args.scenario,
        "token_in": DAI,
        "fork_block": args.block,
        "gas_quote_block": latest_block,
        "principal_raw": assert_raw_uint(principal),
        "received_raw": assert_raw_uint(received),
        "balancer_fee_rate_wad": None if fee_rate is None else assert_raw_uint(fee_rate),
        "flash_fee_reported_raw": assert_raw_uint(args.flash_fee_raw),
        "local_fork_gas_used": gas_used,
        "rpc_current_gas_price_wei": None if gas_price is None else assert_raw_uint(gas_price),
        "gas_units_multiplier_bps": args.gas_units_multiplier_bps,
        "gas_price_multiplier_bps": args.gas_price_multiplier_bps,
        "minimum_net_profit_bps": args.min_net_profit_bps,
        "quick_wpol_reference": pair_q,
        "sushi_wpol_reference": pair_s,
        "flash_fee_historical_rpc_read": fee_rate is not None,
        "gas_price_live_rpc_read": gas_price is not None,
        "conversion_historical_rpc_read": pair_q is not None and pair_s is not None,
        "notes": [
            "FORK gas used: measurement from local synthetic test, not real transaction receipt",
            "LIVE gas quote: from block later than historical fork; not historical inclusion cost",
            "WPOL/DAI reserves: historical spot-price references; quote subject to liquidity and manipulation",
            "SYNTHETIC scenario: manipulated local reserve state; not a historically executable arbitrage",
            "NO execution permission, signer, transaction or broadcast",
        ] + messages,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--block", type=int, default=96179857)
    p.add_argument("--scenario", choices=["synthetic_fork", "historical_untouched"], default="synthetic_fork")
    p.add_argument("--gas-used", type=int, required=True, help="local execution gas from Foundry emit, not total test gas")
    p.add_argument("--principal-raw", type=int, required=True)
    p.add_argument("--received-raw", type=int, required=True)
    p.add_argument("--flash-fee-raw", type=int, default=0, help="fee actually reported inside fork callback")
    p.add_argument("--gas-units-multiplier-bps", type=int, default=15000)
    p.add_argument("--gas-price-multiplier-bps", type=int, default=20000)
    p.add_argument("--min-net-profit-bps", type=int, default=20)
    p.add_argument("--output", default="outputs/evidence_step9.json")
    args = p.parse_args()
    for v in (args.gas_units_multiplier_bps, args.gas_price_multiplier_bps):
        if not 10000 <= v <= 50000: p.error("gas multipliers must be 10000..50000 bps")
    if not 1 <= args.min_net_profit_bps <= 10000: p.error("minimum net-profit bps must be 1..10000")
    if not args.block > 0: p.error("invalid block")
    url = os.environ.get("POLYGON_RPC_URL", "")
    if not url:
        print("Missing POLYGON_RPC_URL. Use read -rsp and export in the same VPS shell.", file=sys.stderr)
        sys.exit(2)
    try:
        report = collect(Rpc(url), args)
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("STEP 9: RPC evidence saved to", out)
        print("polygon_chain_id=137", "fork_block=", args.block, "live_block=", report["gas_quote_block"])
        print("historical_fee_read=", report["flash_fee_historical_rpc_read"])
        print("historical_dual_pool_gas_conversion_read=", report["conversion_historical_rpc_read"])
        print("live_gas_price_read=", report["gas_price_live_rpc_read"])
        for line in report["notes"][5:]:
            print("BLOCKER:", line)
        print("LOCAL MODEL ONLY — NO TRANSACTION, SIGNING OR BROADCASTING")
    except Exception as exc:
        # Exceptions might include private URL in some urllib error strings.
        print("Step 9 RPC collection could not complete:", type(exc).__name__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
