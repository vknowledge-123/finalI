# Safe practice lab

This is a small **paper-only reference implementation**, separate from the real
application. It intentionally omits authentication, persistence, trading hours,
fees, broker execution, WebSockets and distributed workers. Its routes are not
the production app's routes. Never deploy this server publicly.

Run from this directory in a NEW virtual environment:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m pytest -m "not browser" -q
.venv\Scripts\python -m pytest -m browser --browser chromium -q
.venv\Scripts\python -m uvicorn server:app --host 127.0.0.1 --port 8019
```

Open http://127.0.0.1:8019. The automatic browser tests start their own server
on a free loopback port; do not start Uvicorn first for those tests. If you use
installed Chrome instead of Playwright Chromium, add `--browser-channel chrome`.
On Linux/macOS use `python3 -m venv .venv` and `.venv/bin/python`.

The dependency ranges are lab compatibility ranges, NOT a deployment lockfile.
Record the successful environment with `python -m pip freeze > requirements.lock.txt`
in this disposable lab. Never install these over the trading app's environment.

Every test gets fresh application state. Every browser test gets a separate
server and browser context. Multiple processes would each have different memory;
the lab is intentionally single-process. Idempotency is scoped to this app
instance and is not a durable distributed guarantee.

Manual session: save strategy `demo`, quantity 2, target 1%, SL 0.5%. In a second
terminal (Git Bash), submit SYNTHETIC data only:

```bash
curl -X POST http://127.0.0.1:8019/api/signals -H 'Content-Type: application/json' -d '{"event_id":"manual-1","strategy":"demo","symbol":"TEST","price":"100"}'
curl -X POST http://127.0.0.1:8019/api/ticks/TEST -H 'Content-Type: application/json' -d '{"price":"101"}'
```

Refresh positions: CLOSED, TARGET, P&L 2.00. Repeating `manual-1` returns its
original response, not a new order or the latest position snapshot. Query
`/api/positions` for current state. Reset by stopping and restarting this lab;
there is no production reset route and no production database is touched.
