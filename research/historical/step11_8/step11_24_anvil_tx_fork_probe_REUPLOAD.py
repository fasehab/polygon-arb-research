#!/usr/bin/env python3
"""Step 11.24: verify a Polygon after-transaction Anvil fork against traced pool state.

Read-only diagnostic: Anvil runs bound to loopback, and this program performs
eth_chainId / eth_call only. The fork process is always stopped. No wallet use.

Anvil makes provider requests internally; this test bounds runtime, not remote
RPC request/credit count. Never print your endpoint or fork logs publicly.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

TARGET_HASH = "0x355a059ebe4926b290a4b777edf7143ce0fcf2b5aea8389c8539b0369a19c461"
BLOCK = 96179651
TARGET_INDEX = 20
ALG = "0xa5cd8351cbf30b531c7b11b0d9d3ff38ea2e280f"
UNI = "0xeef1a9507b3d505f0062f2be9453981255b503c8"
EXPECTED = {
    "algebra": {"sqrt_price_x96": 2241340274156083598680336563302, "tick": 66853, "liquidity_raw": 26858104422},
    "uniswap_500": {"sqrt_price_x96": 2242245549720868207229490053548, "tick": 66861, "liquidity_raw": 175899081147},
}
# Function selectors (Solidity ABI)
GLOBAL_STATE = "0xe76c01e4"
SLOT0 = "0x3850c7bd"
LIQUIDITY = "0x1a686502"


def unpack_words(data):
    if not isinstance(data, str) or not data.startswith("0x"):
        raise ValueError("nonhex eth_call result")
    data = data[2:]
    if len(data) < 64 or len(data) % 64 or any(c not in "0123456789abcdefABCDEF" for c in data):
        raise ValueError("invalid ABI words")
    return [int(data[i:i + 64], 16) for i in range(0, len(data), 64)]


def signed_int24(word):
    n = word & 0xffffff
    return n - (1 << 24) if n & (1 << 23) else n


def local_rpc(method, params, port, timeout=5):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(
        "http://127.0.0.1:%d" % port, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError("local RPC response too large")
    result = json.loads(raw)
    if "error" in result:
        raise ValueError("local RPC error code: %s" % result["error"].get("code"))
    if "result" not in result:
        raise ValueError("missing RPC result")
    return result["result"]


def read_pool(address, getter, port):
    state = unpack_words(local_rpc("eth_call", [{"to": address, "data": getter}, "latest"], port))
    if len(state) < 2:
        raise ValueError("incomplete pool getter")
    liq = unpack_words(local_rpc("eth_call", [{"to": address, "data": LIQUIDITY}, "latest"], port))[0]
    return {"sqrt_price_x96": state[0], "tick": signed_int24(state[1]), "liquidity_raw": liq}


def self_test():
    assert signed_int24(66853) == 66853
    assert signed_int24((1 << 256) - 1) == -1
    assert signed_int24(1 << 23) == -(1 << 23)
    assert unpack_words("0x" + "0" * 63 + "a") == [10]
    assert all(v["liquidity_raw"] > 0 for v in EXPECTED.values())
    print("STEP 11.24 OFFLINE PARSER CHECKS: PASS (NOT RPC PROOF)")


def run(args):
    endpoint = os.environ.get("POLYGON_RPC_URL", "")
    if not endpoint.startswith("https://"):
        raise SystemExit("FAIL: export your existing POLYGON_RPC_URL as an HTTPS URL")
    anvil = args.anvil
    if not Path(anvil).is_file():
        raise SystemExit("UNAVAILABLE: Anvil binary not found at " + anvil)
    help_run = subprocess.run([anvil, "--help"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=8)
    if help_run.returncode or "--fork-transaction-hash" not in help_run.stdout:
        raise SystemExit("UNAVAILABLE: Installed Anvil does not advertise --fork-transaction-hash")

    argv = [
        anvil, "--fork-url", endpoint,
        "--fork-transaction-hash", TARGET_HASH,
        "--chain-id", "137", "--host", "127.0.0.1", "--port", str(args.port),
        "--retries", "0", "--timeout", "15000",
    ]
    report = {
        "step": "11.24", "target_block": BLOCK, "target_tx_index": TARGET_INDEX,
        "target_tx_hash": TARGET_HASH, "read_only": True,
        "remote_rpc_requests": "NOT_COUNTED_BY_THIS_PROBE",
        "expected": EXPECTED, "observed": None,
        "verdict": "UNPROVEN",
    }
    process = None
    begin = time.monotonic()
    try:
        # Suppress Anvil's default dev-account key display and avoid logging
        # the private RPC endpoint to disk. Never bind the fork publicly.
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=open("/root/anvil_step11_24_error.log", "w"),
            start_new_session=True,
        )
        ready = False
        while time.monotonic() - begin < args.deadline - 15:
            if process.poll() is not None:
                report["reason"] = "ANVIL_EXITED_DURING_REPLAY"
                report["exit_code"] = process.returncode
                break
            try:
                chain_id = int(local_rpc("eth_chainId", [], args.port, 3), 16)
                if chain_id != 137:
                    report["reason"] = "WRONG_LOCAL_CHAIN_ID"
                    break
                ready = True
                break
            except (ValueError, urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(1)
        if not ready and "reason" not in report:
            report["reason"] = "REPLAY_STARTUP_TIMEOUT"
        if ready:
            if time.monotonic() - begin > args.deadline - 12:
                report["reason"] = "NO_TIME_LEFT_FOR_VERIFICATION"
            else:
                obs = {
                    "algebra": read_pool(ALG, GLOBAL_STATE, args.port),
                    "uniswap_500": read_pool(UNI, SLOT0, args.port),
                }
                report["observed"] = obs
                report["comparisons"] = {name: {key: obs[name][key] == val
                    for key, val in values.items()} for name, values in EXPECTED.items()}
                if all(all(d.values()) for d in report["comparisons"].values()):
                    report["verdict"] = "LOCAL_FORK_MATCHES_INDEPENDENT_TRACE"
                    report["reason"] = "Two pools' price, tick and active liquidity equal prior full-block tracing evidence"
                else:
                    report["reason"] = "LOCAL_FORK_STATE_MISMATCH"
    except (OSError, ValueError, urllib.error.URLError, TimeoutError, subprocess.SubprocessError) as exc:
        # Do not print str(exc); it could include an endpoint and API key.
        report["reason"] = "REPLAY_OR_RPC_ERROR_" + type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=4)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
        report["elapsed_seconds"] = round(time.monotonic() - begin, 1)
        report_path = Path(args.output)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("STEP 11.24 — HISTORICAL TX-HASH FORK FEASIBILITY — READ ONLY")
    print("verdict:", report["verdict"])
    print("reason:", report.get("reason"))
    if report.get("observed"):
        print("pool_state_matches:", {n: all(v.values()) for n, v in report["comparisons"].items()})
        print("observed:", report["observed"])
    print("elapsed_seconds:", report["elapsed_seconds"])
    print("saved:", args.output)
    print("REMOTE RPC CREDIT COUNT NOT KNOWN / NO SIGNING / NO BROADCAST")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Check Anvil after-transaction fork against independently traced state")
    ap.add_argument("--anvil", default="/root/.foundry/bin/anvil")
    ap.add_argument("--port", type=int, default=18547)
    ap.add_argument("--deadline", type=int, default=105)
    ap.add_argument("--output", default="outputs/step11_24_anvil_tx_fork.json")
    ap.add_argument("--self-test", action="store_true")
    opts = ap.parse_args()
    if opts.self_test:
        self_test()
    else:
        run(opts)
