# Part V. Framework Design and API Automation

## Chapter 29. Page Object Model and Framework Design, Part One

### Build after you understand repeated behavior

A framework is an organized set of conventions, helpers and fixtures that make tests easier to write and maintain. It should not require a beginner to understand twenty abstractions before writing one assertion. Start with explicit tests, identify real duplication, then extract small helpers. Page Object Model groups page interactions behind meaningful methods.

```python
from playwright.sync_api import expect

class StrategyPage:
    def __init__(self, page):
        self.page = page
        self.name = page.get_by_label("Strategy name")
        self.qty = page.get_by_label("Quantity")

    def save(self, name, qty):
        self.name.fill(name)
        self.qty.fill(str(qty))
        self.page.get_by_role("button", name="Save strategy").click()
        expect(self.page.get_by_role("status")).to_have_text("Strategy saved")

def test_save_with_page_object(page, lab_url):
    page.goto(lab_url)
    strategy = StrategyPage(page)
    strategy.save("alpha", 2)
    saved = page.request.get(lab_url + "/api/strategies").json()
    assert saved[0]["qty"] == 2
```

This helper contains locators and one meaningful operation. It does not decide every business assertion for the test. Some teams keep all assertions in tests; others allow page objects to assert readiness and successful completion of their own operations. Choose a consistent rule. The important point is not to hide the expected business outcome behind an always-passing helper.

### A small architecture

```text
tests/              scenarios and expected outcomes
pages/              browser interactions
clients/            HTTP transport helpers
data/               synthetic input sets and schemas
conftest.py         lifecycle and dependency wiring
pytest.ini          markers and reporting defaults
artifacts/          ignored evidence, not source
```

Keep domain expectations independent from the implementation under test. Importing the production target calculator to generate every expected target means the test may duplicate the bug rather than detect it. Use hand-worked examples or an independently specified property for critical values.

### Common overengineering traps

A universal `click_element(locator_type, locator, retries, timeout)` wrapper can obscure Playwright's built-in behavior. A deeply inherited BasePage can force unrelated pages to depend on login state. A retry decorator around every operation can duplicate side effects. Prefer direct Playwright calls until a real cross-cutting need justifies a wrapper.

Keep helpers honest about failure. Do not catch every exception and return false if callers forget to assert that result. Raise with useful context. Avoid helpers that call production APIs based on an unvalidated environment variable. A framework should fail closed when its target is not the intended lab/staging environment.

### Practice and checkpoint

Extract one repeated configuration operation into a page object, then change a label in the lab HTML and update one locator. Explain which assertions remain in the test and why. A good explanation distinguishes page readiness from business persistence.

## Chapter 30. Framework Design, Parts Two and Three: Data, Reports and Maintenance

### Configuration must not become a secret leak

Separate nonsecret settings, synthetic datasets and credentials. Nonsecret settings include base URL, browser and timeouts. Test identities and tokens belong in an approved secret mechanism or temporary fixture. Environment variables are a delivery mechanism, not encryption. Never print an entire environment to a CI log.

For a learning suite, validate that the base URL is loopback before any request. Use an explicit environment selection for staging. Do not accept an arbitrary public URL and assume it is safe because the test is called smoke.

```python
from urllib.parse import urlsplit

def require_local_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Learning tests require a local HTTP server")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in test URLs")
    return url
```

This is a teaching safeguard, not a complete SSRF defense for a public service. Production authorization and network policies are separate concerns.

### Reports should answer release questions

JUnit XML serves CI aggregation. Optional `pytest-html` generates an HTML report. Optional `allure-pytest` records richer test steps and attachments; the Allure command-line tool must also be installed to render its results. Consult the [Allure pytest guide](https://allurereport.org/docs/pytest/) for compatible setup rather than assuming the Python package alone includes the CLI.

```bash
python -m pytest --junitxml=artifacts/junit.xml
python -m pytest --html=artifacts/report.html --self-contained-html
python -m pytest --alluredir=artifacts/allure-results
allure generate artifacts/allure-results -o artifacts/allure-report --clean
```

Run the report commands only after installing their optional plugins. Generate each run into a new directory or clean only the dedicated previous report directory. Old result files can inflate counts or confuse failures. Reports should identify commit, environment, start/end time, tests collected, passed, failed, skipped and any expected failures.

### Framework maintenance

Review test code like product code. Require readable names, deterministic setup, meaningful assertions and cleanup. Track flaky tests with owners and deadlines. Quarantine is temporary containment, not deletion of evidence. Keep the fastest useful checks on every pull request and schedule slower browser matrices separately. Prefer a small reliable release gate to thousands of meaningless clicks.

### Practice and checkpoint

Generate JUnit XML from the lab and locate one test name, duration and outcome. Intentionally fail a test in a disposable branch and confirm CI would fail even if report generation succeeds. A report is evidence of a run, not authority to turn a red run green.

## Chapter 31. HTTP, REST and CRUD API Testing

### Understand a request before automating it

An HTTP request has a method, URL, headers and optional body. A response has status, headers and optional body. GET typically reads; POST commonly creates or triggers an operation; PUT commonly replaces a resource; PATCH modifies selected fields; DELETE removes it. These are design conventions, and your API contract determines precise semantics. Do not invent unsupported methods because a course listed them.

The lab uses POST to save a strategy, GET to list strategies, DELETE to remove one and POST to submit synthetic signals/ticks. It does not implement PUT or PATCH. Add them as a deliberate exercise with documented idempotency and partial-update semantics.

### In-process API tests

```python
def test_strategy_crud(client):
    created = client.post("/api/strategies", json={"name": "alpha", "qty": 2})
    assert created.status_code == 201
    assert created.json()["qty"] == 2
    listed = client.get("/api/strategies")
    assert listed.status_code == 200
    assert listed.json()[0]["name"] == "alpha"
    deleted = client.delete("/api/strategies/alpha")
    assert deleted.status_code == 204
    assert client.get("/api/strategies").json() == []
```

FastAPI's TestClient exercises routing, validation and application code without opening a real network socket. It does not test Nginx, TLS, DNS or a deployed process. Use a second layer of network tests for those boundaries. Never parse JSON from a 204 response with no body.

### Playwright APIRequestContext

```python
def test_network_api(playwright, lab_url):
    api = playwright.request.new_context(base_url=lab_url)
    try:
        response = api.post("/api/strategies", data={"name": "api-demo", "qty": 2})
        assert response.status == 201
        assert response.json()["name"] == "api-demo"
    finally:
        api.dispose()
```

This uses real localhost HTTP. Playwright serializes dictionary data as JSON for this request. The [API testing guide](https://playwright.dev/python/docs/api-testing) explains request contexts. An independent request context has its own cookie storage; a page/context-associated request context shares the corresponding browser context's cookies.

### Negative cases and semantics

Test missing fields, unknown fields, wrong types, nulls, negative values and excessive sizes. Assert a non-success status, a stable error contract and no write. A 200 response containing an error object is not automatically success; validate both transport and domain result where an external API uses that pattern. Retrying POST after a timeout requires idempotency or reconciliation because the original request may have succeeded.

### Practice and checkpoint

Send a signal before saving its configuration. Expect CFG_MISSING and no position. Then save configuration and repeat with a new event ID. Explain why checking only status 200 on a create request would miss a wrong saved quantity.

## Chapter 32. Headers, Cookies and JSON Schema Validation

### Headers influence interpretation

`Content-Type` declares a body representation. `Accept` requests a response representation. Authorization headers carry credentials. Cache-control affects whether sensitive or stale responses can be reused. Request/correlation IDs help follow a transaction through services. HTTP header names are case-insensitive, but application tokens and many values are not.

Cookies may carry a session identifier. Test intended Secure, HttpOnly and SameSite attributes in the HTTPS deployment configuration. HttpOnly prevents ordinary script access to the cookie; it does not eliminate CSRF or all XSS risks. SameSite reduces some cross-site request contexts but is not a complete authorization policy. Do not paste live cookies into test source.

### Schema and business meaning are different

JSON Schema checks structure, required fields, types and constraints. It cannot alone establish that the P&L is mathematically correct or that the position belongs to the authenticated user. Pair schema validation with semantic assertions.

```python
from jsonschema import validate

POSITION_SCHEMA = {
    "type": "object",
    "required": ["strategy", "symbol", "qty", "status", "pnl"],
    "properties": {
        "strategy": {"type": "string", "minLength": 1},
        "symbol": {"type": "string"},
        "qty": {"type": "integer", "minimum": 1},
        "status": {"enum": ["OPEN", "CLOSED"]},
        "pnl": {"type": "string"},
    },
    "additionalProperties": True,
}

def assert_position_schema(position):
    validate(position, POSITION_SCHEMA)
```

This schema is for the compact lab, where closed positions preserve their original quantity for reporting. Do not reuse it unchanged for a production schema whose remaining quantity becomes zero or whose states include PARTIAL/EXITING. Schema strictness should distinguish safety-critical omissions from harmless additive vendor fields.

### Contract evolution

Store sanitized fixtures with a source/version note. When a vendor changes a field from numeric to string, decide whether the adapter should normalize it or reject it. Test unknown statuses explicitly. Upgrading a Python SDK can change method signatures even when REST field names remain unchanged; unit mocks without a spec may miss this. Record installed package versions in compatibility reports.

### Practice and checkpoint

Remove `status` from a lab position response and observe schema failure. Then set `pnl` to a wrong but valid string. Schema should pass, showing why a separate arithmetic assertion is necessary. Write the second assertion using independently calculated expected P&L.

## Chapter 33. Authentication, Email Verification, TOTP and Authorization

### Different credentials solve different problems

Basic authentication sends a username/password representation in a header and requires transport protection; encoding is not encryption. Bearer-token authentication grants access to whoever holds a valid token. API keys identify an integration according to the service's policy. OAuth-style delegated flows involve redirects and token issuance, with details specific to the provider. Do not assume every provider implements every OAuth feature.

Recipe for a **local authenticated training endpoint you add**, not the current unauthenticated lab:

```python
def authorized_context(playwright, base_url, synthetic_token):
    return playwright.request.new_context(
        base_url=base_url,
        extra_http_headers={"Authorization": f"Bearer {synthetic_token}"},
    )
```

The caller must dispose of this context. Test no token, malformed token, expired token, wrong audience/account and valid token. Never derive authorization solely from `user_id` in the query string. A login form hidden in the UI is not protection for direct API calls.

### Admin setup and email verification

A single-admin application has a first-run ownership problem: who is allowed to become the administrator? Require a controlled bootstrap mechanism, such as an allowlisted email plus a backend-issued one-time setup capability. Merely being first to open a public URL is unsafe. Test concurrent setup requests: only one may claim ownership atomically. Changing an environment email should not silently transfer an already configured account without a documented reset procedure.

For email verification, use a local mail sink or fake mail adapter, never a real customer's mailbox. Verify a random, expiring, single-use token; bind it to the intended purpose and identity. Store an appropriate protected token representation. Test reuse, expiry, wrong user, changed email and throttling. Responses should avoid unnecessary account enumeration. Email ownership verification is not automatically a second authentication factor and does not prove the person's legal identity.

### TOTP lifecycle

TOTP uses a shared secret and time-based codes. A test should cover enrollment, confirming the first code, required second factor on later logins, incorrect code, expired code, allowed clock-skew window, replay policy, rate limiting and administrative reset. Use a synthetic secret and an injected clock. Protect the stored secret because validation needs it; password hashes and encrypted TOTP secrets serve different purposes.

For password handling, rely on maintained password-hashing libraries and current security guidance rather than inventing encryption. Test that responses and logs do not expose plaintext passwords, hashes, recovery codes or QR payloads. A password reset or TOTP reset should revoke relevant sessions according to a clear policy.

### Redirect-based broker login tests

Mock consent generation and token consumption. Test success, denial, missing token, replay, mismatched account and session binding. Validate redirects against an allowlist and protect against login CSRF using the mechanisms supported by the provider and application session. Do not fabricate state parameters a provider does not return; inspect the real documented flow and test the application's actual binding strategy.

### Practice and checkpoint

Create an authentication test matrix with five positive and ten negative cases. Include direct API access before login and after logout. The compact lab intentionally lacks authentication; this chapter is an extension assignment and production test-design guide, not a claim that its public-looking test UI is secure to deploy.

## Chapter 34. Frontend-to-Backend Integration and Security Testing

### Follow one fact across the system

A powerful integration test creates a configuration through the browser, verifies it through the API, submits a synthetic signal through the API, then verifies the resulting position through the browser. This crosses meaningful boundaries while avoiding repetitive UI setup for every state. The companion lifecycle test follows this pattern and checks a target exit after a synthetic tick.

Write the chain before coding:

```text
UI form qty=2 -> POST configuration -> backend stores qty=2
API signal -> synthetic position qty=2 -> GET positions
UI refresh -> OPEN row -> API tick at target
UI refresh -> CLOSED/TARGET row with P&L 2.00
```

Then introduce one failure at a time: invalid configuration, missing configuration, duplicate event, unknown symbol, price unavailable, delayed candle, order pending or rejected. Assert both the visible explanation and the absence of incorrect state. A rejected order should not acquire a full filled quantity because the UI wanted a green row.

### Security is a collection of boundaries

Use the [OWASP Web Security Testing Guide](https://owasp.org/projects/web-security-testing-guide) as a structured reference. Test only systems you own or are authorized to assess. Begin with access control, input validation, session lifecycle, output encoding, secrets exposure, CSRF defenses and rate limits. Destructive or high-load tests belong in a disposable environment with explicit scope.

For HTML output, seed a harmless string containing markup and assert it renders as text rather than executable DOM. For database queries, test punctuation through parameterized data paths. For webhook authentication, test absent and wrong secrets, replay policy and payload limits. Mask secrets in URLs because reverse-proxy logs can capture query strings.

### Deployment boundaries

An in-process test cannot detect missing Nginx WebSocket upgrade headers. A deployment test should check the external HTTPS endpoint, authenticated browser connection and actual message delivery. A listening port is not proof of a working handler; an empty upstream reply can still cause a 502. Inspect application logs, reverse-proxy logs and health state together, with timestamps converted consistently.

Do not automatically reset production Redis or restart all services when a test fails. Preserve evidence, understand the failure domain, and protect open positions according to the operational runbook. Test code must not be granted broad production authority merely because it is convenient.

### Practice and checkpoint

Write a test for a failed configuration save followed by page reload. Expected: no success message and no falsely persisted new values. Then test that an earlier skipped alert remains skipped after a later position for the same symbol appears. These tests connect frontend correctness to backend facts rather than treating screenshots as sufficient evidence.
