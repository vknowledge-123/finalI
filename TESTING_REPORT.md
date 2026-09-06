# Integration Test Report

Date: 2026-09-06

Final result: **165 tests passed**, 9 deprecation warnings, in 18.74 seconds.
Python compilation passed. No live orders were placed.

## Checks

- Compiled all application and test Python files.
- Scanned all 46 application Python files with Pyflakes. Fixed the undefined
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

These checks do not prove that every feature is bug-free. Real-browser discovery
returned no available browser, so desktop/mobile layout, clipboard behavior and
visual interaction were not verified in a browser. DOM doubles do not replace
those checks.

Redis/systemd/nginx and the five deployed server processes were not exercised
together. Broker transport, order states and storage are simulated in the tests;
live fills, production credentials, exchange behavior and cross-process contention
were not verified. Redis Lua lock operations require validation on the deployed
Redis environment. Lock renewal cannot retract a broker request already sent or
guarantee safety during process stalls/network partitions; reconciliation remains
necessary after uncertain outcomes.

The local test server was stopped after testing. Changes have not been pushed or
deployed to the client's VM.

## Reproduce

PowerShell, with the project's dependencies and Node.js installed:

```powershell
$env:APP_TESTING = '1'
py -m pytest tests -q -W error::pytest.PytestUnhandledThreadExceptionWarning
py -m compileall -q app tests
```

Existing FastAPI lifecycle and Dhan datetime deprecation warnings are separate
from test failures.
