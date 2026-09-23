## Expansion 1

### Worked lesson tracing a program by hand

Before typing, create a table with columns line, names, and output. Step through this example as the interpreter would:

```python
price = 100
quantity = 2
notional = price * quantity
price = 105
print(notional)
print(price * quantity)
```

The outputs are 200 and 210. `notional` does not contain a spreadsheet formula that updates when price changes. It contains the result of an earlier multiplication. This matters when a dashboard displays an entry-time value instead of recalculating on ticks.

Now add `enabled = False` and an `if enabled:` branch. Indentation defines the suite controlled by the condition. Use four spaces consistently. A syntax error prevents interpretation; a runtime error occurs on an executed path; a logic error produces an incorrect result without an exception. You need different techniques for each: parser feedback, traceback inspection, and expected-behavior tests.

**Independent exercise:** Write three examples that produce the same printed output but use different types internally. Explain why printed output alone cannot validate a request schema. Then write an assertion about the type as well as the value.

## Expansion 2

### Worked lesson package layout and interpreter identity

Create this layout in your own repository, not inside the production app:

```text
trading-school/
  school/
    __init__.py
    money.py
    cli.py
  tests/
    test_money.py
  pyproject.toml
  .gitignore
```

In `school/money.py`, define a function. In `school/cli.py`, import it with `from school.money import ...`. Run `python -m school.cli` from the repository root. Running a file from an arbitrary working directory can change which modules Python finds; avoid fixing import problems by scattering `sys.path.append` throughout application code.

Inspect the active environment without printing secrets:

```bash
python -c "import sys; print(sys.executable); print(sys.version)"
python -m pip --version
python -m pip show fastapi
python -m pip check
```

The interpreter path, pip path, installed distribution, and compatibility check answer different questions. Save exact dependency versions after a tested installation, review the result, and record the Python version as well. A virtual environment copied between operating systems is not a portable deployment artifact.

**Exercise:** Create a second environment without FastAPI. Predict which command fails in it. Repair the invocation rather than installing packages globally. Add a README command that works in a fresh checkout.

## Expansion 3

### Worked lesson grouping without cross strategy leakage

```python
rows = [
    {"strategy": "A", "symbol": "RELIANCE", "qty": 2},
    {"strategy": "B", "symbol": "DABUR", "qty": 3},
    {"strategy": "A", "symbol": "RELIANCE", "qty": 1},
]
totals = {}
for row in rows:
    key = (row["strategy"], row["symbol"])
    totals[key] = totals.get(key, 0) + row["qty"]
assert totals[("A", "RELIANCE")] == 3
assert ("A", "DABUR") not in totals
```

The tuple defines ownership. Replacing it with just `symbol` would merge allocations from different strategies. This example groups already-validated rows; it is not a database transaction or a broker position reconciliation algorithm.

A comprehension is convenient when it remains readable. Use an ordinary loop when you need several validation steps or detailed errors. Clever one-liners are not an engineering objective. Learn dictionary iteration through `.items()`, set intersection for membership checks, and a `deque` for processing oldest events.

**Exercise:** Add account and product to the key. Create two clients with the same strategy name and symbol. Prove they remain separate. Explain why a string such as `account:strategy:symbol` needs careful escaping if any field can contain the separator.

## Expansion 4

### Worked lesson weighted average and invariants

```python
from decimal import Decimal

def average_fill(fills):
    if not fills:
        raise ValueError("No fills")
    quantity = 0
    value = Decimal("0")
    for qty, price in fills:
        if type(qty) is not int or qty <= 0:
            raise ValueError("Invalid fill quantity")
        if not price.is_finite() or price <= 0:
            raise ValueError("Invalid fill price")
        quantity += qty
        value += qty * price
    return quantity, value / quantity

qty, average = average_fill([
    (2, Decimal("100")), (3, Decimal("102"))
])
assert qty == 5 and average == Decimal("101.2")
```

Trace the running quantity and value after each iteration. The result is not the unweighted average 101 because the two price levels have different quantities. Use the returned confirmed quantity rather than the original requested quantity.

A useful invariant is that an average of positive-weight prices lies between the minimum and maximum prices. Write a property-based test for that claim. Then deliberately feed a duplicate execution twice and observe that arithmetic cannot identify the duplicate by itself. Deduplication belongs before aggregation.

## Expansion 5

### Worked lesson exception boundaries and cleanup

```python
from contextlib import contextmanager

@contextmanager
def tracked_resource(events):
    events.append("opened")
    try:
        yield "resource"
    finally:
        events.append("closed")

events = []
try:
    with tracked_resource(events):
        raise ValueError("simulated failure")
except ValueError:
    events.append("reported")
assert events == ["opened", "closed", "reported"]
```

`finally` runs while control leaves the protected block. It does not make an external action reversible. Closing a socket after an order timeout does not cancel an order at the broker.

For a real adapter, translate a provider-specific error into a domain exception with a sanitized reason, preserving the original cause through `raise DomainError(...) from exc`. At the route boundary, produce a safe response and correlation ID. At the worker boundary, persist the failed or unknown state. Do not log the full request just to make diagnosis easy.

**Exercise:** Raise during setup, normal work, and cleanup separately. Explain which exceptions escape. Add a test that resources are released when a coroutine is cancelled.

## Expansion 6

### Worked lesson dependency injection with a recording fake

```python
from dataclasses import dataclass, field

@dataclass
class RecordingBroker:
    submitted: list = field(default_factory=list)

    def submit(self, intent):
        self.submitted.append(intent)
        return {"order_id": f"paper-{len(self.submitted)}"}

first = RecordingBroker()
second = RecordingBroker()
first.submit({"symbol": "DEMO", "quantity": 1})
assert len(first.submitted) == 1
assert second.submitted == []
```

`default_factory` creates an independent list for each object. A recording fake lets a test inspect what the service asked the broker to do. It should not pretend every request is filled; add a separate method to supply execution evidence.

Design the service constructor around small dependencies: repository, broker, clock, and notifier. That makes failure injection straightforward. If a test must patch ten global variables to construct a service, consider whether the production ownership boundaries are too broad.

**Exercise:** Implement a broker that raises before accepting, and another that accepts but raises before returning. Your service must distinguish the certainty available in these scenarios rather than treating both as ordinary rejection.

## Expansion 7

### Worked lesson asynchronous decorators and cancellation

```python
import asyncio
from functools import wraps
from time import perf_counter

def measured_async(function):
    @wraps(function)
    async def wrapper(*args, **kwargs):
        started = perf_counter()
        try:
            return await function(*args, **kwargs)
        finally:
            print(function.__name__, perf_counter() - started)
    return wrapper

@measured_async
async def sample():
    await asyncio.sleep(0.01)
    return 7

async def demo():
    assert await sample() == 7

if __name__ == "__main__":
    asyncio.run(demo())
```

The wrapper awaits the function, so its duration includes the work. The `finally` block also runs on cancellation. Do not catch cancellation and continue executing a trade unless you have a precisely defined ownership protocol.

Inspect `wrapper.__name__` with and without `wraps`. Then implement a generator that yields records from several files. Ask when a file handle closes if the consumer stops early. Practice explicit context management instead of assuming garbage collection happens immediately.

## Expansion 8

### Worked lesson a bounded rolling window

```python
from collections import deque

def rolling_means(values, width):
    if width <= 0:
        raise ValueError("Width must be positive")
    window = deque()
    total = 0.0
    for value in values:
        window.append(value)
        total += value
        if len(window) > width:
            total -= window.popleft()
        if len(window) == width:
            yield total / width

assert list(rolling_means([1, 2, 3, 4], 3)) == [2.0, 3.0]
```

Each item is appended and removed at most once, giving O(n) processing and O(width) storage. This example uses floats for a statistical illustration, not final price settlement. Recomputing each window sum would be O(n times width).

**Exercise:** Add timestamps and reject out-of-order samples under a stated policy. Then support a time window rather than a fixed count. Explain why "last 20 ticks" and "last 20 seconds" are not equivalent for instruments with different activity.

## Expansion 9

### Worked lesson documenting one endpoint completely

For `POST /signals`, write a contract before its implementation: the caller presents a webhook credential; the account is resolved server-side; the payload contains source event identity, alert name, symbols, and source time; a bounded durable job is written; the response contains a receipt ID. Repeated delivery of the same event must not produce a new entry.

```bash
curl -i -X POST http://127.0.0.1:8015/api/signals \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"practice-01","strategy":"Practice","symbol":"DEMO","price":"100"}'
```

This command targets the unauthenticated local paper lab only. Its supplied synthetic price is not a production webhook trust model. Save `Practice` first. Repeat the request and compare receipt identity, then change price while retaining the ID and observe conflict.

A webhook sender may not supply a convenient unique ID. Define your deduplication window and canonical payload identity carefully; hashing only the symbol forever would suppress legitimate future entries. Bound replay by strategy, account, event time, and known delivery semantics rather than assuming any hash makes a request safe.

## Expansion 10

### Worked lesson replace a dependency in a test

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()

def current_account():
    raise RuntimeError("Real authentication belongs here")

@app.get("/whoami")
def whoami(account=Depends(current_account)):
    return {"account_id": account["id"]}

def test_dependency_override():
    app.dependency_overrides[current_account] = lambda: {"id": "test-1"}
    try:
        with TestClient(app) as client:
            assert client.get("/whoami").json() == {"account_id": "test-1"}
    finally:
        app.dependency_overrides.clear()
```

The test override is explicit and removed afterward. Do not implement an internet-accessible header such as `X-Test-Admin: true` as a convenient substitute. Test-only shortcuts must never be enabled by production request input.

**Exercise:** Add a second dependency that checks ownership of a strategy. Return 403 for an authenticated account requesting someone else's strategy. Test both paths without replacing the authorization rule itself.

## Expansion 11

### Worked lesson querying and indexing the ledger

```sql
CREATE INDEX fill_account_time
ON fill (account_id, executed_at DESC);

SELECT broker_order_id,
       SUM(quantity) AS filled_quantity,
       SUM(quantity * price) / SUM(quantity) AS average_price
FROM fill
WHERE account_id = $1
GROUP BY broker_order_id;
```

`$1` represents a bound parameter in this SQL illustration; use the placeholder convention of your chosen driver. The query aggregates fills for one account. An index on `(account_id, executed_at)` supports account/time access patterns, but does not automatically optimize every grouping query. Read `EXPLAIN` on realistic data before adding more indexes.

Every index consumes storage and adds work to writes. A wide JSON document with an index on every possible property is not a substitute for deliberate access patterns. Use foreign keys for ownership relationships and a unique execution identity to prevent double accounting.

**Exercise:** Populate 100,000 synthetic fills, inspect query plans before and after an index, and measure execution time in a warmed and cold-ish cache scenario. Record that timings are machine- and data-dependent.

## Expansion 12

### Worked lesson a conditional transition

```sql
UPDATE position_allocation
SET state = 'EXIT_REQUESTED', version = version + 1
WHERE id = $1
  AND account_id = $2
  AND state = 'OPEN'
  AND version = $3
RETURNING id, version;
```

This example assumes your own position table with those columns. One returned row means this transaction won the state transition. Zero rows can mean a competing update, a closed position, or the wrong account; classify it using an authorized follow-up read. Do not call the broker before successfully reserving the intent.

Inside the same transaction, insert an outbox event describing the exit request. Commit before a separate worker performs external I/O. If the worker loses the broker response, preserve `UNKNOWN` and reconcile. The database transaction does not extend across the internet to the broker.

**Exercise:** Use two independent database connections and a barrier to issue the update concurrently. Exactly one should reserve the exit for the expected version. Demonstrate why an in-process Python lock does not protect another process.

## Expansion 13

### Worked lesson safely releasing a Redis lease

```lua
-- KEYS[1] is the lease key; ARGV[1] is this worker's token.
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
```

Compare-and-delete must be atomic. A separate GET and DEL can race: your old lease expires, another worker acquires it, then your DEL removes its lease. The script avoids that release bug but does not prevent a paused old worker from making an external call after expiry. That is why lease ownership and uncertain order reconciliation remain separate design problems.

**Exercise:** Use a disposable Redis instance. Acquire a short lease with token A, let it expire, acquire with token B, and run the release script with A. Assert B remains. Then disconnect renewal and observe how the worker stops accepting new work while resolving any in-flight uncertainty.

## Expansion 14

### Worked lesson binding an email OTP to its purpose

```python
import hashlib
import hmac
import secrets

def otp_digest(key, challenge_id, purpose, account_id, code):
    message = "\x00".join([challenge_id, purpose, account_id, code])
    return hmac.new(key, message.encode(), hashlib.sha256).hexdigest()

key = secrets.token_bytes(32)
code = f"{secrets.randbelow(1_000_000):06d}"
stored = otp_digest(key, "challenge-7", "verify_email", "account-1", code)
candidate = otp_digest(key, "challenge-7", "verify_email", "account-1", code)
assert hmac.compare_digest(stored, candidate)
assert stored != otp_digest(key, "challenge-7", "reset_password", "account-1", code)
```

This is a digest demonstration, not a complete authentication system. Keep the HMAC key outside the database, validate that identifiers cannot contain the delimiter or use structured serialization, and enforce expiry, rate limits, and atomic single use. A six-digit secret is easy to exhaust without online attempt limits.

**Exercise:** Write a transaction that increments a failed-attempt count without extending original expiry. Two correct simultaneous submissions must not both create sessions. Use a fake mail sender that records messages in test memory; never expose codes through a production response.

## Expansion 15

### Worked lesson designing recovery without erasing trades

Separate `admin_identity`, `admin_sessions`, `mfa_enrollment`, and trading state in storage. A recovery operation should revoke the identity's sessions and MFA seeds under an audited authorization path, but leave fills, positions, strategy history, and broker reconciliation evidence intact.

```text
Trusted operator verifies reset authorization
  -> disable new admin sessions
  -> revoke existing sessions and pending MFA challenges
  -> mark identity recovery required
  -> record audit event without secrets
  -> issue a bounded out-of-band setup opportunity
```

Require a fresh password check for changing a TOTP seed. Protect the enrollment QR from caching and third-party analytics. Reject the same time-step code twice if replay protection is part of the policy, using an atomic compare/update, not two independent reads.

**Exercise:** Sign in in two browsers, perform a recovery operation in the test environment, and show both sessions lose access. Confirm an open paper position still exists and its risk worker still processes ticks. Security recovery should not accidentally stop position accounting.
