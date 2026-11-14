#!/usr/bin/env python3
"""tzer0 backer — buy helper (Base Sepolia, testnet only).

Buys a mission token on the tzer0 bonding curve:
  launchpad 0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c
  buy(address token, uint256 minTokensOut) selector 0xcce7ec13, payable in ETH.

minTokensOut is computed from the mission's live curve (constant-product
virtual reserves vNative/vToken from GET /api/missions/<token>):
  tokensOut = vToken * dETH / (vNative + dETH)
  minOut    = tokensOut * 0.90   (10% slippage, same as the deployer's own buys)

Safety:
- Re-quotes the live curve immediately before signing (the curve moves fast).
- Waits for the tx receipt and RAISES if it reverted — broadcasting is not
  a successful buy; a reverted buy is never recorded as a buy.
- Refuses to buy a mission that already graduated.

Usage:
  python3 buy.py <token-address> [--eth 0.01] [--dry-run]
  --dry-run builds + signs the tx but does NOT broadcast.

The wallet key comes from the environment (see config.py) and is NEVER printed.
"""
import json
import subprocess
import sys
from decimal import Decimal, getcontext

from config import (
    API, BUY_ETH, BUY_SELECTOR, CHAIN_ID, LAUNCHPAD, RPC, load_account,
)

getcontext().prec = 50


def rpc(method, params):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    r = subprocess.run(["curl", "-s", "-m", "20", "-X", "POST", RPC,
                        "-H", "content-type: application/json", "-d", payload],
                       capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    if "error" in d:
        raise RuntimeError(f"RPC {method} error: {d['error']}")
    return d["result"]


def get_mission(token):
    r = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0",
                        f"{API}/{token}"], capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    if "mission" not in d:
        raise RuntimeError(f"mission {token} not found: {r.stdout[:200]}")
    return d["mission"]


def quote_buy(curve_raw, eth_amount):
    """Constant-product quote from virtual reserves. Returns (tokensOut, minOut)."""
    vN = Decimal(curve_raw["vNative"])
    vT = Decimal(curve_raw["vToken"])
    d = Decimal(str(eth_amount))
    out = vT * d / (vN + d)
    return out, out * Decimal("0.90")


def build_buy_tx(token, eth_amount, min_out_wei, nonce, gas_limit, max_fee, max_priority):
    data = ("0x" + BUY_SELECTOR
            + token[2:].lower().rjust(64, "0")
            + format(min_out_wei, "064x"))
    return {
        "chainId": CHAIN_ID,
        "to": LAUNCHPAD,
        "value": int(Decimal(str(eth_amount)) * Decimal(10) ** 18),
        "data": data,
        "nonce": nonce,
        "gas": gas_limit,
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority,
        "type": 2,
    }


def buy(token, eth_amount=BUY_ETH, dry_run=False):
    acct = load_account()
    mission = get_mission(token)
    if mission.get("graduated"):
        raise RuntimeError(f"mission {token} already graduated — refusing to buy")
    tokens_out, min_out = quote_buy(mission["curveRaw"], eth_amount)
    min_out_wei = int(min_out * (Decimal(10) ** 18))
    # balance check
    bal = int(rpc("eth_getBalance", [acct.address, "latest"]), 16)
    value_wei = int(Decimal(str(eth_amount)) * Decimal(10) ** 18)
    # gas estimate (fall back to 300k)
    try:
        gas_hex = rpc("eth_estimateGas", [{
            "from": acct.address, "to": LAUNCHPAD, "value": hex(value_wei),
            "data": "0x" + BUY_SELECTOR + token[2:].lower().rjust(64, "0") + format(min_out_wei, "064x"),
        }])
        gas_limit = int(int(gas_hex, 16) * 1.3)  # 30% headroom
    except Exception:
        gas_limit = 300000
    # fee (EIP-1559)
    block = rpc("eth_getBlockByNumber", ["latest", False])
    base_fee = int(block["baseFeePerGas"], 16)
    max_priority = 100_000_000  # 0.1 gwei
    max_fee = base_fee * 2 + max_priority
    gas_cost = gas_limit * max_fee
    if bal < value_wei + gas_cost:
        raise RuntimeError(
            f"insufficient Base Sepolia ETH: balance {bal/1e18:.6f}, need "
            f"{(value_wei + gas_cost)/1e18:.6f} (buy {eth_amount} + gas)")
    nonce = int(rpc("eth_getTransactionCount", [acct.address, "latest"]), 16)
    # RE-QUOTE right before signing so minOut reflects the price at broadcast
    # time, not the price from seconds earlier (the curve moves fast).
    mission = get_mission(token)
    if mission.get("graduated"):
        raise RuntimeError(f"mission {token} already graduated — refusing to buy")
    tokens_out, min_out = quote_buy(mission["curveRaw"], eth_amount)
    min_out_wei = int(min_out * (Decimal(10) ** 18))
    tx = build_buy_tx(token, eth_amount, min_out_wei, nonce, gas_limit, max_fee, max_priority)
    signed = acct.sign_transaction(tx)
    if dry_run:
        return {"dry_run": True, "tx_hash": signed.hash.hex(),
                "token": token, "name": mission.get("name"),
                "eth": eth_amount, "expected_tokens": str(tokens_out),
                "min_out": str(min_out), "gas_limit": gas_limit,
                "from": acct.address}
    raw = rpc("eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
    # RECEIPT CHECK: broadcasting is NOT a successful buy. Wait for the
    # receipt and raise if it reverted, so the watcher records BUY FAILED
    # instead of a phantom buy.
    import time as _time
    receipt = None
    for _ in range(24):  # up to ~120s
        _time.sleep(5)
        receipt = rpc("eth_getTransactionReceipt", [raw])
        if receipt is not None:
            break
    if receipt is None:
        raise RuntimeError(
            f"buy tx {raw} has no receipt after ~120s — status unknown, "
            "NOT recording as a buy; check the tx before retrying")
    if receipt.get("status") != "0x1":
        raise RuntimeError(
            f"buy tx {raw} REVERTED on-chain (status {receipt.get('status')}) "
            "— ETH not spent, only gas lost; NOT recording as a buy")
    return {"tx_hash": raw, "token": token, "name": mission.get("name"),
            "symbol": mission.get("symbol"), "eth": eth_amount,
            "expected_tokens": str(tokens_out), "min_out": str(min_out),
            "gas_limit": gas_limit, "from": acct.address,
            "progress_before": mission.get("progressPercent")}


if __name__ == "__main__":
    token = sys.argv[1]
    eth_amount = BUY_ETH
    dry_run = False
    for a in sys.argv[2:]:
        if a == "--dry-run":
            dry_run = True
        elif a.startswith("--eth"):
            eth_amount = float(a.split("=")[1])
    result = buy(token, eth_amount, dry_run)
    print(json.dumps(result, indent=2))
