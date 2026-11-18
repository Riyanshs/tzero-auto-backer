#!/usr/bin/env python3
"""Read-only demo endpoint for the tzero liftoff-circle auto-backer.

Agent Games by zkVerify — Wildcard track demo. This server holds NO keys,
signs NOTHING, and cannot trade. It exposes the agent's decision logic so a
judge can probe it with mission payloads, plus the verified trade history.

Endpoints:
  GET  /         agent info + links
  GET  /health   {"status": "ok"}
  GET  /trades   verified trade history (Base Sepolia, from trades.json)
  POST /agent    decision endpoint. Body:
                 {"type": "evaluate-mission", "task_id": "...",
                  "input": {"token": "0x...", "creator": "0x...",
                            "name": "...", "graduated": false,
                            "progressPercent": 12.5,
                            "seen_before": false, "already_bought": false}}
                 If only {"token": "..."} is given, the mission is fetched
                 live (read-only) from the tzer0 API.
                 Returns {"decision": "BUY"|"HOLD"|"SKIP", "reasons": [...],
                          "task_id": ..., "evaluated_at": ...}

Run:  python3 server.py [port]   (default 8080, or $PORT when set)
Deploy: Railway / Render / Fly.io — stdlib only, no dependencies.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TRADES = json.load(open(os.path.join(HERE, "trades.json")))
API = "https://testnet.tzer0.fun/api/missions"

# Public whitelist (creator addresses only — public on-chain data).
WHITELIST = [
    "0xe11a72fa79a272d1cfb17ec34d265b37fabcdb71",
    "0x83c01daae8317a2f2fe0c46fe1a90311a97081a50",
    "0x1bba41288c1ec9244aadafe949cd56ee8c490ebc",
    "0xdd012c4f776a780fa1290d62bb6e03bb87499ebd",
]
BUYER = "0xc9F62f232B857cC136D81e47681A866E610D9a6C"
BUY_ETH = 0.01
HOLD_MINUTES = 60


def evaluate(inp):
    """Pure decision logic — the same gates as watch.py. Returns (decision, reasons)."""
    reasons = []
    creator = (inp.get("creator") or "").lower()
    token = inp.get("token", "")
    if creator and creator not in WHITELIST:
        return "SKIP", [f"creator {inp.get('creator')} is not whitelisted"]
    if inp.get("already_bought"):
        return "SKIP", ["already bought once — never a second buy on the same mission"]
    if inp.get("graduated"):
        return "SKIP", ["mission already graduated before buy"]
    if inp.get("seen_before"):
        return "SKIP", ["not a new mission (pre-dates the whitelist) — only first-seen missions are bought"]
    if inp.get("progressPercent") is not None and float(inp["progressPercent"]) >= 100:
        return "SKIP", ["curve at 100% — already graduated"]
    # Would buy: record the supporting facts.
    if creator:
        reasons.append(f"creator {inp.get('creator')} is whitelisted")
    if inp.get("name"):
        reasons.append(f"new mission: {inp.get('name')} ({inp.get('symbol', '')})")
    if inp.get("progressPercent") is not None:
        reasons.append(f"curve progress {inp.get('progressPercent')}% — still filling")
    reasons.append(f"would buy exactly {BUY_ETH} ETH, then hold to liftoff + {HOLD_MINUTES} min before any sell")
    if token:
        reasons.append(f"mission: https://testnet.tzer0.fun/m/{token}")
    return "BUY", reasons


def fetch_mission(token):
    req = urllib.request.Request(
        f"{API}/{token}",
        headers={"User-Agent": "tzero-backer-demo/1.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        d = json.loads(r.read().decode())
    m = d.get("mission")
    if not m:
        raise RuntimeError(f"mission {token} not found")
    return {
        "token": m.get("token"),
        "name": m.get("name"),
        "symbol": m.get("symbol"),
        "creator": m.get("creator"),
        "graduated": m.get("graduated"),
        "progressPercent": m.get("progressPercent"),
        "seen_before": False,
        "already_bought": False,
    }


INFO = {
    "agent": "tzero liftoff-circle auto-backer",
    "track": "Agent Games by zkVerify — Wildcard (Season 01)",
    "description": (
        "Autonomous agent that backs new token launches on tzer0.fun "
        "(Base Sepolia testnet): auto-buys 0.01 ETH of each NEW mission from "
        "4 whitelisted creators, holds to liftoff + 60 minutes, then "
        "auto-sells the full balance. Every trade is verifiable on-chain."
    ),
    "buyer_wallet": BUYER,
    "buyer_explorer": f"https://sepolia.basescan.org/address/{BUYER}",
    "chain": "Base Sepolia (84532)",
    "launchpad": "0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c",
    "router": "0x21BbD201c69d0091707d0367D3AD6875cEc150e1",
    "whitelist_creators": WHITELIST,
    "buy_eth_per_mission": BUY_ETH,
    "hold_minutes_after_buy": HOLD_MINUTES,
    "results_2026_11_14": TRADES["summary"],
    "endpoints": {
        "GET /": "this info",
        "GET /health": "liveness check",
        "GET /trades": "verified trade history with Basescan links",
        "POST /agent": "evaluate a mission payload -> BUY / HOLD / SKIP with reasons (read-only, no keys, no signing)",
    },
    "note": (
        "Demo endpoint only: it holds no private key, signs nothing, and "
        "cannot trade. The live agent runs separately (see repo)."
    ),
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self._json(200, INFO)
        elif path == "/health":
            self._json(200, {"status": "ok"})
        elif path == "/trades":
            self._json(200, TRADES)
        else:
            self._json(404, {"error": "not found", "see": "GET /"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path != "/agent":
            self._json(404, {"error": "not found", "see": "GET /"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            self._json(400, {"error": f"invalid JSON: {e}"})
            return
        task_id = body.get("task_id", "")
        inp = body.get("input") or {}
        if not inp and body.get("token"):
            inp = {"token": body["token"]}
        # Live read-only fetch if only a token was supplied.
        if inp.get("token") and not inp.get("creator"):
            try:
                inp = fetch_mission(inp["token"])
            except Exception as e:
                self._json(200, {"decision": "SKIP", "reasons": [str(e)],
                                 "task_id": task_id,
                                 "evaluated_at": datetime.now(timezone.utc).isoformat()})
                return
        decision, reasons = evaluate(inp)
        self._json(200, {
            "decision": decision,
            "reasons": reasons,
            "task_id": task_id,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "agent": "tzero liftoff-circle auto-backer",
            "read_only": True,
        })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8080))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"tzero-backer demo endpoint listening on 0.0.0.0:{port} (read-only, no keys)", flush=True)
    srv.serve_forever()
