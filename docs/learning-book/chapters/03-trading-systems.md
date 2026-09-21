# Part Three Trading State and Distributed Systems

## Chapter 16 Model the lifecycle before writing the broker call

The safest place to begin is a state machine. An order is not a boolean success flag. A local intent can be `CREATED`, `SUBMITTING`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCEL_PENDING`, `CANCELLED`, `REJECTED`, or `UNKNOWN`. Preserve the broker's raw status alongside your normalized status because different brokers use different vocabulary.

`UNKNOWN` is essential. If a submission times out after the broker receives it, the application cannot infer rejection. Immediately repeating the submission may create another order. Persist the intent before submission; persist the broker order ID when available; reconcile using supported broker lookups and correlation identifiers. An identifier field is not necessarily a provider-enforced idempotency key.

The position changes when fills are confirmed, not when the order endpoint returns an ID. Suppose quantity 10 is accepted, four shares fill, and the rest remain open. Your position is four shares. If you cancel the remainder, wait for terminal evidence and recheck fills before creating a new order. A cancellation request can race a fill.

```text
signal -> validated decision -> durable intent -> broker acceptance
                                              -> confirmed fills
                                              -> position ledger
timeout --------------------------------------> unknown -> reconcile
```

Every fill needs deduplication. A trade can appear in a live update, a trade-book poll, and a restart reconciliation. Those are three observations of one fact, not three new purchases. Prefer broker execution identifiers where available. Do not infer a new fill from a repeated cumulative quantity without calculating and validating the delta.

Separate requested, cumulative filled, cancelled, and remaining quantities. Never reset cumulative filled to zero when a later retry fails. Persist every child order under the same intent. Quantity invariants should be executable tests, for example `0 <= total_confirmed_fill <= requested_quantity` for a simple nonsliced intent.

**Build:** Write a paper broker scenario that accepts an order, fills four shares, loses the update, and later reports the fill in the trade book. Deliver the report twice. Your position must still contain four shares. Explain what evidence is needed before submitting the remaining six.

## Chapter 17 Classic strategy math and exit behavior

Classic mode uses configured direction and percentage rules. A candidate signal does not imply strategy-candle confirmation unless that extra filter is explicitly enabled. Treat each filter as a separately testable decision with a reason code.

For a BUY at entry E, target is `E * (1 + target_pct / 100)` and initial stop is `E * (1 - stop_pct / 100)`. For SELL, reverse the signs. With entry 100, target 2%, and stop 1%, BUY exits at or above 102 or at or below 99. SELL exits at or below 98 or at or above 101. These are trigger conditions, not promises of execution at those prices.

The entry should be the weighted average of confirmed fills. If two shares fill at 100 and three at 102, average entry is `(2*100 + 3*102)/5 = 101.20`. Broker fees, taxes, and realized adjustments require separate fields; gross marked-to-market P&L is not net account profit.

### Trailing and cost stops

A BUY trailing stop uses the highest observed valid price since entry; a SELL uses the lowest. For BUY, the effective stop must not decrease when price falls. For SELL it must not increase. A fresh tick receipt timestamp does not guarantee a valid market timestamp, so validate source and ordering before updating extrema.

```python
from decimal import Decimal

def long_effective_stop(old_stop, high_water, trail_pct):
    candidate = high_water * (Decimal("1") - trail_pct / Decimal("100"))
    return max(old_stop, candidate)

assert long_effective_stop(Decimal("101"), Decimal("103"), Decimal("2")) == Decimal("101")
```

Apply this function only when trailing is enabled. If cost SL is enabled with initial SL 0.6% and RR 2, a 1.2% favorable move can move the stop to entry. Do not derive the trigger from an already-trailed stop, because that changes the original risk unit. A price stop at entry is not financially break-even after costs.

Pyramiding adds confirmed quantity, not merely requested quantity. Decide whether targets rebase to average entry and whether a new stop can ever loosen. Preserve a monotonic protective stop unless the user has explicitly authorized a different policy. Track number of completed adds separately from pending add intents.

Multiple exit rules can fire on one tick. Evaluate reasons deterministically and reserve one exit intent per owned position. A target rule, alert exit, manual exit, and trailing stop must not each send a full sell. Partial exit fills reduce remaining quantity; they do not mark the entire position closed.

**Build:** Test BUY and SELL target equality, stop equality, trailing disabled, cost-only mode, a gap beyond stop, two simultaneous exit requests, partial exits, and pyramid fills at different prices. Add the regression: a BUY stop that reached 101 must never return to 100 after a lower tick.

## Chapter 18 Configuration ownership and the full feature map

Use a stable strategy ID as the owner. Alert names are external lookup keys and display strings. Define normalization once; preserve the original display name; reject genuine collisions rather than selecting an arbitrary configuration. An edit to an existing strategy should identify that strategy, so changing unrelated fields is not mistaken for a duplicate creation.

For alert-based exit, match account, configured exit-alert identity, owning strategy, instrument, and open quantity. Strategy A's exit alert containing DABUR must not close Strategy B's DABUR position. If broker netting aggregates the same instrument across strategies, keep a local allocation ledger and reconcile aggregate broker quantity against the sum of those allocations. Account-level exits are a separate, explicitly broader operation.

| Feature | Rebuild exercise | Required failure test |
|---|---|---|
| Enabled strategy | Immutable configuration versions | Disabled alert cannot enter |
| Direction and product | Validate supported combinations | Unsupported CNC short rejected |
| Capital or fixed size | Separate sizing policies | Zero, missing and over-budget behavior |
| Entry window | Exchange timezone and date | Exact start/end boundaries |
| Daily trade limit | Atomic reservation and release policy | Simultaneous alerts at the limit |
| Sector filter | Direction-aware valid snapshot | SHORT chooses lowest changes |
| Breakout | Anchored candle and persistent watch | Expiry and duplicate wakeups |
| Target and SL | Pure trigger calculations | Gap and partial exit |
| TSL and cost SL | Independent toggles | Stop cannot loosen |
| Pyramiding | Child intents and average basis | Duplicate add request |
| CNC carry | Durable ownership and restart restore | Missing holdings is not assumed sale |
| Account P&L exit | Explicit product/account scope | Misleading UI scope rejected |
| Kill switch | Block entries without blocking recovery | Existing risk monitoring continues |
| Telegram | Async bounded notification queue | Sending failure cannot block exits |
| Backtest | Isolated research worker | No access to live broker adapter |
| Daily reset | New dashboard view and counters | Carry positions survive |

For CNC carry, broker positions and holdings can represent different settlement phases. Preserve unresolved ownership until there is evidence. Do not adopt every manually purchased holding as an app-managed position. Confirm available sell quantity and broker authorization requirements before an exit. A process running overnight cannot guarantee a fill while the exchange is closed.

Daily P&L controls must define realized versus unrealized, gross versus net, account-wide versus app-only, and MIS versus CNC scope. The visible label must match the implementation. An application kill switch is not automatically the broker's account-wide kill switch; broker controls may have different prerequisites and effects.

For Telegram, store bot secrets securely, scope destinations, and send only opted-in strategy messages. Deduplicate the 09:05 IST welcome by destination and trading day. Five-minute P&L jobs should not create duplicate account messages per strategy. Label stale or unavailable MTM instead of publishing zero. These are notification semantics, not part of order acknowledgment.

**Build:** Implement one feature per pull request with a saved-configuration roundtrip test and a behavior test. Keep a feature matrix with implemented, tested, documented, and deferred columns. Do not mark a feature complete because its toggle appears on screen.

## Chapter 19 Candles breakout watches and sector ranking

A candle summarizes an interval. For a one-minute signal received at 10:30:03 IST, the previous fully closed interval is 10:29:00 through 10:30:00, usually treated as start-inclusive and end-exclusive. For five minutes at the same instant, anchor to 10:25 through 10:30. Know whether the provider labels a candle by opening time or closing time.

Fetch the required historical candle through a broker adapter, validate instrument and interval, convert timestamps correctly, and exclude the still-forming candle. Do not assume every API's interval names or timezone semantics are identical. If resampling from one minute, anchor to the exchange session and detect missing bars rather than pretending gaps contain real data.

Store the selected reference high or low when the signal creates a breakout watch. Re-fetching "previous candle" every minute changes the strategy. If the user means a rolling reference, expose that as a different mode. Define the buffer's unit explicitly: 0.10 rupees and 0.10% are very different.

For LONG, the threshold is the reference high plus the chosen buffer; SHORT uses the reference low minus it. Define whether touching the threshold qualifies or a strict crossing is required. This book uses `>=` for LONG and `<=` for SHORT as a documented exercise policy.

A watch expires at the earlier of signal-receipt time plus TTL and the strategy entry end time. With a two-minute TTL at 10:30:03, expiry is 10:32:03 unless the entry window closes earlier. Persist it so a restart neither forgets it nor extends its life. On trigger, recheck kill switch, duplicate ownership, available budget, current config policy, and market-data freshness. Use a single-consumer claim to prevent two ticks submitting two entries.

Sector percentage is `(current_index_price - previous_session_close) / previous_session_close * 100`. Previous close must be positive and identified by the correct previous trading session, not simply yesterday's calendar date. Premarket data can legitimately show LTP equal to previous close; a cached `pct: 0` at 08:46 is not a live ranking at noon.

Cache the baseline; update the numerator from live validated ticks. A current-day market close must not overwrite the previous-session baseline while calculating that day's change. Separate baseline health, live-price health, and ranking completeness. For LONG sort descending; for SHORT sort ascending. Decide whether eligibility additionally requires positive or negative movement, because "lowest-ranked" does not necessarily mean negative.

Unknown sector mapping should produce an explicit policy outcome only when the filter is enabled. Instrument/security-ID resolution is a different concern and must work independently of membership in your curated sector list.

**Build:** Simulate a Friday-to-Monday cache, an intraday cache refresh, one missing index, equal percentages, and SHORT ranking. Add a watch at 10:30:03, advance a fake clock, and verify it cannot fire after expiry or change reference candle silently.

## Chapter 20 Async concurrency and bounded work

Concurrency means handling overlapping work. Parallelism means executing work simultaneously. An event loop switches between coroutines at await points. It does not make blocking CPU work nonblocking. Threads can help with synchronous I/O; process workers are useful for CPU-heavy backtests and isolation. Do not rely on a particular GIL configuration as your correctness mechanism.

```python
import asyncio

async def worker(queue):
    while True:
        job = await queue.get()
        try:
            if job is None:
                return
            await asyncio.sleep(0)
        finally:
            queue.task_done()

async def main():
    queue = asyncio.Queue(maxsize=32)
    async with asyncio.TaskGroup() as group:
        for _ in range(2):
            group.create_task(worker(queue))
        for number in range(100):
            await queue.put(number)
        await queue.join()
        for _ in range(2):
            await queue.put(None)

if __name__ == "__main__":
    asyncio.run(main())
```

The bounded queue applies backpressure. It does not persist jobs, and a worker crash in this simple example ends the task group. Learn that behavior before replacing the queue with Redis. Never call `asyncio.run` inside an already running event loop or create a new loop for each market tick.

Handle cancellation deliberately. Cancelling an await around `asyncio.to_thread` does not necessarily stop the thread or undo its network call. If the operation may have submitted an order, cancellation creates an unknown outcome requiring reconciliation. Preserve ownership until that uncertainty is resolved.

Use a semaphore to bound concurrent candle requests and a separate rate limiter for broker request quotas. They solve different problems: concurrency limits in-flight work; rate limits requests per time. Reserve capacity for exits and reconciliation so a burst of entry signals cannot starve safety work.

A queue of obsolete ticks is harmful even if nothing was dropped. Preserve original receipt timestamps so delayed work cannot appear fresh. Dashboard updates can often coalesce to the newest price. Risk checks may need ordered ticks and extrema to avoid missing a brief stop crossing. Define overflow behavior explicitly; silently dropping risk events is not acceptable.

**Build:** Inject a slow candle API while ticks continue. Measure tick handling delay. Cap concurrency, then compare latency before and after. Kill a task during simulated submission and verify your system records uncertainty rather than retrying blindly.

## Chapter 21 Broker adapters and WebSocket evidence

A broker adapter isolates external request shapes, response validation, error normalization, and version differences. It should expose domain operations without pretending that Dhan and Kite have identical product names, instrument identifiers, depth packets, or authentication.

Read the pinned SDK source and official API contract together. Dhan's market feed uses subscription requests and binary market responses; its order-update feed is a different channel. Kite also supplies a binary market stream with separate message handling. Let the supported SDK parse protocol packets where possible, and test your normalization against sanitized fixtures. [Dhan market feed](https://dhanhq.co/docs/v2/live-market-feed/), [Kite WebSocket protocol](https://kite.trade/docs/connect/v3/websocket/)

Track at least these states separately: credentials valid, socket connecting, socket open, subscription requested, first tick received, latest tick fresh, reconnecting, and authentication blocked. A service heartbeat is not a broker heartbeat. A subscription request sent is not exchange acknowledgment. REST fallback must be marked as REST, not relabeled as a WebSocket tick.

Keep one connection owner per broker account where required by your architecture. Token changes must notify that owner. Recreate a stale client on credential change; do not require the operator to discover that an old token remains in memory. Maintain the active subscription set and replay it after reconnect. Deduplicate instrument subscriptions by exchange and security ID.

Use exponential backoff with jitter for transient failures. Authentication rejection should stop futile retries and request reauthentication. Do not reconnect aggressively merely because an event-based instrument has no recent trades. Outside the market session, absence of ticks is not enough to diagnose a broken socket.

Store both source and receipt time with prices. If a REST fallback is permitted, enforce its age and rate limits and make degraded status visible. Fallback may support temporary monitoring but cannot guarantee instant exits during provider or network failure.

For aggressive limits, use fresh ask depth for BUY and bid depth for SELL; inspect cumulative depth for quantity; apply a bounded buffer; align to instrument tick size; enforce circuit and slippage limits. Depth is a snapshot, not a reservation. No algorithm can guarantee instant execution. Broker rejection must retain its specific reason. [Dhan order contract](https://dhanhq.co/docs/v2/orders/)

**Build:** Maintain a capability table for each SDK version. Replay malformed packets, expired-token disconnects, duplicate subscriptions, stale ticks, and order updates with missing quantities. Assert that malformed broker data cannot create a fill. A package upgrade is a change requiring contract tests, not a routine blind install.

## Chapter 22 Service boundaries recovery and architecture drawings

Your target has five deployable processes: API/dashboard, alert intake, market feed, execution, and reconciliation. The sixteen logical responsibilities discussed earlier can live inside these processes as modules. Separate logical ownership first; add more processes when workload isolation, fault containment, or scaling justifies them.

```text
Browser --HTTPS--> Nginx --> API/dashboard --read--> state
Browser <--WSS---- Nginx <-- notifications <------- tick/state bus

Chartink --> authenticated intake --> durable signal queue
                                           |
                         strategy + decision + breakout watch
                                           |
                                    execution owner --> broker
                                           ^               |
                                           |         orders and fills
Broker market feed --> normalized ticks --> risk      reconciliation
                                           |               |
                                           +---- durable position ledger

Email / Telegram / analytics consume events outside the order path
```

Keep the API from directly bypassing execution ownership for manual square-off. A manual command is another durable intent. The reconciliation process observes broker facts and updates the ledger through the same quantity rules, rather than editing arbitrary Redis fields without accounting.

Think through failure windows: before enqueue, after enqueue before acknowledgment, after claim, after broker submit before recording ID, after a partial fill, and after a database commit before publishing. Write a recovery policy for each. The most dangerous interval is not necessarily the slowest one.

Delivery guarantees apply at particular boundaries. At-least-once queue delivery is compatible with deduplicated local effects. End-to-end exactly-once trading is not obtained by naming a queue "exactly once." The broker, network, ledger, and consumer all have independent failure semantics.

Start capacity planning with assumptions: candidate signals per minute, active instruments, ticks per second, concurrent browsers, broker call quota, and acceptable queue delay. Estimate average queued work with Little's Law, `L = arrival_rate * time_in_system`, when stable. Then measure p95 and p99 latency; averages hide bursts. A queue growing faster than it drains needs admission control, not unlimited RAM.

Use liveness for process viability and readiness for safe work acceptance. A disconnected broker might make new-entry readiness false while the API must remain available for status and recovery. Restarting every component on every failed dependency can amplify an outage.

**Build:** Draw a context diagram, container/process diagram, signal sequence diagram, and order state machine. Annotate each arrow with authentication, timeout, durability, and retry policy. Explain why dashboard WebSocket failure and Dhan WebSocket failure are independent paths.

