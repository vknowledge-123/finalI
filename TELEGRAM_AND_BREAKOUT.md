# Telegram and Previous-Candle Breakout

Both features are optional and default to OFF in Strategy Configuration.

## Telegram Setup

1. Create a bot using Telegram's official BotFather and obtain its bot token.
2. Add the bot to your group and give it permission to send messages.
3. Obtain the group's chat ID (usually a negative number), or its public username.
4. In a Classic alert configuration, turn Telegram Messages ON and enter the
   bot token and chat ID. Save Configuration.
5. Keep the same valid `ENCRYPTION_KEY` available to all app services. Token
   storage fails closed without encryption. Use HTTPS when entering credentials.

The token is encrypted in the alert configuration and is never returned by the
configuration API. A blank token field on editing preserves the stored token;
enter a new token to replace it. Deleting the strategy deletes its configuration.

Messages:
- Daily at 09:05 IST: "Welcome dear traders!" A five-minute recovery window
  allows a delayed scheduler/restart to deliver the welcome; no late-day replay.
- Confirmed Classic entries: symbol, BUY/SELL, product, actual filled quantity,
  average entry, target and initial stop loss. Rejected/skipped alerts are not sent.
- Every five-minute clock slot, weekdays 09:15-15:30 IST: current Dhan account
  positions P&L, adding realized and unrealized P&L once per net position. This is
  account P&L, not this strategy's P&L or the value of all overnight demat holdings.

Turning the toggle OFF stops that strategy's messages and drops its queued entry
messages. If other Classic alerts still enable the same bot/group, that group
continues receiving shared welcome/P&L messages, once per scheduled slot.

The existing API service owns the background Telegram worker. Execution services
write entry events to Redis; no extra systemd service is required. The API VM must
be running at scheduled times. Scheduling is not exchange-holiday aware. Events
expire after ten minutes rather than posting old entries after a long outage.

Network failures do not fail orders. Delivery has a timeout, rate-limit backoff,
and Redis deduplication across worker restarts. Telegram does not provide an
idempotency key for sendMessage: an ambiguous network timeout can still cause a
duplicate on retry. This is best-effort notification, not an execution safeguard.
Logs: `sudo journalctl -u ashuchart-api --since "15 min ago" --no-pager | grep TELEGRAM`

## Previous-Candle Breakout

- Turn Previous Candle Breakout ON; choose 1 or 5 minutes.
- Optional Price Buffer has its own toggle. The amount is INR, not a percentage.
- LONG requires LTP strictly greater than previous high + enabled buffer.
- SHORT requires LTP strictly less than previous low - enabled buffer.
- At 10:30:03 IST, a 1-minute check uses 10:29-10:30; a 5-minute check uses
  10:25-10:30. The current forming candle is never used.
- Set Monitoring TTL to a whole number of minutes (1-60; default 1). If not yet
  broken, the alert shows `WAITING FOR BREAKOUT`. The previous candle and buffered
  level remain fixed; new candles do not move the threshold.
- TTL starts when the execution engine begins processing the alert, including
  candle/feed preparation. Example: processing at 10:30:03 with TTL 2 expires at
  10:32:03, not at the next candle close. Entry End Time can stop the watch earlier.
- Fresh shared quotes are checked approximately every 250 ms, with up to eight
  candidate checks concurrently. No broker order is held while waiting. On a
  strict cross, normal feed, direction, sector, duplicate-position, sizing,
  kill-switch and trade-limit checks run again before submission. A later price
  check can return the stock to waiting if price has fallen back. Fills are not
  guaranteed at the breakout level.
- No crossing before expiry produces `SKIPPED / BREAKOUT_TTL_EXPIRED`. No fresh
  quote means no entry. Disabling/changing the strategy or enabling the kill
  switch cancels its waiting watch. Repeated alerts do not extend an active TTL.
- Watches are stored in Redis and unexpired waiting watches resume after restart.
  An interrupted submission is NOT replayed: the local kill switch is set and
  the record requires broker reconciliation. The existing execution service owns
  split-deployment watches; the API runtime owns single-process watches. No new
  systemd service is needed. Completed watch records are retained for one day.
- The dashboard displays the threshold and expiry time, refreshing pending watch
  status every two seconds while the page is visible.
- Missing/invalid previous candles fail closed. Older candles, including the
  previous session at market open, are not substituted.
- Turning the breakout toggle OFF bypasses candle fetching and the buffer.
  Existing sizing, sector, risk, entry-window and kill-switch guards still apply.

References: [Dhan positions](https://dhanhq.co/docs/v2/portfolio/),
[Dhan candles](https://dhanhq.co/docs/v2/historical-data/),
[Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
