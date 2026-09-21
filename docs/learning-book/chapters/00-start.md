# Rebuilding Your Trading Application

## A practical book on Python software engineering and Google Cloud

Prepared for Amol. Edition 1, 19 September 2026.

This book teaches you to rebuild a trading application yourself, beginning with small Python programs and progressing to APIs, databases, secure login, asynchronous services, browser testing, cloud operations, and architecture. The aim is not to memorize your existing source code. It is to understand the decisions behind it, reproduce its useful behavior, and recognize where a different design would be safer.

Your existing application is the case study. You receive Chartink signals, resolve instruments, evaluate strategies, submit broker orders, reconcile actual fills, monitor exits, and show results in a browser. That one workflow contains nearly every difficulty that makes backend engineering interesting: untrusted input, timing, concurrency, partial failure, external contracts, persistent state, and human expectations.

This is a substantial project textbook and workbook, not an encyclopedia of every Python feature or a substitute for every professional cloud exam guide. It gives you a deep common foundation and specialization labs. Completing it is evidence of learning, not a guarantee of employment, seniority, certification, trading profitability, or production safety.

### How to use the book

Read in order the first time. For each chapter, explain the concept aloud, implement the exercise without opening the existing implementation, write tests, and only then compare designs. Keep a notebook with four headings: prediction, observed behavior, explanation, and remaining uncertainty. A test failure is useful when you can explain why it occurred.

The accompanying `lab` directory is a deliberately small reference implementation. It uses a simulated broker and local SQLite. It has no live-order adapter and is not a production deployment. Build your own version in a new repository; use the reference to compare behavior rather than to bypass the exercises. The later chapters specify larger systems you must implement yourself. They do not claim those systems already exist in the reference lab.

Code blocks fall into three categories. The lab's files are runnable together. Short Python examples demonstrate a concept and name their dependencies. Architecture sketches and pseudocode explain contracts, not complete deployable services. Cloud commands create billable resources only where explicitly stated. Run cloud exercises in a disposable project, never in a client's production project.

Keep real access tokens, webhook secrets, client emails, encryption keys, Redis passwords, and trading records out of your learning repository. Use fictional symbols and sanitized fixtures. A secret pasted into a chat or committed to Git needs a rotation plan; deleting the visible text is not rotation. Do not casually rotate an encryption key without first planning how existing encrypted data will be decrypted and re-encrypted.

### What exists and what you will add

The repository inspected for this book contains FastAPI, an HTML dashboard, Redis state and queues, Dhan and Kite adapters, a trade engine, authentication code, and separate API, alert, execution, market-feed, and reconciliation entry points. Its dependency file pins DhanHQ 2.2.0 and KiteConnect 5.0.1. Those are facts about this checkout, not a recommendation to use old pins forever.

PostgreSQL, SQLAlchemy, database migrations, a transactional outbox, BigQuery, Vertex AI, vector search, Terraform, and Kubernetes are learning extensions in this book. They are not silently attributed to the existing application. Start with the tools that solve your current problem; adding every cloud product to a single trading path would increase cost and failure modes.

| Existing reference | What to study there | What to question |
|---|---|---|
| `app/main.py` | API routes, auth, lifecycle, browser feed | How much responsibility belongs in one module? |
| `app/trade_engine.py` | Entry and exit decisions | Which rules should become pure functions? |
| `app/dhan_broker.py` | SDK boundary and feed recovery | Are responses validated before changing state? |
| `app/kite_broker.py` | A different broker contract | Are common names hiding different meanings? |
| `app/redis_store.py` | Persistence, key scope, TTL | Which facts must survive cache eviction? |
| `app/market_feed_service.py` | Subscription ownership and health | Connected, subscribed, or receiving fresh data? |
| `app/execution_service.py` | Queue consumption and worker lease | What happens between submission and acknowledgment? |
| `app/reconciliation_service.py` | Recovery and fallback pricing | What is broker evidence versus local expectation? |
| `app/order_locks.py` | Mutual exclusion and ownership | What happens when a lease expires during work? |
| `app/auth.py`, `app/email_service.py` | Email login workflow | Is verification atomic and abuse-resistant? |
| `app/static/dashboard.html` | Forms, fetch, WebSocket updates | Are browser labels backed by fresh evidence? |
| `tests/` | Unit, integration and browser checks | Which failure cannot this test actually detect? |

Read code as evidence, not as scripture. A README can lag behind implementation; a test can encode a mistaken assumption. Follow a real input through the current routes and service boundaries.

### Your development sequence

1. Build a command-line calculator for quantity, target, stop, and P&L.
2. Model immutable signals, orders, fills, positions, and strategy settings.
3. Add a deterministic paper broker and unit tests.
4. Save configuration and fills in a relational database.
5. Expose a validated HTTP API with a consistent error contract.
6. Add administrator bootstrap, password login, email verification, and TOTP.
7. Build a dashboard that displays server state and handles failures.
8. Add simulated ticks and confirm exits from actual simulated fills.
9. Add durable background jobs, duplicate protection, and recovery.
10. Implement broker adapters against sanitized fixtures and official contracts.
11. Split the measured bottlenecks into independently supervised processes.
12. Deploy a paper-only version to Google Cloud with HTTPS, monitoring, and restore tests.
13. Add analytics and ML as separate, non-executing extensions.
14. Conduct an architecture review, incident drill, and independent code review.

Do not begin with sixteen network services. A logical service can initially be a Python class. A process is an operating-system boundary; a distributed service adds network and operational boundaries. You can learn modularity before learning distributed failure.

### A realistic study rhythm

Use four sessions per topic: learn and trace; implement; test and break; explain and refactor. At roughly ten focused hours each week, a 36-week first pass is a planning estimate, not a deadline. Someone new to programming may need longer. Advance when you can pass the chapter's exit exercise without following a tutorial.

Weeks 1-8 cover Python and algorithms. Weeks 9-14 cover API, SQL, and authentication. Weeks 15-21 cover trading state, feeds, and concurrency. Weeks 22-26 cover testing and browser integration. Weeks 27-32 cover cloud and operations. Weeks 33-36 cover one specialization and a capstone review. Work experience and maintaining software over time remain important for senior roles.

### The essential safety rule

Use a simulator throughout this book. A successful HTTP response is not a confirmed fill. A green WebSocket badge is not proof of current prices. A passing test suite is not proof that every failure has been considered. Build evidence for each claim separately.

### Sources and version policy

Official sources are linked near version-sensitive topics. They were consulted for this edition. Recheck SDK signatures, cloud product names, quotas, prices, exam guides, and broker requirements when you implement or book an exam. The cloud portfolio in your attachment is a list of possible paths, not a single syllabus, and its cross-vendor comparisons are approximate rather than equivalences.

