# Part I. Learn to Think Like a Tester

## Chapter 1. The Application, the Learner and the Safety Boundary

### What you are learning

A tester turns a claim into an experiment. A developer says, "A target hit closes the position." The tester asks: Which price? Which timestamp? Which position? Does a sell request mean the sell filled? What happens if the response is lost? This book starts with those questions before teaching tools. Tools repeat instructions quickly; they do not choose the correct expectation for you.

Our case study is an alert-driven trading application. An administrator configures a named strategy. An external scanner sends candidate symbols. The application checks configuration, time, direction, sizing and optional sector/breakout rules. An execution component contacts a broker. Reconciliation compares the application's records with broker evidence. A dashboard displays positions and P&L. Market-data and browser WebSockets are different connections: the dashboard can receive app messages even when broker prices are stale.

Draw the important boundaries before writing tests:

```text
Scanner -> HTTP intake -> durable work -> decision -> broker adapter
                                            |             |
                                      configuration   order evidence
                                            |             |
Browser <- API / dashboard feed <- positions <- reconciliation
                                     ^
Broker market feed -> normalized ticks -> risk decisions
```

Each arrow can lose, delay, duplicate or corrupt information. "The process is running" tests none of those arrows. "Subscription sent" is not the same observation as "a fresh tick was consumed for this symbol." Good test names describe the arrow and the guarantee being tested.

### Three kinds of examples

The **case study** refers to actual project modules such as `app/main.py`, `app/trade_engine.py`, `app/dhan_broker.py`, `app/redis_store.py` and `app/services/breakout_monitor.py`. Explore them read-only first. The **lab** is the small companion application in `lab/`; it cannot contact a broker. A **recipe** is a focused example to adapt after providing the stated HTML, fixture or interface. A recipe is not a promise that the existing dashboard contains that exact label or endpoint.

You do not need market experience. A BUY position gains before costs when price rises; a SELL position gains before costs when price falls. A target is a desired exit threshold. A stop is a protective exit threshold. A tick is a price update, not proof of an executable price. A fill is an actual executed quantity. The lab assumes synthetic fills so you can study software mechanics; the production system must confirm execution.

### Your first experiment

For a synthetic BUY of 2 shares at 100, target 1%, stop 0.5%: expected target is 101, stop is 99.50. A tick of 100.75 produces unrealized P&L 1.50 before costs. A tick of 101 closes the simplified lab with P&L 2.00. Write these numbers on paper before running a test. That independent calculation is your **oracle**, the source of expected results.

Keep synthetic symbols, localhost URLs and disposable data. Never run automation against a client IP. A single recorded click on "Square Off All" can become a real financial action when replayed in the wrong environment. Do not put real tokens, webhook secrets, TOTP seeds, session cookies or screenshots containing them in the book exercises or Git.

### Practice and checkpoint

Draw five boundaries in the real app and write one failure for each. Explain why a dashboard WebSocket connection cannot prove broker tick freshness. A strong answer names the two independent channels and asks for a symbol-specific receive timestamp and source. A weak answer says "the green badge proves everything works."

## Chapter 2. Testing Foundations, SDLC and STLC

### Error, defect and failure

A human may misunderstand a requirement: that is an error. Code may use `value or default`, accidentally replacing a configured numeric zero: that is a defect. The user saves zero retries but later sees one retry: that is an observable failure. Debugging locates and repairs causes. Testing designs and executes observations that reveal failures or increase confidence. Quality assurance also improves the process that produces the software; it is broader than executing test cases.

Verification asks whether a work product follows its specification. Validation asks whether the result serves the user's need. Reviewing a formula is verification. Having a trader confirm that an exit alert closes only positions owned by the matching strategy contributes to validation. Both can be done early; neither is restricted to a final stage.

### Development and test lifecycles

The software development lifecycle includes discovery, requirements, design, implementation, verification, release and maintenance. Iterative teams revisit these stages rather than completing each once. The software testing lifecycle includes analyzing requirements, planning, designing tests, preparing data/environment, executing, reporting defects, retesting, regression testing and reporting residual risk. Testing should begin during requirements discussions: an ambiguous rule is much cheaper to fix before it becomes five services and a dashboard.

Consider capital 10,000 and stock price 12,000. Should quantity be zero or one? Both are implementable, but only one matches a chosen business policy. A "minimum one" policy intentionally exceeds the per-trade capital value. A test cannot resolve the business disagreement; it must record the chosen policy and its consequences. Never hide such a decision inside a test helper.

### Levels and purposes

| Test level | Example | What remains unproved |
| --- | --- | --- |
| Unit | BUY target calculation | HTTP parsing and broker fills |
| Integration | API saves configuration and store reads it | Real browser interaction |
| System | Browser, API, workers and disposable Redis | Live brokerage availability |
| Acceptance | User validates strategy-owned exit workflow | All possible failure sequences |

Functional testing checks behavior. Nonfunctional testing checks characteristics such as latency, resilience, accessibility, security and maintainability. Smoke testing asks whether a build is usable enough for deeper testing. Regression testing checks whether changes broke previously working behavior. Retesting executes the specific failed scenario after its fix. Exploratory testing combines learning, test design and execution in a bounded session.

### Why complete testing is impossible

Ten binary toggles already have 1,024 combinations before symbols, prices, time, broker responses and restarts are considered. Testing therefore selects risks deliberately. Prioritize duplicated orders, unauthorized actions and wrong exit quantities above cosmetic spacing. Test concentration is not negligence when the risk argument and remaining gaps are explicit.

Do not equate coverage with confidence. A line can execute while its result is never checked. A browser can click every button without verifying the persisted values. A suite of 300 tests can miss one damaging interleaving. Your report should say what you observed, under which conditions, and what you did not exercise.

### Practice and checkpoint

Classify "wrong retry count after reload," "response takes 20 seconds," and "another user can view credentials." They are respectively functional persistence, performance and authorization concerns, though categories overlap. Write one unit and one integration test idea for the zero-retry defect. The integration test must save zero, reload from the backend and compare the returned value, not merely read the input immediately after typing.

## Chapter 3. Requirements, Test Plans and Traceability

### Make a requirement observable

"Entries must be fast" is an aspiration. A testable statement defines measurement points, load, environment and an acceptance threshold. For example: "Under a synthetic burst of 20 alerts per second on the specified staging machine, 95% of valid webhook acknowledgments finish within the agreed budget; none wait for broker execution." The budget is a stakeholder decision. Do not advertise a made-up latency as an existing app guarantee.

Use Given/When/Then to clarify behavior:

```gherkin
Given strategy alpha owns an OPEN TEST position
And strategy beta owns another OPEN TEST position
When alpha's configured exit alert includes TEST
Then alpha's remaining quantity is submitted for exit once
And beta's position is unchanged
And alpha is CLOSED only after confirmed full exit fills
```

The final sentence prevents a dangerous shortcut: equating a submitted exit request with closure. Add examples for partial fills, rejected exits and duplicate callbacks. Acceptance criteria should distinguish intentional behavior from missing implementation.

### A compact test plan

State scope, risks, environments, data, responsibilities, schedule, entry criteria, exit criteria and reporting. For our release, scope includes configuration persistence, webhook-to-watch flow, execution idempotency and position display. Exclusions include actual exchange matching and broker uptime. The environment uses a fake broker and disposable Redis. Entry criteria include an identified commit and installed dependencies. Exit criteria include all critical safety scenarios passing, unresolved high-risk defects reviewed, and evidence attached. A deadline alone is not an exit criterion.

Create a traceability matrix before implementation grows:

| Requirement | Manual experiment | Automated evidence |
| --- | --- | --- |
| R01 preserve numeric zero | Save and reload retries=0 | API persistence plus browser reload |
| R02 strategy-owned exit | Exit alpha, observe beta | Two-position integration test |
| R03 candle TTL fixed | Delay reference candle | Injected clock and watch expiry |
| R04 no false LIVE row | Earlier skip, later entry | Dashboard row identity test |
| R05 broker acceptance is not fill | Return PENDING | Adapter and reconciliation test |

A requirement can have several tests. A test can cover several requirements, but avoid hiding ten unrelated failures behind one enormous scenario. Link defect IDs and test IDs so the team can see why a regression test exists six months later.

### Worked test case

**ID:** CFG-ZERO-01. **Precondition:** isolated app, authenticated test admin, strategy `demo` exists. **Data:** pending retries `0`, limit buffer `0`. **Steps:** open strategy; set both zero; save; confirm successful response; reload the page; reopen the same strategy. **Expected:** both fields and backend JSON remain zero. **Cleanup:** delete only this test strategy. **Evidence:** redacted response, screenshot and build identifier. **Negative variant:** omit a field entirely; its documented default should apply. Zero and missing are distinct partitions.

Store test cases as useful engineering artifacts, not ceremonial spreadsheets. A CSV is fine for a small team. Include title, requirement, setup, input, action, expected result, actual result, status, defect and evidence link. Avoid "works correctly" in the expected-result column: it gives the tester nothing concrete to compare.

### Practice and checkpoint

Rewrite "websocket works" into three requirements covering transport, subscription and freshness. Expected answer: a connection is established; the required security ID is included; a validated tick with acceptable age reaches the risk consumer. Add a fourth for recovery after credentials change. None is proved by only receiving HTTP 200 from `/api/broker-status`.

## Chapter 4. Test Design Techniques with Worked Tables

### Equivalence partitioning and boundaries

Equivalence partitioning groups inputs expected to behave alike. For quantity constrained to integer 1 through 100, useful groups are valid integers, integers below range, integers above range, fractional numbers, strings, booleans, null and missing. Python's `bool` is related to `int`, so `True` is a valuable negative example when a strict integer is required.

Boundary-value analysis targets edges: 0, 1, 2, 99, 100 and 101. For entry time 09:15 through 09:50, first define whether endpoints are inclusive. Then test just before start, exactly start, just after start, just before end, exactly end and just after end. Use timezone-aware timestamps. A test using your laptop's local time silently changes meaning on a UTC server.

### Decision tables

When several conditions interact, enumerate decisions explicitly. A simplified entry table is:

| Config exists | Enabled | Kill switch | Expected |
| --- | --- | --- | --- |
| No | any | any | CFG_MISSING, no submission |
| Yes | No | Off | disabled, no submission |
| Yes | Yes | On | blocked, no submission |
| Yes | Yes | Off | evaluate remaining filters |

"Any" means the result does not depend on that condition for this rule. It does not mean those combinations were all executed. Add sector readiness, fresh prices and duplicate positions in a second table rather than creating an unreadable sheet. If two rejection reasons apply, agree which takes precedence and whether reason ordering is a contract.

### State-transition testing

An order is not a Boolean success flag. Model transitions such as CREATED -> SUBMITTED -> PENDING -> PARTIAL -> COMPLETE, and PENDING -> CANCELLED or REJECTED. Include an UNKNOWN outcome after a network timeout. A valid state-transition test checks both the next state and side effects. After PARTIAL with requested 10 and filled 4, remaining is 6. A duplicate PARTIAL callback must not add another 4. A late COMPLETE after a cancel request requires reconciliation, not blind replacement.

```python
def remaining(requested, filled):
    if not 0 <= filled <= requested:
        raise ValueError("Inconsistent broker quantity")
    return requested - filled

assert remaining(10, 4) == 6
assert remaining(10, 10) == 0
```

The oracle is independently derived arithmetic plus the broker contract. A reported filled quantity greater than requested must not be "fixed" by silently clamping it to zero remaining; it is evidence that needs investigation.

### Pairwise, invariants and error guessing

Pairwise selection covers each pair of factor values, reducing many combinations. It is useful for browser, viewport, product and direction combinations, but cannot replace targeted three-way safety scenarios. Explicitly test SHORT + sector filter + losers selection even if a generated matrix happens to omit it.

Invariants hold across many sequences: a BUY trailing stop never decreases; a SELL trailing stop never increases; filled quantity cannot be negative; a rejected entry cannot create a filled position; exit quantity cannot exceed owned remaining quantity. Error guessing uses incident experience: mismatched alert names, stale secrets, daylight/timezone assumptions, duplicate webhook delivery and SDK error dictionaries returned with successful HTTP transport.

### Practice and checkpoint

Design six tests for breakout TTL=2 minutes. Include before expiry, exact expiry, after expiry, delayed candle, restart and duplicate alert. The essential answer is that retries and duplicate deliveries must not silently extend the original expiry. Define exact-expiry behavior in the specification before asserting it.

## Chapter 5. Manual Testing, Exploration, Accessibility and Defects

### A complete manual lab walkthrough

Start the local lab using its README. Open the page, tab through the form without a mouse, change strategy to `manual`, quantity to 2, target to 1 and stop to 0.5. Save and read the status message. Open browser Developer Tools, Network tab, and inspect the configuration response. The lab stores a strategy through `/api/strategies`; this is deliberately different from the production configuration route.

Submit a synthetic signal from the README's localhost command, replacing its strategy name with `manual`. Refresh positions. Expect one OPEN row at entry 100. Submit the same event again. Refresh: there must still be one position. Submit a tick at 101. Refresh: CLOSED, TARGET, P&L 2.00. Record what you observed at each boundary: response, stored state and screen. The lab fills instantly by design; a live trading test would also need broker fill evidence.

### Exploratory testing with a charter

A charter is a mission, not random clicking: "Explore configuration save failures for 25 minutes, looking for misleading success messages and stale values." Vary empty names, duplicate saves, browser refresh, disconnected network and invalid numbers. Keep notes with timestamp, action, observation and question. Stop and isolate a reproducible failure before changing ten more settings. End with a debrief: coverage, defects, unresolved questions and next charter.

### Usability and accessibility

At 390-pixel width, check whether labels remain readable, the Save button is reachable, and horizontal scrolling is contained to a table rather than the entire page. At 200% zoom, check reflow and clipped controls. Use Tab and Shift+Tab to verify focus order, visible focus and keyboard access. Open and close modals; focus should enter the dialog and return to the initiating control. Color must not be the only way to distinguish LIVE from ERROR.

An accessible name is the name assistive technology exposes for a control. A visible label beside an input is insufficient if it is not programmatically associated. Automated role locators provide useful feedback, but they do not replace a screen-reader session, contrast review or specialist accessibility audit. Do not claim compliance from a passing Playwright suite.

### Write a useful defect report

**Title:** Saved zero pending retries reloads as one. **Environment:** commit, browser version, disposable test account, timezone. **Steps:** minimal numbered sequence. **Expected:** zero persists. **Actual:** API or reload shows one. **Impact:** unintended additional order attempt. **Evidence:** redacted request/response and screenshot. **Severity:** impact if encountered. **Priority:** scheduling decision considering exposure and business urgency. A typo may be low severity but urgent before a public demo; duplicate order placement is high severity even if hard to reproduce.

Typical defect workflow: New -> Triaged -> In progress -> Fixed -> Retest -> Closed, or Reopened if the evidence still fails. A rejected bug needs a documented reason, such as an agreed requirement, not "works on my machine." Always record whether a failure belongs to application behavior, test code, test data or the environment.

### UAT and checkpoint

User acceptance testing uses representative workflows and business expectations. Ask the user to configure two strategies, show which one owns a position, and predict which exit alert is allowed to close it. Agreement must be recorded before release. Practice writing a defect for a skipped alert shown as LIVE after a later entry. A strong expected result preserves the earlier alert's status while presenting the actual open position separately.
