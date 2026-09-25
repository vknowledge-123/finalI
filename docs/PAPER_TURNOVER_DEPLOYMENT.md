# Paper trading and NSE turnover ranking

## Alert settings

`Paper Trading` is off by default. With it on, a qualifying alert creates a
simulated full fill at the observed LTP. The position is persisted with
`paper_trading=true` and a `PAPER-...` order ID. No place/modify/cancel order API
is called for its entry, pyramid additions, partial profits or exit.

Paper positions use the existing tick-driven target, SL, TSL, cost-SL, alert-exit,
MIS auto-square-off and manual-exit logic. CNC paper positions survive daily
cleanup and restart without being compared to demat holdings. Broker order
updates and reconciliation cannot change their quantities. Changing the alert
toggle later does not convert an existing position between paper and live.

The existing one-open-position-per-symbol guard is retained across modes and
strategies. Paper and live positions in the same stock cannot coexist in this
version. A second alert for that symbol is skipped as `ALREADY_OPEN`.

The dashboard labels paper positions, including closed snapshots, and separates
active-position paper P&L from live P&L. Telegram entry notices also identify
paper trades. Existing account-level daily P&L limits and Telegram account MTM
remain based on the real broker account, not simulated P&L. The account kill
switch and Square Off All remain account-wide controls; paper mode is an alert
setting, not a global sandbox switch for those account actions.

Paper fills are a simple LTP simulation: no queue priority, slippage, brokerage,
tax, margin rejection or exchange liquidity model. Simulated profitability is
not evidence of live fill quality. Market-data credentials and a healthy data
source are still required. Paper mode does not bypass entry/sector/breakout,
time-window, freshness or trade-limit checks.

## Turnover definition and universe

Enable `Turnover Filter (Non-F&O)` and enter `Top N by Turnover`, e.g. 10.
Both LONG and SHORT candidates must be in the highest-turnover N stocks.
It is not a price-gainer filter, and SHORT does not reverse the turnover order.

The requested formula is:

```text
turnover proxy = cumulative shares traded today * current LTP
Example: 50,000 shares * INR 120 = INR 6,000,000
```

This is not the actual sum of every trade's price x quantity. A price change
revalues the whole day's volume. Cumulative volume replaces the previous value;
it is never added to it, and it is not last-trade quantity or candle volume.

The worker downloads Dhan's official compact CSV once per IST day, filters NSE
cash equity, exchange instrument type `ES`, and stock series EQ/BE/BZ/SM/ST.
This includes SME stocks, but not indices, bonds, ETFs, funds or derivatives.
Filtering only `INSTRUMENT=EQUITY` is insufficient: the real master also puts
bonds and ETFs in that broad category. Symbol/security-ID ambiguities stop
universe creation instead of silently choosing an instrument.

The 210 user-supplied F&O exclusions are in:
`app/data/turnover_fno_exclusions.txt`.
This is a maintained exclusion list, not an automatic live NSE F&O membership
feed. Review it when NSE adds/removes derivatives stocks, then restart turnover.
The public-master check during development produced 2,945 eligible stocks;
that count changes with the master and exclusions.

## Worker architecture

```text
Official instrument CSV -> stock-only universe minus exclusions
                                  |
Dedicated Dhan Quote websocket -> in-memory volume/LTP book
                                  |
             atomic Redis ranking snapshot every 2 seconds
                                  |
Chartink candidate -> turnover gate -> other filters -> paper OR live execution

Existing stock/sector market-feed service -> position monitoring and exits
```

`app.turnover_service` is a separate read-only process. Its quote connection
does not create a second order-update websocket. Dhan's SDK batches subscription
messages in groups of 100; the service refuses a universe above 5,000 rather than
silently truncating it. With the existing feed, this normally uses two of Dhan's
five available market-feed connections per user. Other external clients also
consume that broker limit.

Quiet symbols are warmed/revalidated with REST quote batches of up to 1,000,
roughly every 45 seconds. The API, trading and turnover workers share a Redis
REST market-data budget. Ranking requests yield to foreground order/exit quote
requests and back off on HTTP 429. Websocket tick delivery is not rate limited by
this REST budget. Existing calls made outside this deployment are not coordinated.

An atomic Redis lease prevents duplicate turnover workers from publishing or
starting feeds. Saved credential changes are picked up on the next configuration
poll, normally within 10 seconds. Disconnect, token change, day rollover, config
disable and process failure invalidate ranking availability. A process crash
leaves at most a short lease/snapshot TTL, not a permanent stale rank.

The ranking requires coverage for the entire eligible universe, not just alert
stocks. Every quote must be at most 120 seconds old. Nonzero volume must have a
same-day exchange trade timestamp. Zero-volume REST quotes may establish quiet
stocks with turnover zero, but those cannot qualify for entry. Out-of-order
updates and decreasing same-day cumulative volumes are rejected.

Snapshots expire in Redis after 15 seconds; the entry gate requires age <=10
seconds and today's IST date/session. It checks again immediately before
submission. Missing/stale/incomplete/disconnected data skips entries; it never
silently disables the filter. If even one eligible stock has no valid quote,
coverage remains incomplete: check the ranking status instead of loosening
safety automatically. Individual rank ordering is as-of the last received
quotes, not a simultaneous exchange-wide snapshot.

Turnover filtering currently requires Dhan as the selected data broker. With
Zerodha selected it returns `TURNOVER_REQUIRES_DHAN`. Paper execution itself
works with either broker's existing market-data path.

Regular session hours and `DHAN_MARKET_HOLIDAYS`/`DHAN_MARKET_EXTRA_SESSIONS`
follow the existing feed calendar policy. Keep those holiday settings current.

## Deploy on an existing VM

Commit/push the changes from the development machine first. On the VM, perform
the deployment outside trading hours; a restart temporarily interrupts
application-managed exits. Confirm any live exposure directly with the broker.
These paths assume the existing `/opt/finalI`, `ashuchart` user and
`/etc/ashuchart.env` deployment. Do not copy another client's credentials.

```bash
cd /opt/finalI
sudo -u ashuchart git pull --ff-only
sudo -u ashuchart /opt/finalI/myvenv/bin/python -m compileall -q app
sudo install -m 644 deploy/ashuchart-turnover.service /etc/systemd/system/ashuchart-turnover.service
sudo systemctl daemon-reload
sudo systemctl enable ashuchart-turnover.service
sudo /usr/local/bin/restart-ashuchart.sh
sudo systemctl start ashuchart-turnover.service
sudo systemctl status ashuchart-turnover.service --no-pager -l
```

No new runtime dependency or mandatory `.env` variable is needed. The unit uses
the same environment file as the other services. `PartOf` and its enabled
dependency on `ashuchart-market-feed.service` make it restart when the existing
market-feed service restarts, including the existing daily restart. It is also
enabled at VM boot. If your service names/user/paths differ, edit the new unit
before installing it. The supplied unit is for the Ubuntu systemd deployment.

Local development (with a separate local Redis and test credentials):

```bash
py -m app.turnover_service
```

Authenticate with Dhan, open an alert, enable Paper Trading and Turnover Filter,
set Top N, and save. The worker subscribes only while an enabled configuration
requires turnover. Use `Refresh Turnover Ranking` in configuration to inspect
coverage and ranks. No ranking is marked ready outside the market session.

## Check status without exposing credentials

```bash
sudo journalctl -u ashuchart-turnover --since "15 minutes ago" --no-pager
sudo journalctl -u ashuchart-turnover -f
systemctl show ashuchart-turnover -p PartOf -p ActiveState --no-pager
```

Press Ctrl+C to stop following logs. Expected log events include
`TURNOVER_UNIVERSE_READY`, `TURNOVER_SUBSCRIBED`, `TURNOVER_CONNECTION` and
`TURNOVER_RANK_STATUS`. Subscription sent is not proof of complete quote coverage.

While logged into the dashboard, the authenticated endpoint
`/api/turnover/top?user_id=1&limit=10` returns `ready`, `reason`, `covered`,
`total`, timestamp and rows containing rank, symbol, security ID, LTP, cumulative
volume and turnover. A plain terminal curl without the admin cookie returns
`ADMIN_AUTH_REQUIRED` as expected. The Redis key is `turnover:snapshot:1`.
Never paste environment files, broker access tokens or Redis passwords in logs.

Common skip reasons: `TURNOVER_RANK_STALE`, `TURNOVER_RANK_NOT_READY`,
`TURNOVER_FEED_DISCONNECTED`, `TURNOVER_FNO_EXCLUDED`,
`TURNOVER_SYMBOL_NOT_IN_UNIVERSE`, `TURNOVER_FILTER`, `TURNOVER_SESSION_CLOSED`.

## References and verification scope

- [Dhan live-feed protocol](https://dhanhq.co/docs/v2/live-market-feed/)
- [Dhan REST quote fields and limits](https://dhanhq.co/docs/v2/market-quote/)
- [Dhan instrument schemas](https://dhanhq.co/docs/v2/instruments/)

Regression tests use simulated quotes, Redis fixtures and mocked broker calls.
Browser tests use a local in-memory server, including mobile layout, config
round-trips, paper labels, live tick/P&L rendering and closed positions. The
public instrument CSV was fetched without credentials to verify the universe
filter. No client VM was modified and no live broker orders were submitted.
Authenticated live-feed coverage, market-data entitlement and systemd startup
must still be verified on the deployed VM; local tests cannot establish those.
