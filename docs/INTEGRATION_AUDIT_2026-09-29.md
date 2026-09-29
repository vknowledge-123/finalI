# Integration Audit: 2026-09-29

## Results

- Baseline: 396 tests passed.
- Final: 409 tests passed, 29 deprecation warnings, 32.28 seconds.
- Added 13 regression tests in `tests/test_audit_regressions.py`.
- Playwright/Chrome smoke checks passed at 1440x1000, 768x1024 and 390x844.
  No browser page errors; desktop and mobile screenshots were inspected.
- Python compilation of `app` and `tests` passed.
- Pyflakes scanned 116 tracked Python files: no undefined names or syntax errors.
  Unused imports/variables, redundant f-string prefixes and a duplicate helper
  remain. This is not a clean-lint claim.
- Bash syntax checks passed separately for the daily restart installer, stack
  restart, admin reset and Let's Encrypt setup scripts.
- `git diff --check` passed.

## Bugs Fixed

1. Normal live exits retained pre-exit tick P&L. They now calculate realized P&L
   from execution fill prices and add previously realized partial-exit profit.
2. Partial exits now immediately include unrealized P&L for the remaining shares,
   without counting the exited shares twice. Custom final partial-profit exits
   also finalize P&L without needing another tick.
3. Reconciliation now acquires the shared exit lock and checks the execution
   snapshot again after the broker request. It defers when execution state changes
   and does not attempt to reopen a newer closed snapshot.
4. Excess account-level broker quantity is reported as `BROKER_QTY_EXCESS` rather
   than silently adopted into an app-owned trade. It may belong to manual orders.
5. Delayed entry callbacks no longer replace confirmed/pyramided average prices
   or disable monitoring of a confirmed partial entry that was later cancelled.
6. Delayed exit callbacks no longer close remaining shares after an earlier
   partial exit. Interrupted exit recovery is locked, requires positive confirmed
   terminal fills and valid prices, and updates realized P&L.
7. Pyramiding uses the shared exit lock and rejects stale execution snapshots,
   preventing concurrent services from independently adding the same pyramid leg.
8. Partial-profit booking rereads saved quantity, realized profit and booked-target
   state under the lock. Final exits also preserve profit booked by another worker.
9. Pytest isolated runs now enable the same offline test mode as the complete
   suite, instead of depending on collection of `test_integration.py` first.

## Coverage

The suite includes broker request/response contracts, binary tick decoding,
subscription recovery, queue/lock behavior, paper trading, partial fills,
reconciliation, CNC restoration, risk exits, sector/turnover ranking, breakout
monitoring, Telegram formatting, strategy configuration, authentication/TOTP,
frontend rendering, API errors and separate strategy/account/paper P&L.

Browser checks exercise local API save/reload, create/edit strategy navigation,
paper/turnover controls, repeated alert rows, tick-driven P&L, trailing-stop
display, closed positions, breakout expiry and account-MTM freshness states.
Broker responses and MTM data are simulated; real browser rendering is used.

## Documentation Check

Installed SDKs inspected: DhanHQ 2.2.0 and KiteConnect 5.0.1. Contract tests use
those SDKs with intercepted transports, not live order endpoints. The Kite
compatibility path still sends market protection through authenticated REST
transport because 5.0.1 lacks that public method argument.

Official references checked:

- [Dhan orders](https://dhanhq.co/docs/v2/orders/)
- [Dhan positions and realized/unrealized profit](https://dhanhq.co/docs/v2/portfolio/)
- [Dhan feed protocol](https://dhanhq.co/docs/v2/live-market-feed/)
- [Dhan Python SDK](https://github.com/dhan-oss/DhanHQ-py)
- [Kite orders and fills](https://kite.trade/docs/connect/v3/orders/)
- [Kite WebSocket protocol](https://kite.trade/docs/connect/v3/websocket/)
- [KiteConnect 5.0.1 source](https://github.com/zerodha/pykiteconnect/blob/v5.0.1/kiteconnect/connect.py)

## Limits

No live orders, external Telegram messages or production deployment were made.
In-memory storage and fakeredis cover persistence contracts; this is not a
production Redis outage/latency test. No sustained exchange-scale load benchmark,
penetration test or live broker-connected trading session was performed.

The results do not certify every branch, timing interleaving or future broker API
change. Broker/manual exits and mixed manual/app ownership still require separate
end-to-end reconciliation validation; account MTM is not interchangeable with
strategy-only P&L. Remaining deprecations are FastAPI lifecycle hooks, Redis close
and an upstream Dhan datetime helper, not failing tests.
