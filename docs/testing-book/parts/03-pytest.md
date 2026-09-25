# Part III. Pytest and Backend Test Engineering

## Chapter 14. Your First Pytest Tests and Assertions

### Discover, arrange, act, assert

Pytest discovers test files and functions by naming conventions. A function named `test_target_exit` in `test_domain.py` is collected; a normal helper named `make_position` is not a test. A test should arrange known inputs, act on the system, and assert an independently chosen expected result. Cleanup belongs in a fixture or a `finally` block so failures cannot prevent it.

Run this complete example from the lab directory:

```python
from decimal import Decimal
from domain import Position

def test_buy_target_closes_position():
    position = Position.from_fill("alpha", "TEST", "BUY", 2, "100", "1", ".5")
    position.mark("101")
    assert position.status == "CLOSED"
    assert position.exit_reason == "TARGET"
    assert position.pnl == Decimal("2")
```

The first line inside the test creates the starting state. The next line simulates the observation that triggers a decision. The assertions inspect the result. Three assertions are appropriate here because they describe one behavior: closure at a target. Combining login, configuration, entry, ten unrelated validation failures and logout into one unit test makes failures difficult to diagnose.

### Read a failure report

Change the expected P&L to 3 and run the test. Pytest shows the test name, failing line and comparison. The defect is now deliberately in the test, not the domain model. Undo it after observing the report. A red result means actual and expected differ; investigation determines which is wrong.

```bash
python -m pytest tests/test_domain.py -q
python -m pytest tests/test_domain.py -v
python -m pytest -k "target or sector" -q
python -m pytest --collect-only -q
```

`-q` reduces output, `-v` lists individual tests, `-k` filters names and `--collect-only` checks discovery without execution. A command that runs zero tests is not a passing release check. Exit codes distinguish success, test failures and collection problems; CI must preserve them rather than append `|| true`.

### Assert the right thing

Use exact equality for discrete values and Decimal money where the contract is exact. Use `pytest.approx` when a numerical algorithm legitimately has floating tolerance; choose a tolerance meaningful to the requirement. Do not allow a one-rupee tolerance merely to hide a paise rounding bug. Check collection contents rather than only their length when identity matters.

```python
import pytest
from domain import quantity

def test_invalid_price_is_rejected():
    with pytest.raises(ValueError, match="positive"):
        quantity("10000", "0")
```

Checking an exception alone may be insufficient. For a stateful API, also assert that no configuration or position was written. In a broker test, assert that submission was never called. Negative outcomes often have important absence requirements.

### Practice and checkpoint

Write tests for entry 100, quantity 2 and a stop at 99.50. Test 99.51, 99.50 and 99.49 separately. The expected exact-boundary behavior is closure at `<=` for BUY in this lab. Tests should encode this rule rather than accidentally choosing only prices far beyond the boundary.

## Chapter 15. Fixtures, Scope and Safe Cleanup

### A fixture supplies a dependency

A fixture is a named provider of data or resources. Pytest inspects a test's arguments and supplies matching fixtures. This is dependency injection without manual object construction in every test. `conftest.py` shares fixtures across a directory's tests without importing it explicitly.

```python
import pytest
from fastapi.testclient import TestClient
from server import create_app

@pytest.fixture
def client():
    with TestClient(create_app()) as value:
        yield value

def test_empty_positions(client):
    response = client.get("/api/positions")
    assert response.status_code == 200
    assert response.json() == []
```

The application factory makes fresh dictionaries for this test. `yield` returns control to the test and resumes for teardown afterward. The context manager exits even if an assertion fails. Starting from empty state is not merely convenient: it prevents another test's positions from changing this test's result.

### Scope is a tradeoff

Function scope creates a resource per test. Module and session scopes reuse it longer and may be faster. Reuse also increases shared-state risk. A browser process can often be session-scoped while browser contexts remain function-scoped. A mutable account reused by parallel tests can make an otherwise correct suite unreliable. Use function scope until you can explain precisely why reuse is safe.

Do not make all setup `autouse=True`. Invisible setup can connect to services even for tests that only need arithmetic. Autouse is appropriate for truly universal safety checks, such as refusing a production base URL. Prefer explicit dependencies for authentication, data creation and service startup.

### Factories and cleanup stacks

When a test needs several strategies, return a fixture factory that records created identifiers. Teardown removes only those objects. Never "clean" a shared database with an unscoped flush. For SQL integration tests, transaction rollback may isolate data, but external workers using other connections will not automatically participate in that transaction. For Redis, unique prefixes plus bounded TTLs are safer than assuming a database number is exclusively yours.

Pytest's `tmp_path` fixture creates a separate directory for each test. Use it for uploaded CSVs, downloaded reports and JSON test data. Test-generated browser storage state contains credentials and must live in ignored, access-controlled artifacts, not a fixture committed to Git.

### A fixture failure is not the same as a test failure

If the local server cannot bind or the browser executable is missing, setup errors before the behavior runs. Report that as an environment/setup problem, not proof that Save is broken. Conversely, do not dismiss every setup failure: a new startup crash may be the application defect the fixture reveals.

### Practice and checkpoint

Create two tests: one saves a strategy, the other expects no strategies. Run them in both orders. They should pass with fresh factory state. If the second relies on the first deleting records, isolation is incomplete. Read the companion `lab_url` fixture and identify where its server is started, readiness is checked and shutdown is verified.

## Chapter 16. Parameterization, Markers, Coverage and Async Tests

### One rule, many representative examples

Parameterization runs the same behavior with multiple named data sets. It reduces duplicated test code but should not hide meaning in a giant spreadsheet. Prefer descriptive IDs and a few well-chosen partitions.

```python
from decimal import Decimal
import pytest
from domain import tick_price

@pytest.mark.parametrize("side,expected", [
    pytest.param("BUY", "100.05", id="buy-rounds-up"),
    pytest.param("SELL", "100.00", id="sell-rounds-down"),
])
def test_tick_policy(side, expected):
    assert tick_price("100.03", ".05", side) == Decimal(expected)
```

The tick size is supplied test data, not a claim that every exchange instrument uses 0.05. Expand with already aligned prices, 0.01 ticks, invalid ticks and low prices that would round to zero. Each test should explain its expected outcome.

### Organize suites without hiding failures

Register markers such as `api`, `browser`, `slow` and `security` in pytest configuration. `--strict-markers` catches spelling mistakes. `skip` records a test not executed; `xfail` records a known expected failure. Use `xfail(strict=True)` for tracked defects when an unexpected pass should demand review. Do not use xfail to make an unexplained financial-safety failure appear acceptable.

```bash
python -m pytest -m api -q
python -m pytest -m "not browser" -q
python -m pytest -x --tb=short
python -m pytest --junitxml=artifacts/results.xml
```

`-x` stops after the first failure and is useful while debugging; it is not always best for nightly discovery. JUnit XML enables CI reporting and is separate from a human-readable HTML report.

### Coverage asks where tests executed

With optional `pytest-cov`, use `--cov=domain --cov-branch --cov-report=term-missing`. Branch coverage asks whether alternative paths executed. Even 100% branch coverage cannot prove all event orders, numeric ranges or authorization cases. Add mutation thinking: if `<=` becomes `<`, does a test fail? If filled quantity is ignored, does a test fail? These questions assess assertions, not merely execution.

### Async tests

Choose one supported async testing approach and configure it explicitly. This recipe requires optional `pytest-asyncio`:

```python
import asyncio
import pytest

@pytest.mark.asyncio
async def test_event_released():
    event = asyncio.Event()
    asyncio.get_running_loop().call_soon(event.set)
    await asyncio.wait_for(event.wait(), timeout=2)
    assert event.is_set()
```

Do not call `asyncio.run()` from inside an already running event loop. Match Playwright's sync API to synchronous tests and async API to asynchronous tests. Mixing them can create loop errors or blocked execution. Timeouts bound a test; they should not be its only oracle.

### Practice and checkpoint

Parameterize both sides and exact target/stop thresholds. Then intentionally change a comparator in your disposable copy and observe which test catches it. Restore the code immediately. Explain why a skipped browser suite must be visible in a report claiming "all tests passed."

## Chapter 17. Mocks, Fakes, Spies and Broker Contract Tests

### Choose the smallest useful substitute

A stub returns canned data. A fake implements simplified behavior, such as an in-memory store. A spy records calls. A mock supports expectations about interactions. These terms overlap in libraries; the important question is what behavior the substitute preserves and what it omits.

```python
from unittest.mock import Mock

def place_if_enabled(broker, enabled):
    if enabled:
        return broker.place(symbol="TEST", qty=2)
    return None

def test_disabled_never_submits():
    broker = Mock()
    assert place_if_enabled(broker, False) is None
    broker.place.assert_not_called()
```

Use `AsyncMock` for awaited dependencies. Patch where a name is looked up, not automatically where it was originally defined. If `service.py` imports `fetch` into its own namespace, patch `service.fetch`. `autospec` or `spec_set` can expose missing methods and signature mistakes. An unrestricted mock accepts almost anything and can conceal exactly the SDK mismatch you intended to detect.

### Acceptance is not execution

Broker adapters should normalize vendor responses into a stable internal contract without inventing fills. A response containing an order identifier proves acceptance or registration according to the broker's contract, not necessarily traded quantity. Test a sequence: accepted -> pending -> partial 4/10 -> cancelled with final filled 4. The local position must reflect 4, and any replacement must consider only the reconciled remainder.

Official broker references: [Dhan orders](https://dhanhq.co/docs/v2/orders/) and [Kite orders](https://kite.trade/docs/connect/v3/orders/). Use the installed SDK version and reviewed response fixtures; do not call a live order endpoint as part of a beginner exercise. Contract fixtures should remove account IDs and secrets while retaining field names and types.

### Adversarial fixtures

Include empty bodies, malformed JSON, HTTP 429, HTTP 500, unknown status, missing filled quantity, error envelopes and unexpected additional fields. Forward-compatible parsing can ignore harmless extra fields, but missing safety-critical fields should produce an unresolved/error outcome rather than guessed success. An unrecognized rejection reason should not trigger an unconditional retry.

A mocked adapter test proves your behavior for the supplied contract. It cannot prove that a vendor still returns that contract today. A separate read-only compatibility check, version review and controlled staging validation cover that gap. Never claim that a monkeypatched success response validates network authentication.

### Practice and checkpoint

Write a fake broker with a list of scripted states and a submission counter. Send the same event twice and assert one submission. Then simulate a network timeout after acceptance. The correct next step is reconcile by correlation/order identity, not assume the order failed and place another full quantity.

## Chapter 18. Database, Redis, Transactions and Concurrency Tests

### Test durable facts, not only responses

An API can return "saved" while the wrong Redis key was written. An integration test should read through the supported store boundary and verify user, strategy and field values. A persistence test should reopen the storage connection or restart the component as appropriate. Reusing the same in-memory object cannot prove restart recovery.

For a relational design, choose keys and constraints that encode ownership. A conceptual positions table might use account ID, strategy ID and trade ID, with symbol as an attribute rather than the only key. Test uniqueness, foreign keys, nullability, transaction rollback and migration compatibility. Use parameterized SQL; test user input containing punctuation as data, never concatenate it into a query.

```sql
SELECT strategy_id, symbol, remaining_qty
FROM positions
WHERE account_id = :account_id AND status = :status;
```

The named parameters here are conceptual; use your database driver's supported parameter syntax. Authorization must come from the authenticated identity, not a caller-selected account ID alone.

### Redis is more than a dictionary

A fake Redis implementation is useful for fast tests, but verify critical Lua scripts, expiry, streams, acknowledgment and atomic compare-and-set behavior against a disposable real Redis instance too. Configure a unique namespace per test worker. Never run `FLUSHALL` on a client VM. Verify that stale lease tokens cannot release a new owner's lock and that queued jobs are not lost between claiming and completion.

### Deterministic concurrency

This recipe proves an order task can finish while a simulated blocking data request remains blocked. It does not measure production latency:

```python
import asyncio
import threading

async def demonstrate_independent_io():
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def data_call():
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("Test gate was not released")

    task = asyncio.create_task(asyncio.to_thread(data_call))
    try:
        await asyncio.wait_for(started.wait(), 2)
        order_result = await asyncio.to_thread(lambda: "accepted")
        assert order_result == "accepted"
        assert not task.done()
    finally:
        release.set()
        await task
```

For distributed orders, test the dangerous window: broker accepts, worker crashes before recording the result, job is redelivered. An outbox or durable intent record can improve recoverability, but the acceptance criteria must still require reconciliation before another submission. "Exactly once" is not created by adding a queue.

### Practice and checkpoint

Design tests for expired locks, stale lock owners, duplicate jobs and crash recovery. Include a test where ownership is lost immediately before broker submission. Expected behavior is to stop new submission and reconcile; do not weaken the lock to make a flaky test pass. The repository's `tests/test_order_locks.py` is a useful advanced reading exercise after you understand these invariants.
