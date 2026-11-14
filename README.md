# tzero liftoff-circle auto-backer

An autonomous agent that backs new token launches ("missions") on
[tzer0.fun](https://testnet.tzer0.fun) — the launchpad for Zero (LayerZero's
new L1), live on **Base Sepolia testnet** — and sells them after they
"liftoff" (graduate the bonding curve).

**Agent Games by zkVerify — Wildcard track entry (Season 01, Nov 2026).**

It is a real, working agent, not a demo: it ran live on 2026-11-14 and its
every trade is verifiable on-chain (Base Sepolia). Buyer wallet
`0xc9F62f232B857cC136D81e47681A866E610D9a6C`
([Basescan](https://sepolia.basescan.org/address/0xc9F62f232B857cC136D81e47681A866E610D9a6C)).

## What problem it solves

New launchpads are a race: a mission's bonding curve fills in minutes, and
the best entries are the ones placed in the first seconds after deploy — but
only from creators you trust. A human can't watch 4 creators' deploys
24/7 and react in seconds. This agent does: it polls the launchpad API,
buys a fixed small stake of every **new** mission from a **whitelisted**
circle of creators, holds it through liftoff, and sells the full balance
once the position has matured — with no human in the loop and no discretion
to chase anything outside the whitelist.

The whitelist is a *mutual liftoff circle*: the four creators back each
other's missions the same way, so every buy also helps a friend's launch
reach liftoff. The agent is the automation of a social contract, not a
sniper.

## How it decides

Every 2 minutes `watch.py` runs one cycle:

1. **Fetch** all current missions for each whitelisted creator
   (`GET /api/missions?creator=<address>`).
2. **Gate — whitelist:** creator not in `whitelist.json` → skip, log, never buy.
3. **Gate — new only:** a mission is "new" only if first seen in *this*
   run. Missions that pre-date the whitelist (marked by `--seed`) are never
   bought. A mission already bought once is never bought again.
4. **Gate — not graduated:** already graduated before the buy → skip.
5. **Buy** exactly `TZERO_BUY_ETH` (default 0.01 ETH) via `buy.py`:
   `buy(token, minTokensOut)` on launchpad
   `0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c`, with `minTokensOut` = 90%
   of the constant-product quote from the live curve
   (`tokensOut = vToken * dETH / (vNative + dETH)`). The curve is
   **re-quoted immediately before signing** — the API curve lags and a
   stale quote reverts (learned the hard way; `retry_buy.py` quotes
   straight from on-chain reserves instead).
6. **Receipt check:** broadcasting is not a buy. `buy.py` waits up to ~120s
   for the receipt and raises if it reverted — a reverted buy is never
   recorded, only gas is lost.
7. **Hold:** after buying, the position is held. It is sold only when
   **both** gates pass, in order: (a) the mission graduated (100%
   progress), **and** (b) 60 minutes have passed since the buy.
8. **Sell** the full balance via `sell.py` on the TzeroSwap custom router
   `0x21BbD201c69d0091707d0367D3AD6875cEc150e1`. The standard UniswapV2
   `swapExactTokensForETH` selector does **not** exist on this router — the
   working sell is the custom `0x1d629f11(token, amountIn, amountOutMin, to,
   deadline)` with `minOut` = 90% of the quote computed from the pair's
   on-chain `getReserves` (0.997 factor). The exact swap calldata is
   **simulated via `eth_call` after approving**; if the simulation reverts,
   nothing is sent.

## Safety rules (built in, not just documented)

- Keys only from the environment (`TZERO_PRIVATE_KEY` or `TZERO_KEY_FILE`);
  no key, seed, or session token anywhere in this repo.
- Fixed stake per mission (0.01 ETH), one buy per mission, whitelist-only.
- No approvals beyond the exact token balance being sold, and only to the
  verified router.
- Every state-changing call is simulated first and receipt-checked after.
- The agent never signs anything except its own scoped buys/sells, and
  never touches any other wallet, token, or chain.
- Testnet only (Base Sepolia). No mainnet funds are ever at risk.

## Architecture

```
watch.py ── poll ──▶ tzer0 API (/api/missions) ──▶ NEW + whitelisted?
   │  (cron, every 2 min)                              │
   ├─ buy ──▶ buy.py ──▶ launchpad buy() ──▶ receipt check ──▶ state.json
   │                                                          │
   └─ sell ─▶ sell.py ─▶ TzeroSwap 0x1d629f11 ─▶ simulate ──▶ state.json
retry_buy.py — manual retry with a live ON-CHAIN quote (API curve is stale)
config.py — public constants + env-based key loading (the only place keys load)
```

State is a single JSON file (`state.json`): `seen` missions and `buys`
with buy tx, liftoff time, and sell tx. Everything is append-only history.

## Verified results — 2026-11-14 (Base Sepolia, all testnet ETH)

25 auto-buys of 0.01 ETH in one day. 21 positions closed, 4 still held
(TUAN, MYSTIX, STRIDER, ORBIT — no liftoff yet). **17 wins / 4 losses**
on a whole-trade basis. Total deployed 0.37 ETH (0.21 auto-buys + one
manual 0.16 ETH TDOGE fill, see note), total returned at sell quotes
0.7424 ETH, **net ≈ +0.37 ETH** before gas (testnet gas is negligible).

"Sold for" is the expected ETH from the sell quote at execution
(`minOut` = 90% of quote); actual received can differ slightly by
slippage. Every tx below is on-chain — click to verify.

| Bought (WIB) | Mission | Buy tx | In (ETH) | Sold for (ETH) | Net (ETH) | Sell tx |
|---|---|---|---|---|---|---|
| 2026-11-14 00:55:28 WIB | Zero Signal (ZSIG) | [buy](https://sepolia.basescan.org/tx/0xaeda1bd8f8f1a2e822fe6e4fadd3d250388ca07e8c8c3ac34c7fb6e1089e9f49) | 0.01 | 0.01238 | +0.00238 | [sell](https://sepolia.basescan.org/tx/0x846f547c11af36d8d27793869f6e4bbf0a2423309ddda3a7286e34797e81c689) |
| 2026-11-14 00:55:38 WIB | Zero Signal (ZSIG) | [buy](https://sepolia.basescan.org/tx/0xd727353be1d019b78f7e12d20f433ff56ac15996bd2677c25ab3ec741f88155d) | 0.01 | 0.05930 | +0.04930 | [sell](https://sepolia.basescan.org/tx/0xb15b1f30d26699852455e3e709d6210fbdcb456f57eb9f4e13295a66d1d8f66c) |
| 2026-11-14 01:48 WIB | Mystix (MYSTIX) | [buy](https://sepolia.basescan.org/tx/0xf3ad414db7e048b67324be9f324f6b50207f2738075a880d90723df546bb6c0e) | 0.01 | held | — | — |
| 2026-11-14 01:48 WIB | Strider (STRIDER) | [buy](https://sepolia.basescan.org/tx/0x523b9c59d6c32e9bb1a37909bf9d52fca8c4d3a62365fd7ac6bc1c58165f9343) | 0.01 | held | — | — |
| 2026-11-14 01:48 WIB | Tuan (TUAN) | [buy](https://sepolia.basescan.org/tx/0x10fc3458a2cd2d6090cd1d0c42ec1af349365c77ddc0166b230ade5cdef173fe) | 0.01 | held | — | — |
| 2026-11-14 01:57:42 WIB | 7okowi (owi) | [buy](https://sepolia.basescan.org/tx/0x065a77e71c75aef11aee7099eae4d4be38f29d1aef040c6415e07addc23f997c) | 0.01 | 0.03537 | +0.02537 | [sell](https://sepolia.basescan.org/tx/0xf8c587b9e0c36cd847a96684bf7dec8b93013f14843c6e59cc0169746b099e6a) |
| 2026-11-14 02:40 WIB | Zero Pioneer (ZPIO) | [buy](https://sepolia.basescan.org/tx/0x395d0cbc791ad49eb5546c19e6a46f303d8e1e1ee47e9455c43042bcd49adfc8) | 0.01 | 0.04983 | +0.03983 | [sell](https://sepolia.basescan.org/tx/0x940b1a91bd3306d772113dc8a9140fd411d61f94f20a794d60e4dd0feb5d7bfb) |
| 2026-11-14 02:52 WIB | Zero Pioneer 2 (ZPIO2) | [buy](https://sepolia.basescan.org/tx/0x21cba44467a87ffe2b263b5a8e2773458c451b3d2afdf304d5191708309abb13) | 0.01 | 0.01599 | +0.00599 | [sell](https://sepolia.basescan.org/tx/0x048a29e879db6b49b9d1284ce48f759e1cfb6f558808482bb6ec9031e2c748ac) |
| 2026-11-14 02:54 WIB | Orbit (ORBIT) | [buy](https://sepolia.basescan.org/tx/0x911d120ea8621f4cd5eae14267f9fff42e31570a6f20caff210a6449ff90c353) | 0.01 | held | — | — |
| 2026-11-14 03:24 WIB | Thunder Zero (TZERO) | [buy](https://sepolia.basescan.org/tx/0x31826ed157a2f79b9e84733f53315056c29e31c569c195bf52e33c9486a34122) | 0.01 | 0.07865 | +0.06865 | [sell](https://sepolia.basescan.org/tx/0xb8c993dfd4e2b75aa25476bf7f8f53e0dec5f5ffba419ad587c6185fe7ad161f) |
| 2026-11-14 05:12 WIB | Zero (ZERO) | [buy](https://sepolia.basescan.org/tx/0xb1ac8fdecdcba7cfab243d2bd90415b060797dbc1c7486ad3ca27b37adac3b50) | 0.01 | 0.01071 | +0.00071 | [sell](https://sepolia.basescan.org/tx/0x3045be6e93bb5a44031ed7bfc4c94642fb754d0df6a37d77515d66827c23866d) |
| 2026-11-14 06:57 WIB | TSIBHA INU (TSHIB) | [buy](https://sepolia.basescan.org/tx/0xd5aad25426f43e5724b1c80d75796fdf6008eb214dc312614aa211e91b3dc8e8) | 0.01 | 0.01698 | +0.00698 | [sell](https://sepolia.basescan.org/tx/0x0f4860c25c0cf96d193ca2ec05ba9cb7f0737a2bb30e79524a33cf328f8ad989) |
| 2026-11-14 07:06 WIB | Lfg (LFG) | [buy](https://sepolia.basescan.org/tx/0x829273a02d97c3c68bdd3e99bf9268be32e86ce1a622a2575af5b048d65d7b2a) | 0.01 | 0.01592 | +0.00592 | [sell](https://sepolia.basescan.org/tx/0xd6fd47ce98c432f7013cada28fc383bf79de1c11f6f0d74d23f0067fb80bac8a) |
| 2026-11-14 07:16 WIB | Moon Monkey (MMONK) | [buy](https://sepolia.basescan.org/tx/0x6567016c78504e9d7e56fb18ef44f4982e96b6676d737a6872d93cc1615654f4) | 0.01 | 0.01183 | +0.00183 | [sell](https://sepolia.basescan.org/tx/0xcd635bbb1011b3f712427e85ad32fa33eebaccb05d04481281ec9c636e039f9c) |
| 2026-11-14 07:20 WIB | Base (BASE) | [buy](https://sepolia.basescan.org/tx/0x031a2c5aaecabfb3ac7fbb06c14f354f4a19e0c9da832c2302a0d35220faddf1) | 0.01 | 0.01592 | +0.00592 | [sell](https://sepolia.basescan.org/tx/0x9bc2e4a2447d90ba0ff87e2b8f2b1ef0912ea7a6a687d467dfbc4804dfdfef7f) |
| 2026-11-14 07:26 WIB | Pepe Moon (PMOON) | [buy](https://sepolia.basescan.org/tx/0x5ca200c9c02bed692435132958e71c9a7f30f92deceeef7c708dbf9c61e4d1dd) | 0.01 | 0.07865 | +0.06865 | [sell](https://sepolia.basescan.org/tx/0x868b8ef7ba9b25e715d075e774d8a279c5e9232e1a10b27e16b84c7b26e00943) |
| 2026-11-14 07:48 WIB | Stargate T0 (SGT0) | [buy](https://sepolia.basescan.org/tx/0x812644229e292485e51df0e1b9c3e9f079be13d67c78726e9c4207aafd9cb066) | 0.01 | 0.03675 | +0.02675 | [sell](https://sepolia.basescan.org/tx/0x5b87b601678a24e5bf6484d92b66174d3c37425adfbf77c675d9848181bb110e) |
| 2026-11-14 07:55 WIB | Natrium (NATRIUM) | [buy](https://sepolia.basescan.org/tx/0x8cefb19ae5ed2b247dc6555a2611181d2c2293b90598b62bd166b102243ea62b) | 0.01 | 0.01763 | +0.00763 | [sell](https://sepolia.basescan.org/tx/0x88f8a3bc53195215325cb716535292d73da078b00ba43bdd17172468db960ad7) |
| 2026-11-14 07:55 WIB | TDOGE (TDOG) | [buy](https://sepolia.basescan.org/tx/0xaa57cff950e9f692e7ce0c735f3c76fa5f52cfab59f67340176236c016305433) | 0.01* | 0.14543 | see note | [sell](https://sepolia.basescan.org/tx/0xfecec451949f4110000a75c7f80ca38e8645875f2efb3e7a56285437efb87d74) |
| 2026-11-14 13:38 WIB | Layer T0 (LAYT0) | [buy](https://sepolia.basescan.org/tx/0xac714fa49dd53110bbcf4d4e5c1a92aa9934d8ebbf74dce028ce8d29b7570c30) | 0.01 | 0.00121 | −0.00879 | [sell](https://sepolia.basescan.org/tx/0xb7d40b608eaa4613ee4f15ae2806fc109fd81a1b0b903c255e52d5fe257df66b) |
| 2026-11-14 13:54 WIB | TPEPE INU (TPEPE) | [buy](https://sepolia.basescan.org/tx/0xc4331eba3b3e3d471fedd9886ebe9eaf47de10eca163a71affcdc5f8c251ca19) | 0.01 | 0.00927 | −0.00073 | [sell](https://sepolia.basescan.org/tx/0xcb9eb4d4dcab78e647b4710fdbf559249f3b1f17405d8ac3e9bdc971ea5b6978) |
| 2026-11-14 15:38 WIB | Zero Gateway (ZGTW) | [buy](https://sepolia.basescan.org/tx/0x2730fcc4865c816ee061a35fefd7d11cd5f353b5c914e8a3d45a7031ecc75397) | 0.01 | 0.05883 | +0.04883 | [sell](https://sepolia.basescan.org/tx/0xe92fe0d2d21bf02f066c9219b2a6d773dc72dba9da7672470f7235559fcc4405) |
| 2026-11-14 16:47 WIB | TBONK (TBONK) | [buy](https://sepolia.basescan.org/tx/0xb7dd25a2fbe96127426d649129910d80ee53a2cfe11b64fd0cf7075bd9ac47e3) | 0.01 | 0.04002 | +0.03002 | [sell](https://sepolia.basescan.org/tx/0x93c861116e46434e90809ebf4db71e03565238815ad645659bca6a08212c2ff9) |
| 2026-11-14 18:02 WIB | Doge (DOGE) | [buy](https://sepolia.basescan.org/tx/0xc5a34052043c7574b48cc0ba41899afee036912519ae1948d16c129fc68e9213) | 0.01 | 0.02313 | +0.01313 | [sell](https://sepolia.basescan.org/tx/0x828eed9ed3421ed9e656495aa06c088c99bc24cffcbf63b14e3abdde4e36cc51) |
| 2026-11-14 20:14 WIB | T0 Beacon (BCN0) | [buy](https://sepolia.basescan.org/tx/0xa2fa934b208158359241046848bf4eb4fcc9dc8804751179f5eca5d933df20b0) | 0.01 | 0.00863 | −0.00137 | [sell](https://sepolia.basescan.org/tx/0x01e1f2f408c44f044e532985f21c77c6709d17913ee8ee42ced9d4ed09c8870d) |

\* **TDOGE note:** the row shows the 0.01 ETH auto-buy leg only. The owner
manually added a 0.16 ETH fill to push that mission to liftoff, so the
whole-trade cost basis was 0.17 ETH against a 0.14543 ETH sell — a **net
−0.0246 ETH loss**, counted as one of the 4 losses above. The agent's own
0.01 ETH leg is not a win on its own; we report the whole trade honestly.

Losses are real and shown: LAYT0 −0.0088 (dumped post-graduation), TPEPE
−0.0007, BCN0 −0.0014, TDOGE −0.0246 (manual fill). The agent does not
always win — it wins often enough, and it never loses more than its fixed
stake plus a manual fill the owner chose.

## Verifiability

Every claim above is checkable without trusting us:

- Buyer wallet on Basescan (Base Sepolia):
  `0xc9F62f232B857cC136D81e47681A866E610D9a6C`
- Each buy/sell tx links to `sepolia.basescan.org` in the table.
- Each mission page: `https://testnet.tzer0.fun/m/<token-address>`.
- Launchpad contract (Sourcify `exact_match`):
  `0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c`.
- The `state.json` this agent writes is its own audit log (buy tx,
  liftoff time, sell tx per position).

**Honest note on zkVerify:** this agent does **not** use zkVerify proofs
today — Wildcard does not require them, and we would rather say so than
fake it. What it does offer is a different kind of verifiability: every
decision and every trade is on-chain and independently checkable. A natural
next step is a zkVerify proof that a sell only executed after both gates
(liftoff + 60-min hold) passed — the two timestamps and the graduation
state are all public inputs, so the claim is provable.

## Run it yourself

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in TZERO_PRIVATE_KEY (testnet wallet only!)
cp whitelist.example.json whitelist.json   # your trusted creators
python3 watch.py --seed     # mark current missions as seen (no buys)
python3 watch.py            # one cycle: buy new missions, sell matured ones
```

Schedule `watch.py` every 2 minutes (cron). It is idempotent: a mission is
bought at most once, and a position is sold at most once.

Manual tools:

```bash
python3 buy.py <token> [--eth 0.01] [--dry-run]
python3 retry_buy.py <token> [--eth 0.01] [--dry-run]   # live on-chain quote
python3 sell.py <token> [--amount <tokens>] [--dry-run]
```

## Demo endpoint

A read-only demo of the decision logic is live at
**https://modxy-2a04-4e41-4-6a89--3f7a-ea89.run.pinggy-free.link** — `GET /`
for agent info, `POST /agent` with
`{"type", "task_id", "input": {...mission fields...}}` returns
`BUY` / `HOLD` / `SKIP` with reasons, `GET /trades` returns the verified
trade history. It holds no keys, signs nothing, and cannot trade. See
`../endpoint/server.py`.

> Note: the public URL is served through a free pinggy tunnel, which
> expires after ~60 minutes and gets a new random URL on each reconnect.
> `../endpoint/run-tunnel.sh` keeps it alive and always writes the current
> URL to `../endpoint/public-url.txt` — check there for the freshest URL.
> For a permanent URL, deploy `server.py` to any HTTPS host (Vercel,
> Railway, Render, Fly.io) or a named Cloudflare Tunnel.

## License

MIT. Testnet only — use at your own risk, never with mainnet funds.
