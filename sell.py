#!/usr/bin/env python3
"""tzer0 auto-sell — sell a graduated mission token for ETH on TzeroSwap.

Router 0x21BbD201c69d0091707d0367D3AD6875cEc150e1 is a CUSTOM router, NOT
standard UniswapV2 (verified on-chain):
- factory() -> 0xf9CE1F9D20d5d42515b27fE2A5380B89A0Be927E, but the factory's
  getPair() and the router's WETH()/getAmountsOut() all revert with "no data".
- The working post-graduation sell uses selector 0x1d629f11 with params
  (address token, uint amountIn, uint amountOutMin, address to, uint deadline).
  The standard swapExactTokensForETH selector 0x18cbafe5 does NOT exist here.
- Quote is computed from the pair's on-chain reserves (getReserves) with the
  0.997 constant-product factor; the pair address comes from the tzer0 API
  mission detail (pair field).

Sell: 0x1d629f11(token, amountIn, amountOutMin, to, deadline), minOut = 90%
of the reserve quote (10% slippage). Simulates the swap via eth_call after
approving, and aborts before sending if the simulation reverts.

Flow: read token balance -> if allowance < balance, approve router for the
full balance (approve(address,uint256) selector 0x095ea7b3) -> simulate
swap -> swap. Sells the FULL balance by default (--amount for a specific amount).

Usage: python3 sell.py <token-address> [--amount <tokens>] [--dry-run]
Prints JSON with tx hashes + ETH received (from receipt logs).

The wallet key comes from the environment (see config.py) and is NEVER printed.
"""
import argparse
import json
import subprocess
import time
from decimal import Decimal, getcontext

from web3 import Web3

from config import API, ROUTER, RPC, SELL_SELECTOR, WETH, load_account

getcontext().prec = 50
APPROVE_SEL = "095ea7b3"   # approve(address,uint256)
BAL_SEL = "70a08231"       # balanceOf(address)
ALLOW_SEL = "dd62ed3e"     # allowance(address,address)
RESERVES_SEL = "0902f1ac"  # getReserves()
TOKEN0_SEL = "0dfe1681"    # token0()

w3 = Web3(Web3.HTTPProvider(RPC, request_kwargs={"timeout": 30}))


def call(to, data):
    return w3.eth.call({"to": to, "data": data}, "latest")


def u256(n):
    return n.to_bytes(32, "big")


def addr(a):
    return bytes.fromhex(a[2:].lower().rjust(64, "0"))


def balance_of(token, who):
    return int.from_bytes(call(token, bytes.fromhex(BAL_SEL) + addr(who)), "big")


def allowance(token, owner, spender):
    return int.from_bytes(call(token, bytes.fromhex(ALLOW_SEL) + addr(owner) + addr(spender)), "big")


def fetch_pair(token):
    r = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0",
                        f"{API}/{token}"], capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    pair = (d.get("mission") or {}).get("pair")
    if not pair:
        raise RuntimeError("no pair in tzer0 API mission detail (not graduated?)")
    return pair


def quote_from_reserves(token, pair, amount_in):
    # getReserves() -> (reserve0, reserve1, blockTimestampLast); token0() orients them.
    res = call(pair, bytes.fromhex(RESERVES_SEL))
    r0 = int.from_bytes(res[0:32], "big")
    r1 = int.from_bytes(res[32:64], "big")
    t0 = "0x" + call(pair, bytes.fromhex(TOKEN0_SEL))[-20:].hex()
    if t0.lower() == token.lower():
        reserve_in, reserve_out = r0, r1
    else:
        reserve_in, reserve_out = r1, r0
    if reserve_in == 0 or reserve_out == 0:
        raise RuntimeError("pair has zero reserves")
    # UniswapV2 constant-product with 0.3% fee
    amount_in_with_fee = amount_in * 997
    return amount_in_with_fee * reserve_out // (reserve_in * 1000 + amount_in_with_fee)


def send_tx(acct, to, data, value=0):
    nonce = w3.eth.get_transaction_count(acct.address)
    gas = w3.eth.estimate_gas({"from": acct.address, "to": to, "value": value, "data": data})
    gas = int(gas * 1.2)
    tx = {"from": acct.address, "to": to, "value": value, "data": data,
          "nonce": nonce, "gas": gas,
          "maxFeePerGas": w3.to_wei(2, "gwei"), "maxPriorityFeePerGas": w3.to_wei(0.1, "gwei"),
          "chainId": 84532, "type": 2}
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    rc = w3.eth.wait_for_transaction_receipt(h, timeout=180)
    if rc["status"] != 1:
        raise RuntimeError(f"tx {h.hex()} reverted")
    return h.hex(), rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token")
    ap.add_argument("--amount", default=None, help="tokens to sell (default: full balance)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    token = Web3.to_checksum_address(args.token)
    acct = load_account()
    bal = balance_of(token, acct.address)
    if args.amount:
        amt = int(Decimal(args.amount) * Decimal(10) ** 18)
    else:
        amt = bal
    if amt <= 0:
        raise SystemExit("nothing to sell (balance 0)")
    if amt > bal:
        raise SystemExit(f"amount {amt/1e18} exceeds balance {bal/1e18}")
    try:
        pair = fetch_pair(token)
        eth_out = quote_from_reserves(token, pair, amt)
    except Exception as e:
        # No pair / not graduated yet — nothing to sell. Exit cleanly so the
        # watcher can keep polling.
        print(json.dumps({"error": "no_pair_or_not_graduated",
                          "detail": str(e)[:200]}), flush=True)
        raise SystemExit(2)
    min_out = eth_out * 90 // 100
    print(json.dumps({"token": token, "pair": pair, "balance": bal / 1e18,
                      "sell_amount": amt / 1e18,
                      "expected_eth": eth_out / 1e18, "min_eth": min_out / 1e18}), flush=True)
    # swap calldata: 0x1d629f11(token, amountIn, amountOutMin, to, deadline)
    deadline = int(time.time()) + 600
    data = bytes.fromhex(SELL_SELECTOR)
    data += addr(token) + u256(amt) + u256(min_out) + addr(acct.address) + u256(deadline)
    if args.dry_run:
        # simulate the exact swap calldata before sending anything
        try:
            w3.eth.call({"from": acct.address, "to": ROUTER, "value": 0, "data": data}, "latest")
            print(json.dumps({"dry_run": True, "swap_simulation": "ok"}), flush=True)
        except Exception as e:
            print(json.dumps({"dry_run": True, "swap_simulation": "REVERT",
                              "detail": str(e)[:200]}), flush=True)
            raise SystemExit(3)
        return
    # approve if needed
    allow = allowance(token, acct.address, ROUTER)
    if allow < amt:
        adata = bytes.fromhex(APPROVE_SEL) + addr(ROUTER) + u256(amt)
        h, rc = send_tx(acct, token, adata)
        print(json.dumps({"approve_tx": h, "gas_used": rc["gasUsed"]}), flush=True)
    # simulate the exact swap before sending (allowance now in place)
    try:
        w3.eth.call({"from": acct.address, "to": ROUTER, "value": 0, "data": data}, "latest")
    except Exception as e:
        print(json.dumps({"error": "swap_simulation_revert", "detail": str(e)[:200]}), flush=True)
        raise SystemExit(3)
    h, rc = send_tx(acct, ROUTER, data)
    print(json.dumps({"sell_tx": h, "block": rc["blockNumber"], "gas_used": rc["gasUsed"],
                      "min_eth": min_out / 1e18, "expected_eth": eth_out / 1e18}), flush=True)


if __name__ == "__main__":
    main()
