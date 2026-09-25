# Part II. Python for Test Engineers

## Chapter 6. Environment, Interpreter and Your First Program

### Build an isolated workspace

A Python interpreter executes Python instructions. A virtual environment gives a project its own package installation directory. It does not create a security sandbox or prevent network calls. Keep the learning environment separate from the trading server and its dependencies. Open a terminal in `docs/testing-book/lab`, not the production application directory, and follow the lab README.

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
.venv\Scripts\python -m pytest -m "not browser" -q
```

On Linux or macOS use `python3` to create the environment and `.venv/bin/python` afterward. Calling the environment's interpreter directly avoids activation-policy problems on Windows. `python -m pip` connects installation to a known interpreter; a bare `pip` can belong to a different installation. Check `python --version`, `python -m pip --version` and `python -c "import sys; print(sys.executable)"` when debugging installation issues.

Create `hello.py` in your own practice directory:

```python
symbol = "TEST"
quantity = 2
print(f"Paper symbol: {symbol}; quantity: {quantity}")
```

The first two lines bind names to objects. The final line prints a formatted string. A comment starts with `#`; use it to explain a decision, not repeat the instruction. Python uses indentation to delimit blocks. Four spaces is the conventional indentation unit; mixing tabs and spaces creates avoidable errors. A syntax error means the interpreter cannot parse the program. An exception means valid syntax reached an invalid operation at runtime.

### Organize files early

Use small modules: domain calculations, API application and tests. Do not name a practice file `json.py`, `pytest.py` or `decimal.py`, because it can shadow a package with that name. Avoid hardcoded machine paths. Use `pathlib.Path` relative to the module or fixture. A module can expose functions for import and reserve command-line actions under `if __name__ == "__main__":`.

The lab requirement ranges describe supported versions, not a reproducible deployment lock. After a successful learning run, record installed versions in a lock or freeze file. Upgrade in a branch, run tests, then review behavior. Never fix a learning import error by upgrading all packages in the production virtual environment.

### Practice and checkpoint

Print the interpreter path, create a script with one indentation error, observe the traceback and fix it. Explain why activating an environment is convenient but optional when its executable path is explicit. The answer is that activation changes shell lookup; it does not change what Python itself means.

## Chapter 7. Values, Operators, Strings and Formatting

### Types express different meanings

Integers represent counts, strings represent labels, booleans represent decisions, and `None` represents an absent value. A float approximates many decimal fractions. Financial calculations often benefit from `Decimal` constructed from a string. `Decimal(0.1)` preserves a float approximation; `Decimal("0.1")` represents the intended decimal value.

```python
from decimal import Decimal, ROUND_HALF_UP

entry = Decimal("427.50")
target = entry * (1 + Decimal("1") / 100)
stop = entry * (1 - Decimal("0.5") / 100)
print(target)  # 431.775
print(target.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
print(stop.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
# Display: 431.78 and 425.36
```

Display rounding and order-price tick rounding are different requirements. A two-decimal display does not prove that an order is a valid multiple of 0.05. For an aggressive BUY limit, rounding down can make execution less likely; for a SELL limit the direction reverses. Test the policy explicitly rather than sprinkling `round()` calls across the adapter.

### Operators and truthiness

`+`, `-`, `*`, `/`, `//` and `%` perform arithmetic; `==`, `<` and `>=` compare values; `and`, `or`, `not` combine conditions. `=` assigns and `==` compares. `is` checks object identity, so use `value is None` but do not use `is` to compare symbol strings.

```python
config = {"retries": 0}
wrong = config.get("retries") or 1
correct = config.get("retries", 1)
assert wrong == 1
assert correct == 0
```

Zero, empty strings and empty containers are false in a Boolean context. That is useful for some checks but wrong when zero is a valid setting. Missing, null, zero and empty text should be separate test inputs. A `bool` arriving where strict quantity is expected also deserves a test.

### Strings, slicing and normalization

Strings are immutable sequences. `text.strip()` returns a new value; it does not edit `text` in place. `upper()`, `lower()`, `split()`, `join()` and slicing help normalize test data. Normalize according to a contract: collapsing every punctuation mark can make distinct alert names collide.

```python
raw = " TEST, DEMO ,TEST "
symbols = list(dict.fromkeys(s.strip().upper() for s in raw.split(",")))
assert symbols == ["TEST", "DEMO"]
assert "CONFIG"[0:3] == "CON"
```

An f-string inserts values into readable messages. Use descriptive assertion messages, but never include secrets. Format P&L separately from business arithmetic. `f"{float_value:.2f}"` is a display decision, not proof that the underlying number is exact.

### Practice and checkpoint

Calculate a SELL target and stop at entry 100, target 2%, stop 1%. Expected: target 98, stop 101. Explain why `bool("false")` is true: it is a nonempty string. APIs should validate booleans rather than trust arbitrary truthiness.

## Chapter 8. Conditions, Loops and Collections

### Follow the decision path

An `if` selects a branch; `elif` checks later alternatives; `else` handles the remainder. Order matters when multiple conditions are true. In an entry decision, a missing configuration may take precedence over a sector error. Write down this precedence rather than relying on the incidental order of branches.

```python
def entry_reason(config, kill, already_open):
    if config is None:
        return "CFG_MISSING"
    if not config["enabled"]:
        return "DISABLED"
    if kill:
        return "KILL_SWITCH"
    if already_open:
        return "ALREADY_OPEN"
    return "EVALUATE"
```

Test each return path and at least one overlapping condition. This function is deliberately a simplified decision exercise, not the full production gate. It gives a beginner a small behavior they can understand completely before working with queues and brokers.

### Lists, tuples, sets and dictionaries

A list preserves order and permits duplicates; use it for an ordered stream of events. A tuple represents a fixed grouping, such as `(strategy, symbol)`. A set represents unique membership; use it for subscribed symbols, but do not assume display order. A dictionary maps keys to values; use a composite key to prevent one strategy's position from being confused with another's.

```python
positions = {
    ("alpha", "TEST"): {"qty": 2},
    ("beta", "TEST"): {"qty": 5},
}
assert positions[("alpha", "TEST")]["qty"] == 2
subscribed = {"TEST", "DEMO", "TEST"}
assert len(subscribed) == 2
```

Mutability creates test leaks. Assigning `other = config` creates another reference to the same dictionary. `config.copy()` is shallow: nested lists are still shared. Use fixture factories or a deliberate deep copy for nested mutable test data. Do not mutate a dictionary while iterating over its keys without planning how iteration remains valid.

### Iteration and comprehensions

Use `for` to process a finite collection, `enumerate` when you need indices, and `zip` when two sequences have a meaningful pairing. `zip` normally stops at the shortest sequence, which is dangerous for malformed OHLC arrays. Validate equal lengths or use strict pairing where supported.

```python
prices = [100, 101, 102]
quantities = [2, 3, 4]
notionals = [price * qty for price, qty in zip(prices, quantities, strict=True)]
assert notionals == [200, 303, 408]
```

A `while` loop needs a termination condition. Polling a broker forever can block a worker. Tests should cover a successful condition, a timeout, cancellation and a malformed response. Comprehensions are concise but should not hide side effects or complex state transitions. Prefer a readable loop when every iteration needs logging and recovery.

### Practice and checkpoint

Given sector percentages AUTO=1.5, IT=-2.0, METAL=-0.5, select two LONG and two SHORT candidates. Expected LONG order: AUTO then METAL; SHORT: IT then METAL. This is rank ordering; whether positive-only or negative-only membership is additionally required must be a separate business rule.

## Chapter 9. Functions, Scope, Modules and Packages

### A function is a testable contract

A function receives inputs, performs a responsibility, and returns a result or raises an exception. Prefer explicit inputs to reading global state. `calculate_pnl(entry, ltp, qty, side)` is easier to test than a function that reads the current broker session, global position and wall clock. A pure function has no observable side effects and produces the same output for the same input.

```python
from decimal import Decimal

def pnl(entry: Decimal, ltp: Decimal, qty: int, side: str) -> Decimal:
    if side not in {"BUY", "SELL"} or qty <= 0:
        raise ValueError("Invalid position")
    direction = 1 if side == "BUY" else -1
    return (ltp - entry) * qty * direction

assert pnl(Decimal("100"), Decimal("101"), 2, "BUY") == 2
```

Type hints document expectations and help static tools, but Python does not automatically enforce them at runtime. Validate untrusted boundaries. Avoid mutable default arguments such as `def collect(items=[]):`; the same list is reused between calls. Use `None` and construct a fresh list inside the function.

### Scope and dependencies

Names can be local to a function, enclosed in an outer function, global to a module or built-in. `global` and `nonlocal` change assignment behavior, but frequent global mutation makes tests harder to isolate. Pass dependencies instead: a store, broker adapter, clock or notifier. A closure can capture a fake clock for deterministic expiry tests.

```python
def is_expired(expires_at, now):
    return now >= expires_at

assert not is_expired(120, 119.9)
assert is_expired(120, 120)
```

The function does not sleep. It tests the rule, while another integration test verifies scheduling calls the rule. Separating policy from scheduling makes failures easier to interpret.

### Module layout

The lab has `domain.py`, `server.py` and `tests/`. The server imports the model; tests import both layers as needed. Larger packages may have `domain/`, `adapters/`, `services/` and `tests/`. Avoid circular imports by depending on small interfaces and keeping startup wiring outside domain code. Importing a module should not place orders, start services or modify databases.

Use `__init__.py` when intentionally creating a regular package. Run tests from a documented directory so imports are reproducible. Do not repeatedly modify `sys.path` throughout a framework to compensate for an unclear package design. In this companion lab, running `python -m pytest` from the lab directory makes its modules importable.

### Practice and checkpoint

Refactor a function that reads `datetime.now()` into one accepting `now`. Write tests before, at and after expiry. The key insight is that dependency injection is not a large framework: passing one argument can make a hidden dependency visible.

## Chapter 10. OOP, Inheritance, Polymorphism and Abstraction

### State and behavior together

A class defines a kind of object. An instance contains its own state. A position has identity, quantities and prices; methods such as `mark()` update it according to rules. Encapsulation means changes go through meaningful operations rather than arbitrary mutations from every caller. A leading underscore is a convention for nonpublic attributes, not a security boundary.

The lab's `Position` dataclass creates initialization and representation methods. `from_fill()` is a class method because it constructs an instance from a meaningful event. `pnl` is a property computed from current state. Read `lab/domain.py` and trace which inputs affect each field. The lab deliberately has fewer rules than the real engine.

### Program to a small interface

```python
from typing import Protocol

class Broker(Protocol):
    def order_status(self, order_id: str) -> dict: ...

class PaperBroker:
    def order_status(self, order_id: str) -> dict:
        return {"id": order_id, "status": "PENDING", "filled": 0}

def confirmed_quantity(broker: Broker, order_id: str) -> int:
    return broker.order_status(order_id)["filled"]

assert confirmed_quantity(PaperBroker(), "paper-1") == 0
```

Polymorphism means the caller works with different implementations through the same contract. A production adapter and a fake can both expose `order_status`. The fake above is intentionally minimal; a realistic contract test must include rejection, partial fill and unknown status. An abstraction is useful when it hides vendor differences without erasing essential semantics.

Inheritance represents an "is a" relationship and can reuse behavior. Composition gives an object collaborators, such as a page object receiving a Playwright `Page`. Prefer composition for test services and page objects unless inheritance genuinely simplifies shared behavior. A giant `BaseTest` containing login, database setup, retries and screenshots can make every test depend on hidden setup.

### Test behavior, not implementation trivia

Do not assert that a private helper was called three times merely because it currently is. Assert that duplicate order updates do not increase filled quantity. Interaction assertions are useful when the interaction itself is the requirement, such as "broker submission must not occur while kill switch is on." An object-oriented design should make important boundaries inspectable, not require tests to reach into every private field.

### Practice and checkpoint

Write two notifier implementations: one records messages in a list, one raises a simulated delivery failure. Inject each into a service. Explain why notification failure must not undo an already confirmed broker fill. The correct boundary separates execution state from optional notification delivery.

## Chapter 11. Files, JSON, CSV and Excel Test Data

### Files need a lifecycle

Use a context manager so the file closes even after an exception. Prefer explicit UTF-8 encoding for text. Relative paths depend on the working directory; fixture data should be located relative to the test module or a configured data directory. Pytest's `tmp_path` provides a disposable directory for one test, avoiding accidental edits to real files.

```python
import json
from pathlib import Path

path = Path("cases.json")
cases = [{"side": "BUY", "entry": "100", "ltp": "101", "pnl": "2"}]
path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
loaded = json.loads(path.read_text(encoding="utf-8"))
assert loaded == cases
```

JSON is a structured interchange format. It has arrays, objects, strings, numbers, booleans and null, but not Python Decimal or datetime objects. Decide an explicit serialization policy. The lab represents monetary values as strings so tests can reconstruct Decimal precisely. Do not use `eval()` to read test data; it executes code.

### CSV and Excel

CSV is simple and reviewable in Git. Use `csv.DictReader`, not `line.split(",")`, because quoted fields can contain commas. Convert strings at the boundary and validate required columns. Preserve leading zeroes when a field is an identifier rather than a number.

```python
import csv
import io

stream = io.StringIO("symbol,quantity\nTEST,2\nDEMO,3\n")
rows = list(csv.DictReader(stream))
assert int(rows[0]["quantity"]) == 2
```

Recipe, with optional `openpyxl` installed in the lab environment:

```python
from openpyxl import load_workbook

book = load_workbook("cases.xlsx", read_only=True, data_only=True)
try:
    sheet = book["Entries"]
    for symbol, qty in sheet.iter_rows(min_row=2, values_only=True):
        assert isinstance(symbol, str)
        assert isinstance(qty, int) and qty > 0
finally:
    book.close()
```

`data_only=True` reads cached formula results; it does not calculate Excel formulas. Missing caches can produce `None`. Spreadsheet data is not automatically authoritative. Review its expected values independently, version it, and avoid duplicating the same calculation in both application and expected-data generator.

### Practice and checkpoint

Create CSV cases for zero, negative and fractional quantities. Test a malformed row and a missing column. A good data loader fails with the file and row context, without echoing confidential content. Never include real customer account exports in a beginner test repository.

## Chapter 12. Exceptions, Logging and Debugging

### Exceptions carry meaning

An exception interrupts normal flow and travels up the call stack until handled. Catch only where you can recover, add useful context or translate to a boundary response. `except Exception: pass` makes failures disappear and can leave inconsistent trading state. A `finally` block releases resources; it does not prove the operation succeeded.

```python
import json

def decode_quote(raw):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Quote response was not JSON") from exc
    if "ltp" not in data:
        raise ValueError("Quote missing ltp")
    return data["ltp"]
```

Exception chaining preserves the original cause. In tests, assert both the error category and the absence of unwanted side effects. A malformed quote should not create a position. An HTML 502 page should not become a fake price of zero. A catch-all API handler can produce a safe JSON error envelope, but it must still log a redacted traceback and return an appropriate non-success status.

### Debug from evidence

Read a traceback from the last exception upward. Identify the first relevant project frame, the input that reached it, and the earliest violated assumption. Distinguish root cause from consequences: a missing socket attribute during cleanup may follow failure to create an event loop because the process exhausted file descriptors. Fixing the cleanup message alone would miss the leak.

Use structured fields: service, user identifier, request ID, symbol, order correlation ID, operation and outcome. Never log access tokens, passwords, TOTP seeds or full Redis URLs. A successful response status alone is insufficient evidence of correct content. Logs should make decisions explainable without exposing credentials.

### Test failures versus application failures

A strict locator matching two rows is usually a test selector problem. A timeout after clicking Save may be application latency, network failure, invalid data or a selector waiting for the wrong message. Inspect the response and trace before increasing timeout. A 50 ms performance assertion on a busy laptop can fail despite correct concurrency; coordinate workers with events to test ordering, and measure performance in a controlled benchmark.

### Practice and checkpoint

Make `decode_quote` receive HTML, empty text and JSON without `ltp`. Write three exception assertions. Then write one successful case. Explain why replacing all failures with `{}` would reduce observability and may turn a broker outage into a misleading "no data" condition.

## Chapter 13. Advanced Python for Reliable Automation

### Time, resources and structured data

Use timezone-aware datetimes for exchange sessions and UTC for portable event timestamps. Use a monotonic clock for elapsed durations; wall-clock corrections should not extend a timeout. A dataclass gives named fields; an enum gives a controlled set of states; a generator yields values lazily. These features help express contracts, but do not eliminate validation.

```python
from datetime import datetime
from zoneinfo import ZoneInfo

ist = ZoneInfo("Asia/Kolkata")
alert_time = datetime(2026, 9, 23, 10, 30, 3, tzinfo=ist)
assert alert_time.utcoffset().total_seconds() == 19800
```

On systems without an IANA timezone database, install `tzdata` in the lab environment. Never simulate IST by changing a UTC timestamp's label without converting the represented instant.

### Async is cooperative, not magic

An `async def` function returns a coroutine. `await` allows other tasks to progress when the awaited operation cooperates. Calling a synchronous slow broker SDK directly from the event loop can still block every request. Use a bounded thread worker for blocking I/O. CPU-heavy calculations may need a process rather than more async tasks. A semaphore bounds concurrent work, but you also need admission limits when producers can outpace consumers.

```python
import asyncio

async def collect_two(fetch):
    first, second = await asyncio.gather(fetch("TEST"), fetch("DEMO"))
    return first, second

async def paper_fetch(symbol):
    await asyncio.sleep(0)
    return {"symbol": symbol, "ltp": 100}
```

This example shows concurrent scheduling, not a speed benchmark. Shared mutable state still needs ownership rules. Test cancellation and cleanup: an interrupted task should release its local resources, but a broker request already sent may still execute remotely. Cancellation is not proof that an external order was cancelled.

### Dependency injection and clocks

A deterministic test controls time, randomness and external responses. Inject a clock with `now()`, a broker with scripted results, and a store with an isolated namespace. For lease tests, advance fake time during controlled renewals; for concurrency, hold a fake market-data call on an event while checking an order worker completes. Avoid hundreds of `sleep(0.01)` calls that only work on one machine.

Decorators wrap behavior; pytest fixtures and marks use decorators. Context managers implement setup/cleanup; fixtures often use `yield` for the same lifecycle. Learn these mechanisms by writing small examples before building a large framework.

### Practice and checkpoint

Build a fake clock and a breakout watch that expires at 120. Advance to 119 and 120 without sleeping. Explain why deterministic time tests do not replace a separate staging test of actual scheduling delays. One proves policy; the other observes real runtime behavior.
