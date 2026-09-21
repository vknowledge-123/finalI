# Part One Python From First Principles

## Chapter 1 Programs values and execution

A program is a sequence of instructions operating on values. Python's interpreter executes your source within a process. The process has memory, open files, sockets, and an exit status. A source file on disk is not a running process. Editing the file does not necessarily update a process that already imported it. This explains why a server sometimes needs a controlled restart after deployment.

A variable is a name bound to an object. The object has a type; the name can later refer to another object. `quantity = 5` binds an integer. `symbol = "RELIANCE"` binds text. A string containing `"5"` is not the integer `5`. HTTP often carries text, so boundaries require conversion and validation.

```python
symbol = "RELIANCE"
quantity = 5
entry = 100
latest = 103
pnl = (latest - entry) * quantity
print(f"{symbol}: quantity={quantity}, P&L={pnl}")
```

The output is `RELIANCE: quantity=5, P&L=15`. Change `latest` to `97` and predict the output before running. Change `quantity` to `"5"`; explain the result rather than guessing what Python should do. Multiplication of a string repeats it, demonstrating why syntactically legal code can be semantically wrong.

Learn these basic types: `int` for whole numbers, `float` for approximate real arithmetic, `bool` for truth values, `str` for Unicode text, `bytes` for encoded data, and `None` for absence. `0`, `False`, an empty list, and `None` are all false-like in conditions, but they do not mean the same thing.

One of your application's previous configuration risks illustrates this distinction:

```python
payload = {"pending_retries": 0}
wrong = payload.get("pending_retries") or 1
raw = payload.get("pending_retries")
correct = 1 if raw is None else raw
assert wrong == 1
assert correct == 0
```

Zero is a legitimate request for no retries. Missing means the client did not specify a value. Never use truthiness as a substitute for a domain rule.

**Build:** Ask for a symbol, side, quantity, and price with `input()`. Convert quantity explicitly, reject nonpositive values, and print a paper-order summary. Do not connect to a broker.

**Exit check:** Explain source file versus process, text versus number, and missing versus zero. Include tests for blank input, negative quantity, and malformed price.

## Chapter 2 Environments modules and a repeatable setup

A virtual environment isolates Python packages. It does not isolate the operating system, network, or live broker account. A requirements file records dependencies; a lock or fully resolved constraints record makes installations more reproducible. Security updates still require review and tests.

Create a separate learning repository. In Windows PowerShell:

```powershell
mkdir trading-school
cd trading-school
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
git init
```

On Ubuntu use `python3 -m venv .venv` and `.venv/bin/python`. Use Python 3.12 or later for this book; test SDK compatibility before upgrading production. Do not install teaching dependencies into your deployed application's virtual environment.

Use `python -m package.module` to execute a module with a predictable import context. A package organizes related modules. Imports execute top-level statements once per interpreter import cache, so avoid placing live broker calls, network downloads, or background thread startup at import time.

```python
def main():
    print("Paper environment ready")

if __name__ == "__main__":
    main()
```

The guard prevents `main()` from running merely because another module imports the file. It is particularly important when process workers import modules during startup.

Your project should contain `src/` or one clear package, `tests/`, a dependency declaration, `.gitignore`, and a README with commands you have actually tested. Ignore `.env`, virtual environments, real datasets, and token-bearing screenshots. Commit a `.env.example` containing names and harmless placeholders only.

**Build:** Split your calculator into `money.py`, `risk.py`, and `cli.py`. Import functions rather than duplicating them. Explain what happens if two modules import each other. Resolve the cycle by moving shared types into a smaller module, not by hiding imports everywhere.

**Debug drill:** Deliberately install a package in the wrong interpreter. Compare `python -c "import sys; print(sys.executable)"` with the interpreter used by your service. Learn why "installed successfully" does not imply "available to this process."

## Chapter 3 Conditions loops and collections

Conditions choose a path. Loops apply a rule repeatedly. A list preserves order and permits duplicates. A set represents membership without duplicates. A dictionary maps keys to values. A tuple is an immutable sequence, useful for composite identities such as `(exchange, security_id)`.

Never identify an instrument only by a display name if multiple exchanges can use the same symbol. Never identify a client's position only by symbol when strategy ownership matters.

```python
raw_symbols = [" sbin ", "TCS", "SBIN", ""]
symbols = list(dict.fromkeys(
    item.strip().upper() for item in raw_symbols if item.strip()
))
assert symbols == ["SBIN", "TCS"]
```

This example assumes a list of strings. An API must validate that assumption first. `dict.fromkeys` removes duplicates while preserving first appearance. A set alone would lose that intended order.

Mutation changes an existing object. Assignment does not necessarily copy:

```python
a = {"symbols": ["SBIN"]}
b = a.copy()
b["symbols"].append("TCS")
assert a["symbols"] == ["SBIN", "TCS"]
```

The dictionary copy is shallow; both dictionaries refer to the same inner list. Prefer immutable domain objects and deliberate construction rather than casually deep-copying huge state. Understand `copy.deepcopy`, but ask whether shared mutable state should exist at all.

Avoid modifying a dictionary while iterating through it. Gather keys to remove, then remove them, or construct a new dictionary. Use `enumerate` for positions and values, and `zip(..., strict=True)` when lengths must match. Silent truncation can hide mismatched quote and instrument arrays.

**Build:** Normalize a Chartink-like symbol list, retain input order, record rejected values, and produce one validation result per symbol. Add an explicit maximum count to prevent a huge payload from consuming unlimited memory.

**Exit check:** Explain why configuration dictionaries, subscription sets, and append-only fill lists have different purposes. Demonstrate aliasing with a ten-line program.

## Chapter 4 Functions contracts and precise money

A function separates an operation from the details of its caller. Parameters are inputs; a return value is an output. A pure function has no externally visible side effects and depends only on its inputs. Pure sizing and stop calculations are easier to test than functions that also read Redis, call Dhan, and send Telegram messages.

Floating point is suitable for many measurements, but prices and tick-size alignment benefit from decimal arithmetic. Construct `Decimal` from text, not from a binary float whose rounding error has already occurred.

```python
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

def align_limit(price: Decimal, tick: Decimal, side: str) -> Decimal:
    if not price.is_finite() or not tick.is_finite():
        raise ValueError("Price and tick must be finite")
    if price <= 0 or tick <= 0 or side not in {"BUY", "SELL"}:
        raise ValueError("Invalid order-price inputs")
    rounding = ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    units = (price / tick).to_integral_value(rounding=rounding)
    result = units * tick
    if result <= 0:
        raise ValueError("Aligned price must be positive")
    return result

assert align_limit(Decimal("100.03"), Decimal("0.05"), "BUY") == Decimal("100.05")
```

This rounds an aggressive buy limit upward. It is not a universal rounding rule for every order type. Circuit limits and an absolute slippage cap must still be checked after rounding. If rounding upward violates the cap, reject the request rather than silently paying more. Tick size comes from the correct instrument metadata; do not assume every stock uses `0.05`.

For capital sizing, `floor(capital / price)` respects the stated allocation before fees and margin constraints. Your earlier requirement to buy at least one share even if it exceeds capital is a different policy. Name it `allow_minimum_one_over_budget`; do not conceal it inside arithmetic. With capital 10,000 and price 12,000, strict sizing returns zero and skips. The alternate policy returns one and explicitly exceeds the budget.

Avoid mutable default arguments such as `def add(x, items=[])`. Defaults are evaluated once, not per call. Use `None` and create a list inside. Use keyword-only parameters for safety-sensitive options, and annotations to document expectations. An annotation alone does not validate a web request.

**Build:** Implement strict sizing, optional minimum-one sizing, target, stop, and P&L as pure functions. Test BUY and SELL, zero, missing, infinity, NaN, invalid sides, and very small ticks. Explain the policy differences before optimizing anything.

## Chapter 5 Exceptions files time and logging

Exceptions report that an operation could not meet its contract. Catch the narrow errors you understand. Catching `Exception` at a service boundary can prevent a process from crashing, but it must preserve an explicit failed state and alert operators. `except Exception: pass` converts failure into misleading silence.

Use `try/finally` or a context manager to release files, sessions, and locks. A leaked socket or event loop owns operating-system file descriptors; increasing `LimitNOFILE` may postpone a leak rather than repair it.

```python
import csv
from pathlib import Path

def load_symbols(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield row["symbol"].strip().upper()
```

Use parsers for CSV and JSON. Splitting CSV on commas fails when quoted fields contain commas. Avoid deserializing untrusted pickle data, evaluating expressions from request bodies, or building shell commands from symbols.

Time has multiple meanings. Store event timestamps as timezone-aware UTC. Convert to `Asia/Kolkata` for trading-day rules and display. Use `time.monotonic()` for durations inside one process, because wall clocks can jump. Monotonic values from different machines or restarts are not comparable persistent timestamps.

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

received_at = datetime.now(timezone.utc)
local = received_at.astimezone(ZoneInfo("Asia/Kolkata"))
print(local.isoformat())
```

Install `tzdata` in environments without the IANA timezone database, commonly Windows virtual environments. A weekday check is not an exchange calendar: holidays and special sessions need explicit treatment.

A log should answer what happened, to which entity, in which process, and with which correlation identifier. Log `signal_id`, `intent_id`, sanitized broker error class, and duration. Never log tokens, OTPs, entire environment files, or credential-bearing URLs. Metrics describe counts and latency distributions; logs explain individual events; traces link an operation across services.

**Build:** Read a small CSV, reject malformed records with row numbers, and emit structured summaries. Inject a clock function into your risk rules so tests do not depend on Saturday versus Monday.

**Exit check:** Explain why a current heartbeat timestamp does not prove that a stock's last tick is current.

## Chapter 6 Objects dataclasses and interfaces

An object groups state with behavior. Use it when that grouping makes an invariant easier to preserve. Do not turn every small arithmetic function into a class. Prefer composition: an execution service has a broker and a repository; it is not a subclass of every broker and database.

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Decimal

@dataclass(frozen=True)
class AcceptedOrder:
    broker_order_id: str

class Broker(Protocol):
    def submit(self, intent: OrderIntent) -> AcceptedOrder: ...
```

The protocol expresses what a caller needs, not how a broker fulfills it. Dhan, Kite, and a paper broker can satisfy the same interface with different implementations. Crucially, `AcceptedOrder` contains no invented filled quantity. You need a separate fill or order-status contract.

`frozen=True` prevents assigning new attribute values, but does not recursively freeze an inner list. Use tuples where deep immutability matters. Dataclass equality compares fields; object identity asks whether two names refer to the same instance. Learn `==` versus `is`; compare values with equality and use `is None` for absence.

Class attributes are shared across instances. Accidentally defining `positions = {}` on a class can mix user state. Instance attributes belong to one object, but concurrent tasks can still access them. Encapsulation is useful only when callers cannot bypass the rules by mutating the underlying dictionary.

An abstract base class can enforce method implementation at instantiation. A protocol supports structural typing. Neither proves remote broker behavior. Contract tests bridge your Python interface and real wire-level examples.

**Build:** Write a paper broker that returns acceptance first and emits a fill later. Inject it into a service through a protocol. Test two instances to prove they do not share accounts accidentally.

## Chapter 7 Iterators decorators typing and Python internals

An iterable can produce an iterator; an iterator yields its next item and eventually raises `StopIteration`. A generator function containing `yield` builds such an iterator lazily. This is useful for large historical data files, but it is not free streaming if you immediately wrap the result in `list()`.

Decorators wrap behavior. Use `functools.wraps` to preserve metadata. A retry decorator around arbitrary functions is dangerous: retrying a read can be reasonable, while retrying an ambiguous order submission can place another order. A decorator must respect the side-effect semantics of the function it wraps.

```python
from functools import wraps
from time import perf_counter

def timed(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        start = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            print(function.__name__, perf_counter() - start)
    return wrapped
```

This is a synchronous teaching decorator. Wrapping an `async def` with it would measure coroutine creation, not awaited work. Write a separate async wrapper and test it with an actual awaited delay.

Learn `TypedDict` for dictionary shape, `Literal` for finite choices, `Enum` for named states, `Protocol` for behavior, and generics for reusable containers. Use `mypy` or `pyright` as a feedback tool. Runtime validation is still necessary where JSON enters the process.

Understand closures and late binding: a lambda in a loop can refer to the loop variable's final value. Understand descriptors by studying how `property` controls access. Understand MRO sufficiently to read a traceback involving inheritance. Metaclasses are an advanced extension mechanism, not an expected ingredient in a trading app.

Python implementations manage memory differently. In ordinary CPython, reference counting and cyclic garbage collection both matter. A retained dictionary of tasks can keep objects alive even when you expected them to disappear. `tracemalloc` studies Python allocations; operating-system RSS also includes native allocations and mapped pages. A high RSS does not automatically mean a Python reference leak.

Use `inspect.signature` on an installed SDK method when checking compatibility. Package version, Python version, installed file location, and actual method signature are more persuasive than an example copied from a different release. Do not catch every `TypeError` and resubmit an order with fewer arguments: a bug could have happened after a side effect.

**Build:** Stream a million synthetic ticks through a generator, calculating count and extrema without storing them. Measure peak memory. Add a typed protocol and run your type checker. Then implement and test the async timing wrapper.

## Chapter 8 Algorithms complexity and practical data structures

Big-O describes how resource use grows with input size. It is not a stopwatch. An O(n) scan over 23 sectors can be simpler and faster in practice than maintaining a complex distributed ranking structure. A dictionary lookup is expected O(1), but network latency dominates a Redis lookup compared with an in-process dictionary.

For a rolling window use `collections.deque`, not repeated `list.pop(0)` which shifts elements. For expiring breakout watches, a heap can expose the next expiry efficiently. Keep a dictionary of active watch versions so obsolete heap entries can be discarded safely. A heap alone does not solve cancellation or replacement.

Sorting n sectors is O(n log n). For tiny n, sort the validated snapshot and use stable tie-breaking. For top-k from a large dataset, compare a heap-based O(n log k) approach. First exclude missing and stale values; treating missing as zero invents a ranking.

Practice these algorithms using project problems: binary search over sorted candle timestamps; sliding-window counts for login abuse; hashing for event deduplication; BFS for service-dependency analysis; intervals for session windows; prefix sums for cumulative depth quantity. Learn recursion with a base case, then identify where an iterative version is clearer.

**Worked example:** asks are 50 shares at 100.10 and 80 shares at 100.20. A buy of 100 needs the second level if the snapshot remains available. Cumulative quantities are 50 and 130. The relevant level is 100.20, not last-traded price. This is a linear scan of a short depth array; a search tree is unnecessary.

**Build:** Benchmark a list versus deque for 100,000 oldest-item removals. Implement top-sector ranking with deterministic ties. Explain worst-case space as well as time. Test empty input and repeated timestamps.

### Part One review

Without looking at source, explain mutable defaults, aliasing, Decimal, UTC versus elapsed time, exceptions, context managers, generators, protocols, and O(n log n). Write tests for the sizing function from memory. The official [Python tutorial](https://docs.python.org/3/tutorial/) is a companion reference for language details; this book supplies the project exercises and learning sequence.

