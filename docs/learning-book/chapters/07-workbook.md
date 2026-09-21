# Part Seven Your Rebuild Workbook

## Chapter 43 The staged implementation contract

The reference lab is a small first vertical slice. It deliberately omits production login, distributed queues, live WebSockets, broker credentials, carry reconciliation, sector feeds, and cloud APIs. Those are your later assignments. Its ability to calculate a paper exit is not evidence that it can manage a brokerage account.

Create a new repository for your rebuild and keep the existing application read-only during study. Use one branch per milestone. At the start, write the behavior in plain English and write a failing test. At the end, explain the implementation without opening the code. Keep a short decision note recording what you intentionally did not build.

### Milestone 1 A calculator you can trust

Implement strict capital sizing, explicit minimum-one policy, BUY/SELL P&L, target, stop, and tick alignment. Inputs must reject NaN, infinity, zero price, and unsupported sides. Deliver a CLI, ten or more meaningful boundary tests, and a one-page explanation of Decimal versus float. A function that silently invents defaults fails the milestone.

### Milestone 2 Domain objects and a paper broker

Create signal, intent, order, fill, and position types. The broker accepts an order independently of filling it. Support delayed and partial fills in the simulator. Deliver a state diagram and prove that a repeated fill event has no second accounting effect. Explain why a broker ID is not an entry price.

### Milestone 3 Durable configuration and accounting

Use SQLite locally to learn SQL, then PostgreSQL for concurrency exercises. Store strategy versions, signals, intents, and fills with constraints. Restart and recover records. Deliver migrations and a restore test. Do not store a client's actual broker token during this milestone.

### Milestone 4 An HTTP vertical slice

Build create/list strategy, receive signal, list positions, and request exit routes. Define schemas and errors. Add duplicate-request semantics and conflict on reused IDs with different payloads. Deliver API tests that query the resulting database. Returning 200 without checking stored effects fails the milestone.

### Milestone 5 Admin email password and TOTP

Implement restricted atomic setup, email ownership verification, password hashing, TOTP enrollment, full and pending sessions, CSRF, and recovery. Use a fake mail provider. Deliver concurrent setup and token redemption tests. Verify that a public visitor cannot seize the administrator account before its intended owner.

### Milestone 6 A browser that reflects reality

Build configuration forms, open/closed positions, connection health, stale-price display, and understandable failures. Deliver Playwright tests at desktop and mobile sizes. Send an HTML 502 response to the frontend and confirm it shows a controlled error rather than a JavaScript crash.

### Milestone 7 A deterministic risk engine

Add independent target, stop, trailing, cost-stop, pyramid, and alert-exit behavior. Preserve ownership by strategy and product. Deliver a tick replay with expected order intents and confirmed exits. A partially filled exit must not disappear from open positions until its remaining quantity is zero.

### Milestone 8 Candle and sector filters

Add a historical-data interface, anchored breakout watches with TTL, exchange-calendar rules, and previous-session sector baselines. Deliver SHORT loser ranking tests, unknown-sector policy tests, and a watch-expiry race test. Replaying a watch after restart must not extend it.

### Milestone 9 Queues and concurrent workers

Introduce durable signal processing and bounded workers. Add backpressure, admission control, poison-message handling, and a recovery policy. Deliver a crash-after-claim test and an unknown-submission test. Measure queue age under load. Do not replace uncertainty with blind retries to make tests easier.

### Milestone 10 Broker adapters without live execution

Implement Dhan and Kite adapters using their installed SDK signatures and sanitized fixtures. Cover instrument mapping, quote normalization, market depth, order status, fills, candles, and holdings. Deliver a capability matrix and transport-level contract tests. Keep all test credentials fictional and all network order submission blocked.

### Milestone 11 Feed ownership and reconciliation

Implement one connection owner, credential refresh, subscription replay, bounded tick delivery, source timestamps, and explicit degraded states. Reconcile orders, trades, positions, and holdings. Deliver a restart drill with carry positions and a lost order update. Report separately whether you proved connection, subscription request, or actual tick receipt.

### Milestone 12 A cloud paper deployment

Deploy behind HTTPS using least privilege and protected secrets. Add health checks, monitoring, log retention, backups, and a measured restore. Deliver infrastructure definitions and a deployment runbook that another person can execute. The deployed application remains paper-only.

### Milestone 13 Analytics and optional AI

Export sanitized events to an analytical store and add one advisory ML or RAG feature. Deliver leakage checks, retrieval/model evaluation, cost controls, and access restrictions. The AI component has no authority to submit, cancel, or modify a live order.

### Milestone 14 Independent review

Ask another engineer to use the app, inspect one transaction race, attempt an unauthorized request in the test environment, and follow your recovery guide. Record findings, fix them, and add regressions. Senior-level growth includes responding to evidence and feedback, not merely producing more code.

## Chapter 44 Guided debugging cases and answer notes

### Case A Configuration exists on screen but an alert says missing

Collect the incoming alert name, authorized account, canonical-name rule, stored configuration key, and worker environment. Compare exact strings before blaming Redis. A configured name `BULLISH SCANNER` does not match incoming `high volume stock`. Also check that API and worker use the same intended Redis endpoint and database. Do not print credentials while comparing endpoints.

**Expected reasoning:** First separate name mismatch from tenant mismatch from storage/authentication failure. Add a correlation ID across intake and execution, and a sanitized reason explaining which lookup failed. Never fall back to an arbitrary strategy because its name seems similar.

### Case B Price equals entry all afternoon

Check broker connection health, stock subscription identity, latest tick source, receipt time, queue delay, and browser connection. A `REST_EXIT_FALLBACK` price is not a WebSocket tick. A browser may also display stale data even while risk processing receives fresh data.

**Expected reasoning:** Trace the path hop by hop: broker packet, normalization, shared store/bus, risk consumer, dashboard publisher, browser DOM. Attach timestamps at each boundary. Do not assume a green service state proves the entire chain.

### Case C A target appeared hit but no closed position appeared

Check whether the observed LTP was fresh and belonged to the right instrument; whether the configured exit applied to that product; whether one exit intent was reserved; whether the broker accepted and filled it; and whether the UI merged the final state correctly.

**Expected reasoning:** Trigger, request, acceptance, fill, ledger closure, and rendering are different facts. A UI status like `ENTERED BUY` must not replace a confirmed closed-position record. Reconciliation and stable entity IDs should repair missed notifications.

### Case D Too many open files after reconnecting

Measure the process descriptor count over repeated connect/disconnect cycles. Inspect event loops, HTTP sessions, sockets, thread lifetimes, and cleanup paths. Raising the limit can be temporary containment but does not establish that the leak stopped.

**Expected reasoning:** Reproduce in an isolated soak test. Assert descriptors and threads stabilize after many cycles. Close resources in `finally`, and make reconnect ownership singular. Do not create an event loop for each message.

### Case E Admin email is allowed in a file but setup rejects it

Check which EnvironmentFile the running service loads, when it last restarted, any application `.env` loading precedence, address normalization, and the actual request email. Avoid displaying the entire process environment.

**Expected reasoning:** File contents are not automatically running process state. An updated file can coexist with an older in-memory value. Fix configuration precedence and provide a safe diagnostic that reveals only the allowed email policy, not secrets.

### Case F Execution worker fails for roughly thirty seconds after restart

Examine whether the previous worker's ownership lease remains, whether it was released safely, and whether the new worker later recovers. Compare lock TTL and startup timing. Do not delete a lock merely to silence the error.

**Expected reasoning:** A lease may be intentionally protecting against overlap. Improve shutdown and startup observability, but preserve safety against a still-running old owner and uncertain broker submissions. Temporary lock contention is not proof Redis is slow.

### Case G A market order was accepted but never filled

Distinguish pending, rejected, partial, cancelled, and unknown. Read broker-provided rejection details. Validate product, funds, security ID, tick size, circuit limits, and the actual serialized order type. A last-traded price is not executable ask depth.

**Expected reasoning:** Retry only under a bounded policy with fresh data and reconciled remaining quantity. Never retry an unknown outcome as though it were a confirmed rejection. Broker protection mechanisms and rules must be checked against the current provider contract.

## Chapter 45 Senior developer interview preparation

A senior engineer is expected to make trade-offs, debug across boundaries, communicate uncertainty, and improve how a team delivers software. Learning one project deeply can be powerful evidence, but years of operational judgment are not compressed into a certificate. Prepare to discuss decisions and failures honestly, including the role AI played in the original implementation.

### A forty five minute system design exercise

Spend five minutes clarifying users, brokers, account isolation, volume, latency, market hours, and safety requirements. Spend five defining entities and invariants. Spend ten drawing the major components and data flow. Spend ten on order uncertainty, retries, reconciliation, and recovery. Spend five on scale and capacity. Spend five on security and operations. Use the final five to summarize trade-offs and unanswered risks.

State assumptions before choosing tools. Example: "For one client and a modest tick set, I start with supervised processes on one VM and a durable ledger. This minimizes cost but accepts a single-VM availability limitation. I would not call it highly available."

### Questions you should answer without notes

1. Why is an accepted order not a position? Explain fill-based accounting and partial quantity.
2. How do two exit triggers avoid double-selling? Explain durable reservation and ownership.
3. What happens when a broker call times out? Explain unknown state and evidence-based recovery.
4. How does a cache differ from a ledger? Explain expiry, durability, and authoritative state.
5. When would you split a service? Explain workload isolation and operational cost.
6. How do you keep an event loop responsive? Explain bounded I/O offloading and CPU isolation.
7. How do you change an API safely? Explain versioned contracts and compatibility rollout.
8. Why can a unit test pass while the dashboard is broken? Explain proxy, browser, and deployment boundaries.
9. How do you secure first-admin setup? Explain out-of-band ownership and atomic initialization.
10. How do you recover after losing a database? Explain backups, reconciliation, RPO, and RTO.
11. How do you rank sectors correctly for short trades? Explain ascending valid changes and stale-data exclusion.
12. How would you use ML safely here? Explain an advisory boundary with no execution authority.

Use the structure requirement, alternatives, decision, evidence, consequence. Avoid claiming a feature is industry standard just because it uses Redis or Kubernetes. Show a test, a measurement, a diagram, or a failure analysis.

### Portfolio evidence

Include a sanitized architecture document, a runnable paper demo, migration files, meaningful test results, one load report, a threat model, a restore report, and two incident postmortems. Record which code you wrote independently and which reference material you consulted. Never publish client credentials, brokerage screenshots with personal data, or private production logs.

## Chapter 46 Completion rubric and daily practice

Score each skill from 0 to 3. Zero means unfamiliar. One means you can follow instructions. Two means you can implement and explain independently. Three means you can diagnose failure, compare alternatives, and review another person's implementation. This is a personal learning rubric, not a hiring or certification standard.

| Skill | Evidence for level 3 |
|---|---|
| Core Python | Explain aliasing and exceptions; write tested pure functions |
| Advanced Python | Bound concurrency and correctly handle cancellation |
| API development | Validate, authorize, version, and observe a vertical slice |
| Database design | Enforce invariants under concurrent transactions |
| Authentication | Defend bootstrap, recovery, token redemption, and sessions |
| Broker integration | Handle partial fills and ambiguous side effects |
| Testing | Reproduce a race and prevent regression across browser/backend |
| Cloud operations | Deploy, diagnose, restore, and account for costs |
| System design | Defend a simpler and a scaled architecture with trade-offs |
| Data and ML | Detect leakage and isolate advisory models from execution |
| Communication | Write a clear incident report without overstating certainty |

A productive daily session can be 20 minutes reviewing yesterday's concept, 50 minutes coding, 30 minutes testing, and 20 minutes explaining and recording decisions. Change the durations to fit your schedule. The important habit is producing your own reasoning, not consuming endless videos.

When using AI, first write your prediction and test. Ask for a hint, a counterexample, or a review rather than a full replacement. After reading help, close it and reimplement the concept. If you cannot explain the failure case that motivated the code, you have not yet learned that code.

## Appendix A Glossary

**Acceptance:** A broker acknowledges an order request; it does not prove a trade.

**Atomicity:** A group of local database changes commits together or not at all.

**Backpressure:** Slowing or rejecting producers when downstream capacity is bounded.

**Circuit breaker:** A client-side failure-control pattern that temporarily stops calls after repeated failure. This is different from exchange circuit-price limits.

**Correlation ID:** An identifier linking related operations; it is not automatically an idempotency guarantee.

**Durability:** A stated level of persistence after acknowledgment, dependent on configuration and failure assumptions.

**Fencing:** Rejecting operations from an older owner using a monotonically newer ownership version, where the receiving system supports it.

**Freshness:** Whether data is recent enough for a particular use, considering its source and timestamps.

**Idempotency:** Repeating the same logical operation does not create another business effect.

**Invariant:** A rule that must always remain true, such as nonnegative remaining quantity.

**Liveness:** Evidence that a process can continue operating, distinct from readiness for a business action.

**OLTP:** Transaction-oriented storage for operational state changes.

**OLAP:** Analytical processing across historical or aggregated data.

**Outbox:** Durable events committed with the business changes they describe.

**RPO:** The acceptable data-loss window after a failure.

**RTO:** The target time to restore a service after a failure.

**Reconciliation:** Comparing local state with external authoritative evidence and resolving differences.

**SLI and SLO:** A measured service indicator and its target objective.

**TOTP:** A time-based code derived from a shared secret; one authentication factor, not broker authorization.

**Trace:** A linked view of an operation across components.

## Appendix B Reference reading order

Use official documentation as a reference while building, not as a reason to postpone coding indefinitely. Begin with [Python](https://docs.python.org/3/tutorial/), then [FastAPI](https://fastapi.tiangolo.com/), [pytest](https://docs.pytest.org/), and [Playwright Python](https://playwright.dev/python/). Read PostgreSQL transaction and indexing documentation when you reach durable state. Read current OWASP guidance before implementing public authentication.

For brokers, use the [DhanHQ API documentation](https://dhanhq.co/docs/v2/) and [Kite Connect API documentation](https://kite.trade/docs/connect/v3/), together with the exact installed SDK source. Do not assume SDK documentation with a different version number matches your pinned package.

For cloud study, follow the current landing page and linked guide for your chosen certification. Product names and weights change. Keep a dated objective checklist and explicitly identify topics not exercised by the trading project. Official sample questions teach format; they do not guarantee your exam result.

