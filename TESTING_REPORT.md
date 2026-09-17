# Integration Test Report

Date: 2026-09-17

Latest complete suite: **256 tests passed**, no failures or skips, 12 deprecation
warnings (21.57 seconds). Python compilation passed. Shell scripts are unchanged
since the previous successful syntax checks.
No live orders were placed. The production VMs were not changed.

## Breakout Monitoring TTL

Added a persisted 1-60 minute TTL (default 1), fixed previous-candle threshold,
non-blocking watches and dashboard waiting/expiry states. TTL includes initial
preparation time; Entry End Time remains an earlier cutoff. Repeated alerts do
not extend an active watch. Split-mode execution and single-process API watches
have separate ownership. Waiting watches resume and resubscribe after restart;
uncertain submitted orders are not replayed and require reconciliation.

Added 21 regression tests covering LONG/SHORT crossing, stale/missing prices,
expiry, slow validation past the deadline, unchanged candle reference, price
falling back, duplicate alerts/orders, trade limits, position guards, kill switch,
config changes, disabled toggle, entry window, restart recovery, owner isolation,
subscription recovery, lost leases, order failures, concurrent checks, slow-order
isolation and Redis CAS/history projection. Redis is simulated with fakeredis.
Browser tests verify the real configuration API round trip, TTL toggle/default,
mobile layout and waiting-to-expired refresh without manual clicks. Broker calls
remain mocked. Newly added watch modules pass Pyflakes; no production latency or
live broker execution guarantee is implied.

## Telegram and Breakout Features

Added 16 tests for optional feature settings, encrypted token persistence and
API redaction, strict previous-bar selection, LONG/SHORT boundaries and buffers,
toggle bypass, missing broker candles, actual partial-fill notification quantities,
daily welcome and five-minute P&L slots, shared-group deduplication/restarts,
disabled/custom strategy suppression, delivery failure backoff, Telegram 429,
credential-log redaction, Dhan realized/unrealized aggregation and Redis event
TTL/claim isolation. Telegram HTTP transport and broker calls were mocked.
The final pass also corrects the one-minute candle request for Zerodha to its
documented `minute` interval and verifies closed-bar filtering for both aliases.

Chrome tests at 1440x1000, 768x1024 and 390x844 cover enabling the new controls,
real local API save/GET/reload, masked saved tokens, zero settings and disabling
dependent fields. A test-harness modal-opening error was corrected. Screenshot
inspection also found a mobile sticky-header overlap, fixed with scroll padding
and a header-owned close button. Existing tick/P&L and closed-position UI checks
remain in the browser harness. No real Telegram messages were sent.

Operational behavior and limitations: [Telegram and breakout guide](TELEGRAM_AND_BREAKOUT.md).

## Strategy Configuration Follow-up

- SHORT sector selection now uses ascending percentage change (biggest losers);
  LONG still uses descending change. Custom BOTH strategies use their confirmed
  BUY/SELL side. Dashboard ranking order remains unchanged.
- Daily P&L breaches request square-off for known MIS and CNC positions, including
  tracked carry positions restored into the normal position store. Manual/all
  square-off can hydrate a managed CNC position from Redis before attempting an
  exit, even when it is absent from the broker's day positions.
- The local kill flag is set before requesting square-off. Pyramiding checks it
  before adding exposure. Automatic time-based intraday square-off remains MIS-only.
- Numeric zero retries and zero percentage buffer survive API validation,
  canonical/legacy field normalization, storage, engine settings and browser
  save/reload. Fractional retry counts and zero confirmation timeouts are rejected.
- Chrome smoke tests passed at 1440x1000, 768x1024 and 390x844, including real
  local API save/GET/reload of zero settings, WebSocket updates and closed rows.

Nine new regression tests cover these changes, including profit/loss breaches,
disabled/zero/unbreached limits, kill state on square-off failure, restored CNC
positions, pyramid blocking and side-dependent Top N selection.

Scope: the trigger uses broker positions MTM, not total demat portfolio valuation.
Untracked demat holdings are not imported and liquidated. Square-off remains a
best-effort broker operation, not a guarantee of fills. Broker calls in tests are
mocked; no live exchange execution or production deployment was tested.

The supplied [Dhan Trader's Control documentation](https://dhanhq.co/docs/v2/traders-control/)
was reviewed. This patch does not integrate or enable Dhan's account-wide
`/killswitch` or `/pnlExit` endpoints. The broker kill switch requires closed
positions and no pending orders; it is not a replacement for the app's local
entry block during liquidation. Broker-side P&L rules can trigger immediately
when configured and reset at the end of the trading session.

## Redis Startup Diagnostics Follow-up

The reported production startup error hid the original Redis failure and included
the password-bearing connection URL. API startup/lazy initialization, service
bootstrap and the daily cleanup command now use a five-second connection check
with credential-free error codes for authentication, permissions, loading,
connection failures and timeouts. Failure still prevents startup; there is no
production in-memory fallback. Three regressions cover error classification and
traceback redaction, success/false PING, and cancellation propagation. The complete
suite passed in 22.10 seconds. The actual VM Redis failure remains undiagnosed until
its service status and configured-credential connection check are available.

## Resumed Integration Pass

- Fixed dashboard tick handling so valid live prices update the stored browser
  snapshot, row P&L and aggregate open-position P&L. Previously the row could show
  a new LTP alongside stale P&L and restore an old price on rendering.
- Removed the tick handler's independent continuous trailing-stop calculation.
  It now displays the backend stop rather than silently overriding step-wise or
  custom strategy stops. Invalid/nonpositive tick prices are ignored.
- Fixed clipped navigation text by allowing the header to wrap and take its
  natural height. Removed malformed HTML inside its CSS. Backtest fields now
  wrap into fewer columns instead of clipping the date controls.
- Completed the previously unfinished browser harness: use valid Cost SL inputs,
  check the actual API save-response schema, and open the mobile menu before
  clicking Configure Alert. These were test-fixture failures, not broker failures.
- Isolated production entry-guard tests from live instrument-master downloads.
- Added a Redis Lua regression confirming that a stale update cannot reopen the
  same CLOSED trade, a new trade ID can open, and JSON arrays/objects retain types.

Real headless Chrome / Playwright checks passed at 1440x1000, 768x1024 and 390x844:
configuration save/GET/reload, CNC/fixed quantity, Cost SL, TSL and alert-exit toggle
round trips, real local WebSocket tick delivery, LTP/P&L display, preservation of
the backend trailing stop, and CLOSED position display without a square-off button.
No browser page errors were reported. Screenshots were inspected; header bounds
and document width are also asserted. The test server uses in-memory storage and
synthetic positions, disables admin gating only for this UI harness, and shuts
down after the run. Admin password/TOTP gating has separate API integration tests.

Other passing suites cover Dhan packet parsing/subscription/recovery, cancelled
jobs and dispatch guards, order/partial-fill/cancellation handling, reconciliation,
sector cache, alert filtering, risk/exit behavior, custom strategies, backtests,
and API/frontend contracts. This is coverage of the named test cases, not an
exhaustive test of every possible feature combination.

The Dhan feed contracts were compared with the official
[live market feed documentation](https://dhanhq.co/docs/v2/live-market-feed/)
and the pinned [Python SDK v2.2.0 source](https://github.com/dhan-oss/DhanHQ-py/tree/v2.2.0).
These document segment/security-ID routing, binary packet layouts, subscription
batching and v2 disconnect. Broker transport is simulated in the contract tests.

Optional browser test (Chrome installed, run from the repository root):

```bash
python -m pip install playwright==1.62.0
PYTHONPATH=. python tests/browser_smoke.py
```

Pyflakes scanned application and test Python files: no undefined names reported.
Existing unused imports/locals and a duplicate helper remain. The 10 pytest
warnings are FastAPI lifecycle and Dhan SDK datetime deprecations, not test errors.

## Previously Implemented Reliability Fixes

The sections below document fixes retained from the earlier passes and exercised
again by the current suite; they are not all new changes from this resumed run.

## Execution And Subscription Reliability Follow-up

- Dhan instruments are keyed by exchange segment AND security ID throughout
  subscriptions, tick routing, previous-close storage and tick-size lookup.
  Regression tests keep ADANIENT (NSE_EQ, 25) separate from NIFTY BANK (IDX_I, 25).
  Ambiguous legacy ID-only lookups cannot silently choose the index.
- Equity-master filtering/parsing runs off the event loop. A successful refresh
  replaces obsolete equity mappings while preserving registered other segments.
  Entry preparation loads the master before acquiring the order lock. Custom
  strategy computation also runs off the event loop.
- Entry lock-acquisition failures now reject entry instead of proceeding unlocked.
  Active task-owned leases are checked again before broker order submission.
- Each execution job has its own task. Lock-loss cancellation records an explicit
  error and sets kill without cancelling the entire worker. Worker shutdown still
  propagates cancellation and leaves the claimed job for recovery.
- Execution uses atomic FIFO BLMOVE into a processing list and acknowledges only
  after the result is saved. A single renewable worker lease protects recovery.
  Interrupted jobs are NOT replayed: recovery sets kill and records
  EXECUTION_INTERRUPTED_RECONCILE_REQUIRED. Review broker orders, trades and
  positions before manually resuming. A request already sent cannot be retracted
  by cancelling a Python task.
- Jobs older than MAX_ALERT_AGE_SEC (default 60 seconds), or with an invalid queue
  timestamp, are skipped as ALERT_EXPIRED_IN_QUEUE. Malformed jobs are quarantined.
  Intake supplies a stable timestamp and job ID. Older lost BLPOP jobs from before
  this update cannot be reconstructed automatically.
- Subscription retries have bounded exponential delays and at most five requeues;
  successfully sent symbols are not retried because a sibling is unresolved.
  Dhan login/startup restores sector indices and active positions, not the entire
  sector constituent universe. Alert stocks remain subscribed on demand.
- Redis alert-history upserts use WATCH/MULTI transactions instead of deleting
  and rebuilding a list from a potentially stale snapshot. Concurrent updates
  retain other alert rows and preserve JSON array types.
- Uvicorn access logs omit query strings, including webhook secrets and callback
  tokens. This does not redact nginx or historical logs. Previously exposed
  webhook secrets should be rotated and nginx logging reviewed separately.

Verification includes webhook authentication -> normalized queue job -> execution
-> same persisted dashboard row, queue recovery/expiry/quarantine, failed result
persistence, Redis transaction concurrency, wrong-owner Lua lock operations,
stock/index collisions, off-loop parsing and bounded subscription retries.
Redis tests used fakeredis 2.38.0 with Lua support and the pinned redis-py 5.0.8.
WSL could not start because virtualization is unavailable, so no real Redis server
or Linux process-failure test was run here. FastAPI/frontend Node DOM contract
tests and the real-browser checks described above passed.

Test dependencies are in requirements-test.txt (not needed on the production VM).
Run in test mode with:

```bash
python -m pip install -r requirements-test.txt
APP_TESTING=1 python -m pytest tests -q -W error::pytest.PytestUnhandledThreadExceptionWarning
python -m compileall -q app tests
```

The queue command requires Redis 6.2 or later; see the official
[BLMOVE documentation](https://redis.io/docs/latest/commands/blmove/).
Segment-aware routing follows the header described in the
[Dhan live market feed documentation](https://dhanhq.co/docs/v2/live-market-feed/).
Redis persistence/backups remain necessary: retaining an in-flight list protects
against a worker crash, not loss of the Redis database. These changes have not been
pushed or deployed. This is a targeted reliability pass, not certification that
every feature is bug-free or that orders execute instantly.

## Sunday Feed Recovery Follow-up

- Replaced the SDK's order-login printing path with the documented SELF JSON
  handshake. No credentials, raw response bodies or authenticated URLs are logged
  by the new feed error/retry handlers.
- Market-feed reconnects now have one owner. The external watchdog and symbol
  confirmation cannot bypass a running receiver's cooldown or authentication block.
- Added exponential backoff with jitter, a minimum 60-second HTTP 429 cooldown,
  Retry-After handling, and a minimum 300-second retry delay outside the configured
  equity connection window. Broker rate limits may still occur for external reasons.
- Locally expired JWT metadata prevents socket startup. HTTP authentication rejection
  and SDK disconnect codes for invalid/expired credentials or missing entitlement
  block further market reconnects until credentials are replaced. Metadata checks
  are not signature verification; Dhan remains responsible for authentication.
- Idle market sockets are not restarted merely because no ticks arrived. Symbol
  tick confirmation is disabled outside weekday 09:15-15:30 IST. The connection
  retry window is weekday 08:00-16:00 IST. Optional comma-separated ISO dates in
  DHAN_MARKET_HOLIDAYS and DHAN_MARKET_EXTRA_SESSIONS adjust the trading days;
  exchange holidays/special session times are not automatically downloaded.
- Malformed order frames are rejected atomically, the socket is closed, and the
  receiver backs off. This prevents unsafe state updates; it cannot repair a bad
  broker response. Reconciliation remains the fallback for missing order events.
- Added capitalized documented order fields and preserved explicit zero remaining
  quantity. The callback is awaited instead of creating an unobserved future.
- v2 disconnect sends JSON only, without the SDK's additional legacy binary header.
- Added 14 recovery regressions including credential replacement, expired startup,
  rate limits, session closure, malformed frames, secret-free output and shutdown.

Protocol references: [Dhan order updates](https://dhanhq.co/docs/v2/order-update/)
and [Dhan live market feed](https://dhanhq.co/docs/v2/live-market-feed/).

## Checks

- Compiled all application and test Python files.
- Scanned all 47 application Python files with Pyflakes. Fixed the undefined
  `dhanhq` annotation. Unused imports/locals and an existing duplicate helper
  remain; the scan is not a clean lint certification.
- Ran the Python suite with test mode enabled and unhandled thread exceptions
  treated as test failures.
- Executed the shipped dashboard JavaScript with Node and lightweight DOM
  doubles. Checked HTML escaping, string LTP rendering, stale position removal,
  error responses, authentication redirects, and configuration field round trips.
- Configuration round trips use actual FastAPI responses and submit the resulting
  frontend payload back to FastAPI, including cost SL, TSL and alert-exit toggles.
- Tested Dhan SDK binary parsing, subscription batching/segments, failed sends,
  bounded delivery, receive timestamps and thread/event-loop cleanup with fake
  transport. No broker credentials or live orders were used.

## Bugs Fixed In This Pass

- Broker net/day position views were added together, potentially doubling quantity.
  Intraday reconciliation now also filters by product.
- Broker error responses could be interpreted as an empty portfolio.
- Reconciliation deleted a closed position's history and left an OPEN row available
  to REST fallback. It now retains a CLOSED snapshot and stops fallback for that row.
- Intraday reconciliation could delete CNC positions absent from today's positions
  book. CNC is excluded from that path; its existing holdings/carry path remains.
- A failed or merely acknowledged cancellation could lead to a replacement order.
  Replacement now requires a terminal order snapshot and accounts for fills during
  cancellation. An unresolved cancellation blocks the replacement and sets kill.
- Trade-book data could overwrite a terminal cancellation status with PARTIAL.
- Explicit zero fills could fall back to the requested quantity when updating an
  entry, exit or pyramid. Zero is now distinguished from an unspecified fill.
- Malformed subscription queue jobs could terminate the market-feed loop.
- Zerodha websocket ticks lacked an explicit source marker.
- Alert names, exit-alert names, errors and selected other external dashboard text
  were interpolated as HTML. Those paths are now escaped.
- String-valued alert prices could throw when rendering LTP.
- Position refresh retained removed positions in the browser snapshot cache.
- Short order-lock expiry could allow overlapping workers during a slow broker
  operation. Locks now use unique ownership tokens, conditional renewal/deletion,
  and task-lifetime cleanup. Renewal failure attempts to set kill and cancels the
  owning task to stop subsequent placements. Tests cover renewal, wrong-owner
  release, lost ownership, immediate release and shutdown using in-memory storage.

## Limits

These checks do not prove that every feature is bug-free. Browser coverage is
limited to the flows listed above. Clipboard permissions on public HTTP/HTTPS,
real broker login redirects, physical mobile devices and other browser engines
were not verified by the browser harness.

Redis/systemd/nginx and the five deployed server processes were not exercised
together. Broker transport, order states and storage are simulated in the tests;
live fills, production credentials, exchange behavior and cross-process contention
were not verified. Redis Lua lock operations require validation on the deployed
Redis environment. Lock renewal cannot retract a broker request already sent or
guarantee safety during process stalls/network partitions; reconciliation remains
necessary after uncertain outcomes.

The browser harness server was stopped after testing. A separate brokerless
preview is available at http://127.0.0.1:8010/dashboard. Changes have not been
pushed or deployed to the client's VM.

## Reproduce

PowerShell, with the project's dependencies and Node.js installed:

```powershell
$env:APP_TESTING = '1'
py -m pytest tests -q -W error::pytest.PytestUnhandledThreadExceptionWarning
py -m compileall -q app tests
```

Existing FastAPI lifecycle and Dhan datetime deprecation warnings are separate
from test failures.
