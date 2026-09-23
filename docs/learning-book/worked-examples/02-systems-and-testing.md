## Expansion 16

### Worked lesson cumulative fills across retries

Suppose intent I requests ten shares. Child order A fills four and is confirmed cancelled for its remaining six. Child order B requests six, fills two, and is then rejected for the remainder. The final confirmed position is six shares, not zero, two, ten, or sixteen. Requested quantities and confirmed executions belong in separate fields.

```python
executions = [
    ("A", "fill-1", 4),
    ("B", "fill-2", 2),
    ("A", "fill-1", 4),
]
seen = set()
confirmed = 0
for order_id, execution_id, quantity in executions:
    key = (order_id, execution_id)
    if key not in seen:
        seen.add(key)
        confirmed += quantity
assert confirmed == 6
```

The in-memory set is only a teaching device. Use a durable unique constraint in the ledger. A process restart must not forget which fills have already been applied.

**Exercise:** Add a fill arriving after a cancellation request but before cancellation confirmation. Explain why submitting a replacement at the moment you send cancel can overfill the intent. Write an explicit resolution policy before coding a retry loop.

## Expansion 17

### Worked lesson a target and trailing replay

BUY entry is 100, target is 110, fixed stop is 98, trailing distance is 2%. Ticks are 100, 103, 105, 104, 102. At 105 the trailing line reaches 102.90. At 104 it remains 102.90. At 102 the exit trigger fires. The simulator can fill at 102; it must not claim an execution at 102.90 merely because that was the trigger.

```python
prices = [100, 103, 105, 104, 102]
high = 100
stop = 98
history = []
for price in prices:
    high = max(high, price)
    stop = max(stop, high * 0.98)
    history.append((price, stop, price <= stop))
assert history[-1][2] is True
assert history[3][1] == history[2][1]
```

This float-based trace illustrates the sequence; use the Decimal domain implementation for price-sensitive calculations. Repeat with a SELL and verify the stop moves downward only. Then disable trailing and verify the fixed stop remains unchanged even though the high-water mark can still be recorded for later analysis.

## Expansion 18

### Worked lesson stable IDs versus editable names

```json
{
  "strategy_id": "strategy-17",
  "version": 4,
  "display_name": "Morning Momentum",
  "entry_alert_name": "morning momentum scanner",
  "exit_alert_enabled": true,
  "exit_alert_name": "momentum weakness scanner"
}
```

A position references `strategy-17` and the version used at entry. Renaming the display label does not change ownership. Editing the external alert name changes future matching under a documented policy. An exit event must match the configured exit relationship, not whichever alert name currently appears beside a row.

**Exercise:** Create A and B, each holding DEMO. Send A's exit alert. Assert only A's local allocation is reserved for exit. Then reconcile the aggregate broker quantity and prove the remaining allocation still belongs to B. Include a test where the same display name exists on a different client account.

## Expansion 19

### Worked lesson anchoring a candle and TTL

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

received = datetime(2026, 9, 18, 10, 30, 3,
                    tzinfo=ZoneInfo("Asia/Kolkata"))
current_start = received.replace(second=0, microsecond=0)
reference_start = current_start - timedelta(minutes=1)
deadline = received + timedelta(minutes=2)
assert reference_start.strftime("%H:%M:%S") == "10:29:00"
assert deadline.strftime("%H:%M:%S") == "10:32:03"
```

For general intervals, anchor to the exchange session rather than assuming every interval begins at midnight. Obtain the reference high from the completed bar, persist it with its interval, and keep the threshold constant until this watch expires. Do not treat missing candles as high zero.

**Exercise:** Construct sector data with changes 1.4, -2.1, 0.3, and missing. LONG top two should rank 1.4 then 0.3; SHORT bottom two should rank -2.1 then 0.3 unless your policy additionally requires a negative sign. Write that sign policy separately from ranking.

## Expansion 20

### Worked lesson concurrency is not the same as a rate limit

```python
import asyncio

async def demo():
    in_flight = 0
    peak = 0
    semaphore = asyncio.Semaphore(2)

    async def fetch(number):
        nonlocal in_flight, peak
        async with semaphore:
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                await asyncio.sleep(0.01)
                return number
            finally:
                in_flight -= 1

    result = await asyncio.gather(*(fetch(i) for i in range(6)))
    assert result == list(range(6)) and peak == 2

if __name__ == "__main__":
    asyncio.run(demo())
```

Two concurrent calls can still create hundreds of requests per second if each finishes quickly. Implement rate limiting separately using provider-specific budgets. Reserve a portion for exits and reconciliation. In a multi-process deployment, a per-process limiter may multiply total request rate; decide where the shared account quota is enforced.

## Expansion 21

### Worked lesson a capability matrix before an SDK upgrade

```text
Capability           Dhan adapter       Kite adapter
Instrument identity  exchange + ID      exchange + token/symbol mapping
Accepted order       validated order ID validated order ID
Fill evidence        normalized trades  normalized trades
Quote freshness      source + times    source + times
Pending cancel       request + confirm request + confirm
Token replacement    owner reload      owner reload
```

Fill this table from the pinned SDK and current provider API. Inspect a method signature locally:

```python
import inspect

def inspect_method(client):
    print(type(client).__module__)
    print(inspect.signature(client.place_order))
```

This function expects a client passed by your test setup. Do not construct a live client or print its configuration solely to inspect a signature. Add a contract fixture showing the exact request your adapter emits. Test upgrades in a separate environment and compare request fields, response normalization, exception behavior, and feed callbacks before deployment.

## Expansion 22

### Worked lesson capacity calculation with explicit assumptions

Assume 120 candidate jobs per minute and mean processing time 0.4 seconds. The mean arrival rate is two jobs per second. If stable, Little's Law predicts about 0.8 jobs in the system on average. This does not describe a burst of 100 simultaneous signals or a provider call that stalls for 30 seconds.

```python
arrival_per_second = 120 / 60
mean_seconds = 0.4
average_in_system = arrival_per_second * mean_seconds
assert average_in_system == 0.8
```

Now assume 500 instruments each produce four ticks per second and each normalized event is about 250 bytes. The raw event payload rate is about 500,000 bytes per second before protocol, copies, storage, and fanout overhead. Measure actual sizes and serialization costs rather than treating this estimate as capacity proof.

**Exercise:** Draw the bottleneck if one worker performs three serial 0.3-second broker calls per signal. Explain what can run concurrently and what must remain ordered per account or position. Add admission limits rather than an unlimited queue.

## Expansion 23

### Worked lesson avoiding stale responses in the browser

Two refresh requests can finish out of order. Request A starts first but takes longer; B returns a newer position, then A overwrites it. Use an entity version from the server or a request-generation guard for a single view.

```javascript
let generation = 0;
async function refreshPositions() {
  const mine = ++generation;
  const response = await fetch('/api/positions');
  if (!response.ok) throw new Error('Positions unavailable');
  const data = await response.json();
  if (mine !== generation) return;
  renderPositions(data.positions);
}
```

`renderPositions` is your own DOM function. This guard only handles this view's request ordering; it does not solve out-of-order domain events. Include a server-side version or event sequence in WebSocket updates and reject older updates per entity.

**Exercise:** Intercept two responses in a browser test and deliver them backward. Verify a closed position cannot become open again merely because an older response arrives late. Preserve accessible error text while keeping the last known data marked stale.

## Expansion 24

### Worked lesson tests that catch the wrong operator

```python
import pytest

@pytest.mark.parametrize("price,expected", [(101, False), (102, True), (103, True)])
def test_target_boundary(price, expected):
    assert (price >= 102) is expected
```

Changing `>=` to `>` should fail the equality case. If a mutation tool makes that change and the suite stays green, the tests do not protect the boundary rule. Add examples around threshold, invalid data, and exact expiry time.

Avoid asserting implementation details that do not matter, such as the precise number of helper function calls after a harmless refactor. Assert business evidence: confirmed quantity, deduplicated execution, preserved ownership, and a visible closed status. Use spies only where the call itself is the externally relevant effect.

**Exercise:** Write a deliberately wrong P&L function that ignores SELL direction. Add the smallest test that exposes it, then expand to a parameterized BUY/SELL table with costs clearly excluded or included.

## Expansion 25

### Worked lesson a three boundary integration test

```python
def test_signal_creates_one_position(client):
    config = {"name": "Practice", "quantity": 1}
    assert client.post("/api/configs", json=config).status_code == 200
    signal = {"event_id": "integration-1", "strategy": "Practice",
              "symbol": "DEMO", "price": "100"}
    first = client.post("/api/signals", json=signal).json()
    second = client.post("/api/signals", json=signal).json()
    rows = client.get("/api/positions").json()["positions"]
    assert first["position_id"] == second["position_id"]
    assert len(rows) == 1
```

This test uses the temporary-database `client` fixture in the included paper lab. It crosses HTTP validation, persistence, and position creation. It does not test Dhan, Redis, or network partitions. Naming the tested boundary prevents false confidence.

**Exercise:** Repeat against a PostgreSQL repository. Add a second process making the same request and assert the durable uniqueness constraint holds. Do not replace PostgreSQL with a dictionary for the test whose purpose is to verify transaction behavior.

## Expansion 26

### Worked lesson browser evidence beyond a screenshot

```python
from playwright.sync_api import expect

def check_zero_retry_roundtrip(page, base_url):
    page.goto(base_url)
    page.get_by_label("Strategy name", exact=True).fill("Zero Retry")
    page.get_by_label("Retry count", exact=True).fill("0")
    page.get_by_role("button", name="Save strategy", exact=True).click()
    expect(page.get_by_role("status")).to_have_text("Saved Zero Retry")
    response = page.request.get(base_url + "/api/configs")
    assert response.ok
    strategy = next(row for row in response.json()["strategies"]
                    if row["name"] == "Zero Retry")
    assert strategy["retry_count"] == 0
```

The visible success message and persisted numeric value are both checked. In a production-auth suite, `page.request` shares the browser context's authentication state; keep isolated contexts per test user. Traces and stored authentication files are credentials-bearing artifacts and should not be committed.

**Exercise:** Add a tablet viewport and keyboard-only form submission. Test a server-side validation failure despite a browser form that appears valid. Then take a screenshot as supporting evidence, not as the only assertion.

## Expansion 27

### Worked lesson interpret a latency distribution

```python
from statistics import median

samples_ms = [10] * 95 + [800] * 5
ordered = sorted(samples_ms)
def nearest_rank(percent):
    from math import ceil
    return ordered[max(0, ceil(percent * len(ordered)) - 1)]

assert median(samples_ms) == 10
assert nearest_rank(0.99) == 800
```

The median looks excellent while one percent of requests can still be very slow. Percentile definitions vary for finite samples, so state the method and use an appropriate histogram in production. Include timeouts and failed requests in reliability analysis rather than measuring only successes.

**Exercise:** Run a synthetic load test for five minutes and a soak test for an hour. Compare memory, thread count, open descriptors, queue length, and p99 latency at the beginning and end. A test that slowly accumulates resources may pass a short demonstration and fail during an entire market session.

## Expansion 28

### Worked lesson a CI job with an honest boundary

```yaml
name: paper-lab
on: [push, pull_request]
jobs:
  tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: docs/learning-book/lab
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install -r requirements.txt
      - run: python -m playwright install --with-deps chromium
      - run: python -m pytest -q tests
```

This is a teaching workflow, not an assertion that these action tags will always be current. Production pipelines should pin reviewed immutable action revisions and periodically update them. The included browser suite falls back to installed Playwright Chromium when a Chrome channel is unavailable.

**Exercise:** Add separate artifacts for failure traces, restrict retention, and ensure no broker secret is available to pull-request jobs from untrusted branches. Record the test Python/package versions. A green paper-lab job is not authorization to trade live.
