# Part Four Frontend Integration and Software Testing

## Chapter 23 Browser fundamentals for a backend developer

HTML describes content and form controls. CSS controls layout. JavaScript handles events and communicates with your API. The DOM is the browser's current document tree, not the server database. A number displayed on a page may be stale even while the backend is healthy.

Use labels associated with controls, semantic buttons, keyboard focus, and readable validation messages. Disabled controls should express actual unavailable actions. A hidden field is not a security boundary. User IDs and prices submitted by the browser remain untrusted.

Use server-calculated risk values for display. The browser can preview a calculation, but the execution path must validate and calculate again. Prevent duplicate submissions for usability, then enforce idempotency on the server for correctness. Closing a browser tab must not stop server-side risk monitoring.

```javascript
async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new Error(`Unexpected server response (${response.status})`);
  }
  if (!response.ok || data?.ok === false) {
    throw new Error(data?.detail || `Request failed (${response.status})`);
  }
  return data;
}
```

This handles proxy-generated non-JSON without exposing the full raw response. Production also needs timeout/cancellation, session-expiry behavior, and accessible error display. Retrying a GET differs from retrying a square-off POST.

Render untrusted symbols and error messages with `textContent`, not HTML concatenation. Avoid placing secrets into URLs, DOM attributes, analytics events, or browser local storage. A webhook copy button can use the Clipboard API under its browser security requirements and provide a clearly labeled fallback when unavailable.

For mobile, test the actual layout at narrow widths. A table may scroll horizontally inside its container; the entire page should not overflow. Use one-column forms at small sizes, predictable grouping, and touch-sized controls. Do not make a modal so tall that its Save button becomes inaccessible. Preserve a visible stale-data indicator even on mobile.

**Build:** Implement configuration save/reload with server validation errors shown next to the relevant field. Submit zero retries and verify zero survives reload. Display an offline banner without removing the last known position data or pretending it is fresh.

## Chapter 24 Unit testing as executable design

A unit test checks a small behavior with controlled inputs. It should explain a rule, not merely execute lines. Arrange the state, act once, and assert the important outcome. Test names should describe behavior such as `test_long_stop_never_decreases` rather than `test_function_7`.

```python
from decimal import Decimal
import pytest

@pytest.mark.parametrize("capital,price,expected", [
    ("10000", "2000", 5),
    ("10000", "12000", 0),
    ("0", "100", 0),
])
def test_strict_sizing(capital, price, expected):
    quantity = int(Decimal(capital) // Decimal(price))
    assert quantity == expected
```

The parameterized example checks a mathematical policy; your production function also needs input validation and fee policy. For risk rules, use a fake clock and explicit ticks. Do not rely on today's date or a running exchange.

Fixtures provide setup and cleanup. A test database fixture should start empty and clean itself afterward. A fake broker should record calls and allow scenarios: reject, partial fill, delayed fill, timeout after acceptance, and unknown response. A mock that always returns `success` cannot test reconciliation. [pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html)

Use property-based tests for invariants: aligned prices are multiples of tick size; a long stop is monotonic; confirmed fill deduplication is idempotent; position quantity never becomes negative. Use mutation testing to ask whether changing `>=` to `>` makes a test fail. Coverage measures executed lines, not the truth of the expected result.

**Build:** Write a failing test before fixing a bug. For the zero-default bug, assert `retry_count == 0` after a save/reload. For quantity safety, send the same fill twice and assert the ledger is unchanged after the second delivery.

## Chapter 25 API and real integration tests

Integration tests check collaborating components. Use an actual disposable Redis or PostgreSQL instance when testing transactions, expiry, scripts, locking, or consumer recovery. An in-memory fake is useful for fast tests but may not reproduce atomicity, serialization, network failures, or database constraints.

Test a full vertical slice: HTTP request, validation, transaction, queued work, simulated broker event, ledger update, and response query. Assert stored evidence as well as status codes. A route returning 200 while discarding a setting is still broken.

For the bundled lab, from its directory:

```bash
python -m pytest -q tests/test_domain.py tests/test_api.py
```

The lab creates a fresh temporary SQLite database per test. It never imports your live application or broker SDK. Later, repeat the same contract against PostgreSQL and Redis implementations rather than assuming the lab proves their behavior.

API cases must include unauthenticated and unauthorized access, malformed bodies, unknown configuration, duplicate event IDs, conflicting reuse of an event ID, zero values, oversized input, expired sessions, and missing dependencies. Test that an error response does not include a Redis password or token-bearing URL.

For broker contract tests, pin an SDK version, intercept its HTTP transport, and validate exact serialized requests and parsed responses. Keep fixture metadata identifying API version, instrument, and capture date. Scrub credentials. A contract fixture is not a live sandbox; it detects changes you have modeled, not every future provider behavior.

**Build:** Run the same signal twice and prove only one paper position exists. Reuse its ID with a different payload and require conflict. Save a config, restart the app against the same test database, and retrieve it. Inject a storage error and ensure the API does not claim a durable enqueue succeeded.

## Chapter 26 Playwright with Python

Playwright controls real browser engines. It can verify that a person can operate the UI and that the UI interacts correctly with the backend. Use accessible role and label selectors where possible. Avoid brittle selectors based on the fifth nested `div` or arbitrary sleep calls.

Install its Python pytest plugin in the learning environment, then install a browser:

```bash
python -m pip install pytest-playwright
python -m playwright install chromium
```

The included browser test starts a paper-only local API subprocess itself, saves a strategy through the form, sends a simulated signal through the API, verifies a rendered open position, applies a target-reaching simulated tick, and verifies a closed position. This is frontend-to-backend integration, not a screenshot of static HTML.

```python
from playwright.sync_api import expect

def verify_strategy_form(page, base_url):
    page.goto(base_url)
    page.get_by_label("Strategy name", exact=True).fill("Practice")
    page.get_by_role("button", name="Save strategy", exact=True).click()
    expect(page.get_by_role("status")).to_contain_text("Saved")
```

This illustrative function assumes the lab form's other fields retain valid defaults. Use Playwright's web-first assertions, which retry until the condition or timeout, rather than checking DOM text immediately after a network request. [Playwright Python pytest guidance](https://playwright.dev/python/docs/test-runners)

For production authentication tests, use an isolated test administrator and a fake email inbox. Generate TOTP from a test seed without logging production seeds. Do not disable authentication in the only browser suite; keep a separate suite for login, CSRF, session expiry, and authorization.

Capture traces and screenshots on failure. Traces may include requests, cookies, and page content, so treat them as sensitive artifacts with short retention. Run desktop and mobile viewports; also use Firefox or WebKit for cross-engine coverage when your support policy requires it.

**Build:** Add browser cases for expired session, a 502 HTML response, WebSocket disconnect, zero retries, form reload, and horizontal overflow. Simulate backend messages rather than opening real broker connections. Verify both visible output and backend records.

## Chapter 27 Fault injection performance and security tests

Reliable software is tested under failure, not only with fast successful mocks. Inject delayed responses, malformed JSON, lost order updates, duplicate fills, Redis disconnects, clock boundaries, worker termination, and cancellation during an in-flight submission. Record the expected final state before running the experiment.

| Fault | Required behavior |
|---|---|
| Submission response lost | Mark unknown and reconcile; no blind new order |
| Consumer dies after claim | Recover only with ownership and idempotency rules |
| Ticks arrive late | Preserve receipt time and reject stale entry evidence |
| Telegram stalls | Trading path continues; notification is bounded |
| Dashboard disconnected | Server monitoring continues; browser shows degraded state |
| Database unavailable | No false success for unpersisted critical work |
| Two admin setups race | Exactly one bootstrap succeeds |
| Broker token rotates | Feed owner reloads credentials without process-wide guesswork |
| CNC missing from holdings | Preserve unresolved ownership and investigate positions |

Measure end-to-end timestamps: received, validated, queued, claimed, priced, submitted, acknowledged, filled, and displayed. A two-millisecond function is irrelevant if its job waited five seconds in a queue. Use percentiles and error rate under a stated workload; never claim "superfast" without a reproducible measurement.

Define a load scenario before running it: for example, 50 paper signals per minute, 100 watched instruments, 1,000 synthetic ticks per second, and three dashboards. These are exercise inputs, not measured capacity of your app. Record machine size, software versions, duration, queue maximum, event-loop lag, RSS, descriptor count, and broker calls. Test a short burst and a long soak.

Do not load-test live broker order endpoints. Use a fake transport with realistic delays and errors. Avoid overwhelming a client's production VM with local stress tools during market hours.

Security tests should try cross-account IDs, session replay after logout, TOTP replay, CSRF, HTML in symbol names, malformed content types, arbitrary redirect destinations, path traversal in exports, secret-bearing error responses, and unauthorized WebSocket origins. Scan dependencies, but distinguish a vulnerability finding from a verified exploit in your deployment.

**Build:** Write a failure matrix with evidence links. Stop an isolated worker during every important state transition. Explain why `systemctl active` and a clean error grep cannot prove business correctness.

## Chapter 28 CI and evidence based release decisions

Continuous integration should run formatting, static checks, unit tests, integration tests, contract tests, and selected browser tests on every proposed change. Separate fast feedback from slower scheduled fault and soak tests. Treat flaky tests as defects; endless retries can hide a real race.

Build one immutable artifact and promote it between environments. Pin dependencies, record the Python and SDK versions, and review changes in transitive packages. Include a vulnerability scan and secret scan, but do not make their green output your entire security argument.

A release report should say what changed, which tests ran, their environment, what was not tested, known limitations, migration steps, and rollback conditions. For example, "paper broker tested; no live exchange validation" is honest. "All bugs fixed" is not a defensible test result.

Database migrations need compatibility planning. Add new optional fields before making readers depend on them. Deploy producers and consumers that tolerate the transition. Avoid deleting old data while older workers may still need it. Rollback of code and rollback of state are different operations.

For a trading deployment, pause new entries during ownership handover, preserve exit monitoring, drain or reconcile in-flight intents, restart with a known version, and check feed and queue readiness before resuming. Do not deploy two live execution owners as a conventional blue-green test without explicit fencing and account isolation.

**Build:** Write a CI workflow for the learning repository and deliberately introduce a failed zero-value test, a syntax error, and an accidental secret placeholder matching your scanner rule. Verify each fails for the intended reason. Present a release checklist that another person can follow without knowing your terminal history.

