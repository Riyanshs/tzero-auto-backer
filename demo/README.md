# Demo endpoint — tzero liftoff-circle auto-backer

Read-only demo for the Agent Games by zkVerify Wildcard track. Holds no
keys, signs nothing, cannot trade. Serves the agent's decision logic
(`POST /agent` → BUY / SKIP with reasons) and the verified trade history
(`GET /trades`).

## Run locally

```bash
python3 server.py [port]   # default 8080, or $PORT when set
```

## Deploy (Railway)

The repo root has a `Dockerfile` (python:3.12-slim, stdlib only, no
dependencies). On Railway: New Project → Deploy from GitHub repo →
select this repo → the Dockerfile is auto-detected → Railway assigns a
public domain and `$PORT`. Verify with `GET /health`.
