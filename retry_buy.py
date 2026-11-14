#!/usr/bin/env python3
"""Manual retry buy for a tzer0 mission with a LIVE ON-CHAIN quote.

Why this exists: buy.py quotes from the tzer0 API curveRaw, which lags badly
for fresh missions (example: the API said sold=0 while on-chain ~96.66M were
already sold -> minOut too high -> revert). This script quotes from on-chain
state instead:
  sold       = totalSupply - launchpad.balanceOf(token)
  vT_live    = 1_073_000_000 - sold        (vT0, verified: vT_live+sold == 1.073e9)
  vN_live    = 107_300_000 / vT_live       (k = vN0*vT0 = 0.1 * 1_073_000_000)
  out        = vT_live * dETH / (vN_live + dETH)
  minOut     = out * 0.90

Safety: simulates the buy with eth_call first; aborts if the simulation
reverts or the mission already graduated (launchpad balance 0). Waits for the
receipt and raises if the tx reverted. Verifies the buyer's token balance.

Usage: python3 retry_buy.py <token-address> [--eth 0.01] [--dry-run]
The wallet key comes from the environment (see config.py) and is NEVER printed.
"""
import json
import subprocess
import sys
import time
from decimal import Decimal, getcontext

from config import BUY_ETH, BUY_SELECTOR, CHAIN_ID, LAUNCHPAD, RPC, load_account

getcontext().prec = 50

VT0 = Decimal("1073000000")   # initial virtual token reserve
VN0 = Decimal("0.1")          # initial virtual native reserve (ETH)
K = VN0 * VT0                 # 107_300_000


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    r = subprocess.run(["curl", "-s", "-m", "20", "-X", "POST", RPC,
                        "-H", "content-type: application/json", "-d", payload],
                       capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    if "error" in d:
        raise RuntimeError(f"RPC {method} error: {d['error']}")
    return d["result"]


def eth_call(to, data, value_hex, from_addr):
    return rpc("eth_call", [{"from": from_addr, "to": to, "value": value_hex, "data": data}, "latest"])


def token_call(token, fn_sig, arg=None):
    # minimal ABI-encoded view calls: balanceOf(address) 0x70a08231,
    # totalSupply() 0x18160ddd
    data = "0x" + fn_sig
    if arg is not None:
        data += arg[2:].lower().rjust(64, "0")
    raw = eth_call(token, data, "0x0", LAUNCHPAD)
    return int(raw, 16)


def live_quote(token, eth_amount):
    total = Decimal(token_call(token, "18160ddd")) / Decimal(10) ** 18
    lp_bal = Decimal(token_call(token, "70a08231", LAUNCHPAD)) / Decimal(10) ** 18
    sold = total - lp_bal
    if lp_bal == 0:
        raise RuntimeError(f"mission {token} already graduated (launchpad balance 0) — refusing to buy")
    vT = VT0 - Decimal(sold)
    vN = K / vT
    d = Decimal(str(eth_amount))
    out = vT * d / (vN + d)
    return {
        "total_supply": total, "launchpad_balance": lp_bal, "sold": sold,
        "vToken_live": str(vT), "vNative_live": str(vN),
        "expected_tokens": out, "min_out": out * Decimal("0.90"),
        "progress_pct": float(Decimal(sold) / Decimal(total) * 100),
    }


def retry_buy(token, eth_amount=BUY_ETH, dry_run=False):
    acct = load_account()
    q = live_quote(token, eth_amount)
    min_out_wei = int(q["min_out"] * (Decimal(10) ** 18))
    value_wei = int(Decimal(str(eth_amount)) * Decimal(10) ** 18)
    data = ("0x" + BUY_SELECTOR + token[2:].lower().rjust(64, "0")
            + format(min_out_wei, "064x"))
    # SIMULATE first: eth_call must succeed or we abort before spending gas.
    try:
        eth_call(LAUNCHPAD, data, hex(value_wei), acct.address)
    except Exception as e:
        raise RuntimeError(f"simulation reverted, aborting (no gas spent): {e}\nquote={q}")
    try:
        gas_hex = rpc("eth_estimateGas", [{
            "from": acct.address, "to": LAUNCHPAD, "value": hex(value_wei), "data": data}])
        gas_limit = int(int(gas_hex, 16) * 1.3)
    except Exception:
        gas_limit = 300000
    block = rpc("eth_getBlockByNumber", ["latest", False])
    base_fee = int(block["baseFeePerGas"], 16)
    max_priority = 100_000_000
    max_fee = base_fee * 2 + max_priority
    bal = int(rpc("eth_getBalance", [acct.address, "latest"]), 16)
    if bal < value_wei + gas_limit * max_fee:
        raise RuntimeError(f"insufficient ETH: balance {bal/1e18:.6f}")
    nonce = int(rpc("eth_getTransactionCount", [acct.address, "latest"]), 16)
    # RE-QUOTE right before signing (curve moves fast).
    q = live_quote(token, eth_amount)
    min_out_wei = int(q["min_out"] * (Decimal(10) ** 18))
    data = ("0x" + BUY_SELECTOR + token[2:].lower().rjust(64, "0")
            + format(min_out_wei, "064x"))
    try:
        eth_call(LAUNCHPAD, data, hex(value_wei), acct.address)
    except Exception as e:
        raise RuntimeError(f"re-quote simulation reverted, aborting: {e}\nquote={q}")
    tx = {"chainId": CHAIN_ID, "to": LAUNCHPAD, "value": value_wei, "data": data,
          "nonce": nonce, "gas": gas_limit, "maxFeePerGas": max_fee,
          "maxPriorityFeePerGas": max_priority, "type": 2}
    signed = acct.sign_transaction(tx)
    if dry_run:
        return {"dry_run": True, "tx_hash": signed.hash.hex(), "quote": {k: str(v) for k, v in q.items()}}
    raw = rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    receipt = None
    for _ in range(24):
        time.sleep(5)
        receipt = rpc("eth_getTransactionReceipt", [raw])
        if receipt is not None:
            break
    if receipt is None:
        raise RuntimeError(f"buy tx {raw} has no receipt after ~120s — status unknown, check before retrying")
    if receipt.get("status") != "0x1":
        raise RuntimeError(f"buy tx {raw} REVERTED on-chain — ETH not spent, only gas lost")
    got = token_call(token, "70a08231", acct.address)
    return {"tx_hash": raw, "token": token, "eth": eth_amount,
            "expected_tokens": str(q["expected_tokens"]), "min_out": str(q["min_out"]),
            "tokens_received_balance": got / 1e18,
            "progress_before_pct": q["progress_pct"],
            "gas_used": int(receipt["gasUsed"], 16),
            "block": int(receipt["blockNumber"], 16)}


if __name__ == "__main__":
    token = sys.argv[1]
    eth_amount = BUY_ETH
    dry_run = False
    for a in sys.argv[2:]:
        if a == "--dry-run":
            dry_run = True
        elif a.startswith("--eth"):
            eth_amount = float(a.split("=")[1])
    print(json.dumps(retry_buy(token, eth_amount, dry_run), indent=2, default=str))
