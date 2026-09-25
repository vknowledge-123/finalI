# Part VII. Workbook and Reference

## Appendix A. Manual Test Pack You Can Execute and Extend

These cases are requirements-based templates. Cases marked **Lab** are supported by the companion application. Cases marked **Extension** require you to add a fake implementation or use an isolated copy of the production app. Do not report extension cases as executed merely because this table exists. For each run record commit, test data, actual result and evidence.

| ID / scope | Setup and action | Expected observation |
| --- | --- | --- |
| M01 Lab | Fresh app; GET strategies | Empty list, successful response |
| M02 Lab | Save alpha qty 2 through UI | Success message; API returns qty 2 |
| M03 Lab | Save qty 0 through direct API | Validation error; no new config |
| M04 Lab | Save qty true through API | Strict integer validation rejects |
| M05 Lab | Signal before config exists | CFG_MISSING; zero positions |
| M06 Lab | Save alpha then signal TEST at 100 | OPEN qty 2, entry 100 |
| M07 Lab | Repeat same event and same payload | Same response, no extra position |
| M08 Lab | Same event ID, different price | Conflict, no second submission |
| M09 Lab | New event, same open strategy/symbol | ALREADY_OPEN conflict |
| M10 Lab | BUY tick at 100.99 | Remains OPEN, P&L 1.98 |
| M11 Lab | BUY tick at 101 | CLOSED TARGET, P&L 2.00 |
| M12 Lab | BUY tick at 99.50 | CLOSED STOP_LOSS, P&L -1.00 |
| M13 Lab | SELL tick at 99 | CLOSED TARGET, P&L 2.00 |
| M14 Lab | SELL tick at 100.50 | CLOSED STOP_LOSS, P&L -1.00 |
| M15 Lab | Tick after position CLOSED | Closed P&L unchanged |
| M16 Lab | Alpha/beta own TEST; exit alpha | Beta still OPEN |
| M17 Lab | Delete config then list | Config absent; existing position not erased |
| M18 Lab | Wrong symbol characters | Validation error, no position |
| M19 Lab | Mobile 390 px, complete form | Save reachable, no document overflow |
| M20 Lab | Mock positions API 502 JSON | Error visible; not false success |
| M21 Extension | Save retries 0 and buffer 0 | Both preserved after reload |
| M22 Extension | TSL off, favourable tick | No trailing update |
| M23 Extension | BUY TSL moves up then price falls | TSL does not move back down |
| M24 Extension | Cost SL off; RR reached | No cost-SL action |
| M25 Extension | SL .6%, RR 2, BUY rises 1.2% | Cost rule activates per boundary policy |
| M26 Extension | SHORT sector top-N enabled | Most negative sectors selected first |
| M27 Extension | Missing previous sector close | Ranking not fabricated as zero change |
| M28 Extension | Missing candle then late valid candle | Fixed reference, original TTL retained |
| M29 Extension | Breakout after TTL | No order |
| M30 Extension | Duplicate watch after restart | No duplicate watch/order or TTL extension |
| M31 Extension | Broker PENDING | No full filled position |
| M32 Extension | Fill 4 of requested 10 | Owned quantity 4; remainder 6 |
| M33 Extension | Insufficient funds rejection | Reason visible; no false LIVE entry |
| M34 Extension | Lost broker response after acceptance | Reconcile before possible replacement |
| M35 Extension | Feed connects but no symbol tick | Not represented as fresh data |
| M36 Extension | Old token replaced | Relevant workers use new credentials |
| M37 Extension | Old skip plus new same-symbol trade | Skip remains skip; only correct trade LIVE |
| M38 Extension | API request without admin session | Protected data unavailable |
| M39 Extension | Two simultaneous admin setup requests | One owner only |
| M40 Extension | Logout then reuse session | Sensitive access denied per policy |
| M41 Extension | TOTP reused in same step | Enforced documented replay policy |
| M42 Extension | Wrong webhook secret | No queued trade candidate |
| M43 Extension | Alert exit for wrong strategy | No unrelated position closed |
| M44 Extension | CNC next-day restore, holdings delayed | Preserve unresolved ownership record |
| M45 Extension | Telegram toggle off | No notification sent |
| M46 Extension | Duplicate exit and target tick together | At most owned remaining quantity exits |
| M47 Extension | Worker crashes after claim | Work recovered, not duplicated blindly |
| M48 Extension | Backtest runs during tick flow | Live handling remains within measured budget |

### Reusable defect template

```text
ID:
Title: observable symptom, not a guessed cause
Requirement / risk:
Build and environment:
Preconditions and synthetic data:
Minimal steps:
Expected:
Actual:
Reproducibility:
Severity and proposed priority:
Redacted evidence / correlation IDs:
Workaround and its risks:
Fix verification and regression test:
```

Example: "Historical SKIPPED alert becomes LIVE after later same-symbol entry." Expected: immutable alert outcome remains SKIPPED; a separately identified trade row is LIVE. Risk: operator may close or interpret the wrong event. Verification requires at least two alert episodes and a matching symbol, not a single happy-path position.

## Appendix B. Worked Solutions and Debugging Drills

### Drill 1: the zero-default bug

Faulty expression: `retries = payload.get('retries') or 1`. Trace it with input 0: lookup returns 0, Python considers it false, so `or` selects 1. Fix the policy first: is null allowed, and does omission mean default? For a strict API, reject null or handle it explicitly; use `.get('retries', 1)` when only omission should default. Test absent, zero, one, negative, string and null separately. Add a browser save/reload test to catch serialization mistakes.

### Drill 2: false confidence from a green badge

A status response says authenticated=true, dashboard socket connected=true, but no symbol has a recent broker receive timestamp. The correct conclusion is partial health, not full data readiness. Your evidence checklist is authentication, broker transport state, subscription identity, latest source/age, risk-consumer receipt and browser receipt. The UI should present distinct indicators. Do not substitute a current REST timestamp into a WebSocket-health field.

### Drill 3: target arithmetic and display

Entry 427.50, target 1% gives 431.775 before display rounding. Two-decimal half-up display is 431.78. Stop 0.5% gives 425.3625, displayed 425.36. A configured trailing distance of 0.8% initially gives 424.08 if calculated directly from entry, but an engine may use the tighter protective stop as the effective line. Test the documented effective-stop policy; do not assume a displayed TSL must always equal entry minus the trailing percentage. Order tick alignment is yet another separate rule.

### Drill 4: the delayed breakout candle

At 10:30:03 IST with one-minute breakout, reference the closed 10:29-10:30 bar. If its high is 100 and the absolute buffer is 0.10, the threshold is 100.10. If the policy is strict break, 100.10 does not cross; 100.11 does. A two-minute watch retains its original expiry despite repeated fetches. If the candle appears late, use the original reference bar, not the latest forming candle. Buffer units must be specified: an absolute 0.10 is not the same as 0.10%.

### Drill 5: partial fill and retry

Requested 10; confirmed fill 4; cancel requested; final broker reconciliation says fill 6 before cancellation. The known remaining amount is now 4, not 6. A retry based on the earlier snapshot can overbuy. Build a scripted fake that returns these exact stages and assert the next submitted quantity. If the final outcome is unknown, do not guess a remainder. Preserve unresolved state and escalate according to policy.

### Drill 6: flaky concurrency assertion

The test starts a 150 ms fake data call, then demands an order finish within 50 ms. On a loaded machine, scheduling alone can violate the number. To prove independence, hold the data call behind an event and assert the order completes before releasing it. To prove latency, run a separate benchmark with defined hardware, sample count, load and percentile target. Combining the two questions produces confusing evidence.

### Drill 7: wrong alert name

Incoming alert is `high volume stock`; saved strategy is `Alert for F&O INTRADAY BULLISH SCANNER`. These are different names. Redis health cannot make them the same configuration. Inspect the received normalized key, user scope and saved key. Test intended normalization and collision rejection. Do not implement a fallback to the first enabled strategy: that would route an unknown signal into unrelated trading rules.

### Drill 8: HTTP success versus business success

HTTP transport succeeds, but the response body is an SDK failure envelope with an empty data object. Treating it as an empty candle list hides the broker failure. The adapter should distinguish valid empty data, malformed structure and explicit failure. Test each. User-facing messages should be safe and actionable, while redacted logs retain diagnostic categories.

### Drill 9: temporal ownership

An old entry and a new entry share strategy and symbol but have different trade IDs. A dashboard joins only on symbol and attaches new P&L to the old row. Add trade-episode identity to the join. A regression test should vary both strategy and trade ID independently. An ownership rule is not complete if it handles two strategies but still confuses two trades from the same strategy.

### Drill 10: ambiguous deployment failure

Nginx returns 502; systemd reports API active; the API port listens. These facts do not prove successful request handling. Check a local read-only health endpoint and application traceback, then reverse-proxy upstream errors. Correlate times, process IDs and recent changes. Avoid sending a real webhook as a diagnostic because it can create an order if the system recovers during the test.

## Appendix C. Syllabus Coverage and Additional Depth

| Requested topic | Where to study and practice |
| --- | --- |
| Manual testing, SDLC/STLC, UAT, defects | Chapters 1-5; Appendix A |
| Setup, comments, variables and operators | Chapters 6-7 |
| Strings, lists, tuples, sets, dictionaries | Chapters 7-8 |
| Conditions, loops, formatted output | Chapters 7-8 |
| Functions, scope, modules and packages | Chapter 9 |
| OOP, inheritance, polymorphism, abstraction | Chapter 10 |
| File handling and data-driven inputs | Chapters 11, 16 and 30 |
| Exceptions and advanced Python | Chapters 12-13 |
| Pytest, fixtures, marks and parameterization | Chapters 14-18 |
| Playwright setup and built-in locators | Chapters 19-20 |
| CSS, XPath, Shadow DOM, SelectorsHub | Chapter 20 |
| Inputs, radio, checkbox, native/custom menus | Chapter 21 |
| Static/dynamic/paginated tables | Chapter 22 |
| jQuery/Bootstrap calendars, dialogs, frames | Chapter 23 |
| Mouse, keyboard, upload and download | Chapter 24 |
| Contexts, tabs, popups and auth state | Chapter 25 |
| Screenshots, video, traces and flaky tests | Chapter 26 |
| Request mocking and stream evidence | Chapter 27 |
| Cross-browser, mobile and parallel testing | Chapter 28 |
| Page Object Model, framework development | Chapters 29-30 |
| Pytest and Allure reporting | Chapter 30 |
| REST methods and CRUD | Chapter 31 |
| Headers, cookies and JSON Schema | Chapter 32 |
| Basic/Bearer/API-key/OAuth, email and TOTP | Chapter 33 |
| Full-stack integration and security | Chapter 34 |
| Git, GitHub, Jenkins and GitHub Actions | Chapters 35-36 |
| Codegen, MCP, PyCharm and Copilot | Chapter 37 |
| Load, resilience, queues and observability | Chapters 18 and 38 |
| Project capstone and SDET preparation | Chapters 39-40 |

### Beyond the core: what to study next

Property-based testing generates many values from a stated invariant. For example, valid tick rounding should return a multiple of the tick and must not round a BUY below the input price. Use a library such as Hypothesis in a separate extension exercise; shrinking a failure to a minimal example is often more useful than retaining a giant random dataset.

Mutation testing deliberately changes small operators or conditions and checks whether tests notice. Surviving mutations can expose weak assertions, but some mutations are equivalent or outside meaningful requirements. Review results instead of chasing a single score.

Consumer/provider contract testing checks that services agree on schemas and semantics without running an entire system for every change. A schema-only check is insufficient for order state transitions. Version both response shapes and state assumptions.

Accessibility, security and performance need specialist depth. Treat browser checks, a security checklist and a small load run as beginnings, not certification. For senior roles, also study SQL query plans, networking, Linux processes, containers, cloud IAM, observability, architecture decisions and incident response. The existing engineering book in this repository develops cloud and Kubernetes separately; this volume concentrates on testing.

## Appendix D. Glossary and Release Report

| Term | Practical meaning |
| --- | --- |
| Assertion | Check comparing observed behavior with an expectation |
| Oracle | Independent source of expected behavior |
| Fixture | Controlled setup/data/resource supplied to a test |
| Stub | Substitute returning predefined responses |
| Fake | Simplified working implementation of a dependency |
| Spy | Recorder of calls or interactions |
| Contract | Agreed input, output and semantic behavior at a boundary |
| Idempotency | Repeating an operation does not create extra intended effects |
| Reconciliation | Comparing local records with authoritative evidence |
| Partial fill | Only part of requested quantity has executed |
| Race | Outcome depends on interleaving of concurrent operations |
| Lease | Time-bounded ownership requiring renewal |
| TTL | Lifetime after which data or work expires |
| Regression | Previously supported behavior broken by a change |
| Flaky test | Outcome varies without an intended relevant change |
| Smoke test | Small check that a build/environment is usable |
| UAT | Business-user evaluation against acceptance needs |
| SDET | Engineer combining software development and test engineering |
| P95 latency | Value at or below which roughly 95% of sampled durations lie |
| Test double | General term for a dependency substitute |
| DOM | Browser representation of document structure |
| Context | Isolated browser-session boundary in Playwright |
| Trace | Recorded execution evidence for debugging |
| CI | Automated integration checks for proposed changes |
| Risk-based testing | Prioritizing evidence by impact and likelihood |

### Release summary template

```text
Build / commit:
Environment and dependency versions:
Requirements in scope:
Requirements excluded:
Tests collected / passed / failed / skipped / expected-failed:
Browser engines and viewport sizes actually executed:
Broker mode: fake, recorded fixture, sandbox, or authorized live check:
Critical invariants checked:
Unresolved defects and mitigations:
Performance workload and results, if measured:
Artifact locations, access controls and retention:
Recommendation: release / restricted release / hold:
Decision owner and date:
```

A truthful report may recommend holding a release even when most tests pass. Conversely, a known cosmetic issue may be acceptable with an explicit decision. The tester provides evidence and risk analysis; do not conceal failures to make a dashboard green.

## Appendix E. Primary Sources and Reading Order

Documentation was consulted while preparing this edition. APIs, IDE menus and installation requirements can change; use these primary sources alongside the dependency versions recorded in your own environment. Explanations and exercises in this book are original teaching material, not copied course transcripts.

- [Python tutorial](https://docs.python.org/3/tutorial/): language reference path for Parts II-III.
- [Pytest fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html): fixture lifecycle and scope.
- [Playwright installation](https://playwright.dev/python/docs/intro): setup and browser installation.
- [Playwright locators](https://playwright.dev/python/docs/locators): supported locator behavior.
- [Playwright actions](https://playwright.dev/python/docs/input): form and input APIs.
- [Playwright dialogs](https://playwright.dev/python/docs/dialogs): browser-dialog lifecycle.
- [Playwright frames](https://playwright.dev/python/docs/frames): iframe interaction.
- [Playwright downloads](https://playwright.dev/python/docs/downloads): download events and saving.
- [Playwright authentication](https://playwright.dev/python/docs/auth): session-state reuse precautions.
- [Playwright API testing](https://playwright.dev/python/docs/api-testing): APIRequestContext.
- [Playwright network](https://playwright.dev/python/docs/network): interception and observation.
- [Playwright pytest plugin](https://playwright.dev/python/docs/test-runners): execution options.
- [Playwright trace viewer](https://playwright.dev/python/docs/trace-viewer): diagnostic traces.
- [Playwright Codegen](https://playwright.dev/python/docs/codegen): recording workflows.
- [Playwright MCP](https://github.com/microsoft/playwright-mcp): official browser MCP server.
- [JetBrains MCP client](https://www.jetbrains.com/help/ai-assistant/mcp.html): IDE-client setup.
- [GitHub Copilot MCP](https://docs.github.com/en/copilot/how-tos/provide-context/use-mcp-in-your-ide/extend-copilot-chat-with-mcp): client integration.
- [GitHub Python workflows](https://docs.github.com/en/actions/tutorials/build-and-test-code/python): CI concepts.
- [Jenkins pipeline syntax](https://www.jenkins.io/doc/book/pipeline/syntax/): pipeline configuration.
- [Allure pytest](https://allurereport.org/docs/pytest/): report integration.
- [OWASP testing guide](https://owasp.org/projects/web-security-testing-guide): authorized security assessment.
- [Dhan order contract](https://dhanhq.co/docs/v2/orders/): broker order semantics.
- [Kite order contract](https://kite.trade/docs/connect/v3/orders/): broker order semantics.

Start with concepts, implement the lab, then read the corresponding production tests. Do not begin by executing client-account operations. The goal is to understand and prove software behavior in a controlled environment before approaching operational risk.
