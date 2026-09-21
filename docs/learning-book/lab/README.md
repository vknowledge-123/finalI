# Paper Lab

This is a deliberately small local teaching application. It contains no broker SDK, no live orders, no production secrets, and no authentication. Never expose it publicly. Prices and immediate fills are synthetic. Retry count is persisted to teach the zero-value regression; the simulator does not retry external orders. The dashboard has a small subset of settings; advanced risk fields can be exercised through API tests.

## Windows PowerShell

From this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest -q tests/test_domain.py tests/test_api.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_browser.py
.\.venv\Scripts\python.exe -m uvicorn paperlab.api:default_app --factory --host 127.0.0.1 --port 8015
```

Use `py -3.13` instead if that is your installed compatible interpreter. Python 3.12+ is required. Open http://127.0.0.1:8015 for this optional paper demo. If the port is occupied, choose another free port. Ctrl+C stops the demo.

## Ubuntu or Git Bash

On Ubuntu, create the environment with `python3 -m venv .venv`. Use `.venv/bin/python` for the same commands. In Git Bash on Windows, the executable remains `.venv/Scripts/python.exe`, not `.venv/bin/python`.

## Send a synthetic signal

After saving strategy `Practice`, in a separate Python session with HTTPX installed:

```python
import httpx

response = httpx.post('http://127.0.0.1:8015/api/signals', json={
    'event_id': 'lesson-1', 'strategy': 'Practice',
    'symbol': 'DEMO', 'price': '100'
})
print(response.status_code, response.json())
response = httpx.post('http://127.0.0.1:8015/api/ticks', json={
    'symbol': 'DEMO', 'price': '103'
})
print(response.status_code, response.json())
```

Refresh positions. The simulator should show a closed target exit with gross P&L 3 for quantity one. Sending the same signal ID again will not reopen it. A new event ID is a new logical signal. No slippage, fees, delayed fills, intrabar ambiguity, stale tick policy, or broker execution are simulated here; add them as workbook assignments.

## Testing scope

Domain tests check money and risk invariants. API tests use temporary SQLite files and verify persistence and duplicate handling. Browser tests start a temporary local server, operate the form, call the API, and verify table updates at desktop/mobile sizes. Browser tests use Playwright directly from Python and do not require its pytest plugin. The book explains the plugin as the next step for a larger suite.

The browser suite uses installed Google Chrome when available; otherwise it uses Playwright Chromium. Install Chromium with the command above if neither is available. It shuts down its server after the session.

This lab is not a replacement for the production app or its tests. It intentionally has no Redis, broker networking, email delivery, TOTP, or live WebSocket. Those features need the separate implementation and failure exercises in the book.
