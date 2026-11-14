#!/usr/bin/env python3
"""tzer0 liftoff-circle auto-backer — watcher.

Polls the tzer0 API for NEW missions from WHITELISTED creators and buys
exactly 0.01 ETH of each one, then holds it to liftoff + 60 minutes and
auto-sells the full balance for ETH.

Whitelist: whitelist.json  {"creators": [{"address", "label", "added"}]}
Buyer wallet: from the environment (see config.py). Base Sepolia (testnet only).

Rules:
- One buy of exactly 0.01 ETH per NEW mission from ANY whitelisted creator.
- Never a second buy on the same mission.
- HOLD after buying (min 60 min, until liftoff). Sell ONLY after liftoff AND
  only once the 60-minute hold is complete (both gates, in that order).
- A mission lifts off (graduates) at 100% progress.
- If the creator is NOT whitelisted, the mission already graduated, or there
  is not enough testnet ETH: do NOT buy, log it, do not improvise.

State: state.json
  { "seen": { <token>: {"id", "name", "creator", "first_seen"} },
    "buys": { <token>: {"id", "name", "symbol", "creator", "tx_hash",
                        "bought_at", "eth": 0.01,
                        "liftoff": null | {"at", "progress": 100},
                        "sold": null | {"tx_hash", "at", "expected_eth", "min_eth"}} } }

Usage: python3 watch.py [--seed]
  --seed : (re)build the seen list from ALL whitelisted creators' CURRENT
           missions without buying. Use when a new creator is added so only
           missions deployed AFTER they were whitelisted get auto-bought.
Run it on a schedule (e.g. every 2 minutes via cron).
"""
import datetime
import json
import os
import subprocess
import sys
import time

from config import API, BUY_ETH, STATE_PATH, WHITELIST_PATH

HERE = os.path.dirname(os.path.abspath(__file__))
BUY_PY = os.path.join(HERE, "buy.py")
SELL_PY = os.path.join(HERE, "sell.py")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_whitelist():
    d = json.load(open(WHITELIST_PATH))
    return [c["address"].lower() for c in d.get("creators", [])]


def load_state():
    if os.path.exists(STATE_PATH):
        return json.load(open(STATE_PATH))
    return {"seen": {}, "buys": {}}


def save_state(s):
    json.dump(s, open(STATE_PATH, "w"), indent=2)


def fetch_missions(creator):
    r = subprocess.run(["curl", "-s", "-m", "25", "-A", "Mozilla/5.0",
                        f"{API}?creator={creator}"],
                       capture_output=True, text=True, timeout=40)
    d = json.loads(r.stdout)
    return d.get("missions", [])


def fetch_mission(token):
    r = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0",
                        f"{API}/{token}"], capture_output=True, text=True, timeout=30)
    d = json.loads(r.stdout)
    return d.get("mission")


def buy_token(token):
    r = subprocess.run([sys.executable, BUY_PY, token], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else r.stdout.strip())
    return json.loads(r.stdout)


def main():
    seed = "--seed" in sys.argv
    st = load_state()
    allowed = set(load_whitelist())
    missions = []
    for creator in allowed:
        try:
            missions.extend(fetch_missions(creator))
        except Exception as e:
            print(f"fetch failed for {creator}: {e}", flush=True)
    log = []
    if seed:
        for m in missions:
            st["seen"].setdefault(m["token"], {"id": m["id"], "name": m["name"],
                                               "creator": m.get("creator"),
                                               "first_seen": now_iso()})
        save_state(st)
        print(f"seeded {len(st['seen'])} missions from {len(allowed)} whitelisted creators, no buys")
        return
    for m in missions:
        tok = m["token"]
        creator = (m.get("creator") or "").lower()
        if creator not in allowed:
            log.append(f"SKIP {m['name']} {tok}: creator {m.get('creator')} not whitelisted")
            continue
        is_new = tok not in st["seen"]
        if is_new:
            st["seen"][tok] = {"id": m["id"], "name": m["name"], "creator": m.get("creator"),
                               "first_seen": now_iso()}
        if tok in st["buys"]:
            continue  # already bought once — never a second buy
        if not is_new:
            # Seen before (e.g. marked by --seed when the creator was whitelisted)
            # but never bought: a pre-whitelist deploy. Do NOT buy it — only
            # missions first seen in THIS run are new.
            continue
        if m.get("graduated"):
            log.append(f"SKIP {m['name']} {tok}: already graduated before buy")
            continue
        # NEW mission from a whitelisted creator — buy BUY_ETH
        try:
            res = buy_token(tok)
            st["buys"][tok] = {"id": m["id"], "name": res.get("name") or m["name"],
                               "symbol": res.get("symbol") or m.get("symbol"),
                               "creator": m.get("creator"),
                               "tx_hash": res["tx_hash"], "bought_at": now_iso(),
                               "eth": BUY_ETH,
                               "expected_tokens": res.get("expected_tokens"),
                               "liftoff": None}
            log.append(f"BOUGHT {m['name']} ({m.get('symbol')}) {tok} {BUY_ETH} ETH "
                       f"tx {res['tx_hash']} creator {m.get('creator')}")
        except Exception as e:
            log.append(f"BUY FAILED {m['name']} {tok}: {e}")
    # liftoff check for held positions + AUTO-SELL once a mission has lifted
    # off AND the 60-minute hold is complete (both gates, in that order).
    for tok, b in st["buys"].items():
        if b.get("liftoff") and b.get("sold"):
            continue
        try:
            cur = fetch_mission(tok)
        except Exception:
            cur = None
        if not (cur and cur.get("graduated")):
            continue
        if not b.get("liftoff"):
            b["liftoff"] = {"at": now_iso(), "progress": 100}
            log.append(f"LIFTOFF {b['name']} ({b.get('symbol')}) {tok} — graduated, "
                       f"bought {b['bought_at']}, held to liftoff.")
        bought_dt = datetime.datetime.strptime(
            b["bought_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        held_min = (datetime.datetime.now(datetime.timezone.utc) - bought_dt).total_seconds() / 60.0
        if held_min < 60:
            log.append(f"HOLD {b['name']} ({b.get('symbol')}) {tok} — liftoff done but "
                       f"only {held_min:.0f}/60 min held; auto-sell waits for the 60-min mark.")
            continue
        # auto-sell the full balance
        try:
            r = subprocess.run([sys.executable, SELL_PY, tok],
                               capture_output=True, text=True, timeout=300)
            out = r.stdout.strip().splitlines()
            last = json.loads(out[-1]) if out else {}
            if r.returncode == 0 and "sell_tx" in last:
                b["sold"] = {"tx_hash": last["sell_tx"], "at": now_iso(),
                             "expected_eth": last.get("expected_eth"), "min_eth": last.get("min_eth")}
                log.append(f"AUTO-SOLD {b['name']} ({b.get('symbol')}) {tok} — "
                           f"liftoff + 60-min hold done, tx {last['sell_tx']}, "
                           f"expected ~{last.get('expected_eth')} ETH.")
            else:
                log.append(f"AUTO-SELL FAILED {b['name']} {tok}: {last.get('error', r.stderr.strip()[:200])}")
        except Exception as e:
            log.append(f"AUTO-SELL FAILED {b['name']} {tok}: {e}")
    save_state(st)
    print("\n".join(log) if log else "no new missions, no liftoffs")


if __name__ == "__main__":
    main()
