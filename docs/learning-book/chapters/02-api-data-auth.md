# Part Two APIs Databases and Authentication

## Chapter 9 HTTP and API contracts

HTTP is a request-response protocol. A request has a method, path, headers, and optionally a body. A response has a status, headers, and optionally a body. JSON is a representation of data, not a guarantee of success. The earlier browser error `Unexpected token I` happened because code tried to parse plain `Internal Server Error` as JSON.

Design an API contract before a route function. State who may call it, which inputs are accepted, which durable effect occurs, which response means success, and whether repeating the request is safe. `GET` should not place orders. A webhook `POST` that returns `202 Accepted` means work was accepted for processing, not that a trade filled.

Use `400` for malformed application requests, `401` for missing or invalid authentication, `403` for an authenticated caller lacking permission, `404` for unavailable resources, `409` for state conflicts, `422` for schema validation, `429` for limits, and `503` for temporary inability to serve. Do not force every failure into HTTP 200 just because the browser can parse it.

```json
{
  "ok": false,
  "error": "CONFIG_NOT_FOUND",
  "detail": "No active configuration matches this alert",
  "request_id": "example-request-17"
}
```

Keep public errors useful but free of passwords, stack traces, SDK URLs, SQL, and internal paths. Log the detailed diagnostic under the request ID after redaction. A reverse proxy can still return HTML errors even if your application always returns JSON; the frontend must handle both.

An endpoint like `/api/positions?user_id=2` must not trust the requested user ID as authorization. Derive the authorized account from the session and validate any requested scope against it. This remains relevant on a single-user installation because public URLs are still attacker-controlled inputs.

**Build:** Write an OpenAPI-style table for create strategy, receive signal, list positions, request exit, and read feed health. Include one happy response and three failures for each. Set body-size, symbol-count, and string-length limits.

**Exit check:** Explain why an HTTP 200 from `/api/broker-status` says nothing about whether its response body reports `ticker_connected: false`.

## Chapter 10 FastAPI validation and dependency injection

FastAPI connects HTTP input to Python functions. Pydantic models validate structure and constraints. Dependencies provide services such as an authenticated principal, database session, or broker interface. Dependency injection makes tests easier because you can replace the broker without replacing the business rules.

```python
from decimal import Decimal
from typing import Literal
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI()

class StrategyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    side: Literal["BUY", "SELL"]
    quantity: int = Field(strict=True, gt=0, le=10000)
    stop_pct: Decimal = Field(gt=0, lt=100, allow_inf_nan=False)
    retry_count: int = Field(strict=True, ge=0, le=3)

@app.post("/validate-strategy")
def validate_strategy(payload: StrategyInput):
    return {"ok": True, "strategy": payload.model_dump(mode="json")}
```

This isolated validation example does not authenticate or persist anything. `extra="forbid"` catches misspelled settings rather than silently ignoring them. `strict=True` prevents quantities such as boolean `True` from being treated as one. Decide whether numeric strings are accepted at your boundary, and test that decision.

Use application lifespan to acquire and close long-lived clients. Do not open a new Redis connection or HTTP session on every tick if a pool can be reused. Do not store a database session as a global shared mutable transaction. The repository gives useful examples to review, but your rebuild should keep route handling separate from domain decisions.

An `async def` route runs on an event loop. Calling blocking SDK code directly inside it can freeze other work. Use a bounded thread executor for synchronous network operations or an async client where appropriate. Ordinary synchronous route functions are handled differently by FastAPI; understand the distinction before moving code around. [FastAPI concurrency guidance](https://fastapi.tiangolo.com/async/)

Create a test-only application factory, `create_app(settings, repository, broker)`, instead of mutating global production state. Production should fail startup when required secrets or storage are absent, not silently switch to a test backend.

**Build:** Add schema validation to your calculator API. Test unknown fields, missing names, boolean quantity, zero retry count, negative stop, and unexpected content types. Document whether configuration edits affect existing positions or only future entries.

## Chapter 11 Relational database design

A database is not just a place to store JSON. It can enforce facts that must remain true under concurrency. A primary key identifies a row. A foreign key ensures a referenced row exists. A unique constraint prevents two transactions from creating the same logical record. A transaction groups changes into an all-or-nothing unit.

Your existing application uses Redis extensively. For the rebuild, introduce PostgreSQL as the durable ledger and keep Redis for fast transient state and queues. This is a proposed learning architecture, not a claim that Redis is inherently unsuitable or that PostgreSQL eliminates all failure.

Separate concepts that change independently:

| Entity | Key facts | Important constraint |
|---|---|---|
| Account | Admin ownership, broker connection reference | No cross-account reads |
| Strategy | Stable ID, display name, settings | Unique canonical name per account |
| Strategy version | Immutable settings used at decision time | Position references the version |
| Instrument | Broker, exchange, security ID, tick size | Unique composite identity |
| Signal | Source event, payload digest, timestamps | Scoped deduplication key |
| Order intent | Desired action and quantity | Stable intent ID |
| Broker order | Remote ID and submission state | Unique broker/account/order ID |
| Fill | Execution ID, quantity, price | Unique execution identity |
| Position allocation | Strategy-owned quantity and basis | No negative remaining quantity |
| Outbox event | Payload and delivery state | Written with the business transaction |
| Audit event | Actor, action, reason, timestamp | Append-only application policy |

Do not store target and stop only by looking up today's mutable strategy row. If a user changes a stop from 1% to 3%, yesterday's carry position should not silently change its risk policy unless the product explicitly supports that operation.

```sql
CREATE TABLE strategy (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    canonical_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (account_id, canonical_name)
);

CREATE TABLE fill (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    broker_order_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    price NUMERIC(20, 8) NOT NULL CHECK (price > 0),
    executed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (account_id, broker_order_id, execution_id)
);
```

This is a partial schema exercise; add account and order tables with foreign keys before production use. `NUMERIC` provides decimal storage. Store timestamps as `TIMESTAMPTZ`, but remember a trading date is a separate business attribute.

Normalize repeated facts instead of copying credentials into every strategy. JSONB can hold versioned strategy-specific options, but validate their schema and index deliberately. Do not use JSON to avoid deciding what an order means.

**Build:** Draw the entity relationships. Create migrations with Alembic and SQLAlchemy after first writing equivalent SQL manually. Add unique constraints, check constraints, and indexes. Make the database reject duplicate fills even when two workers race.

## Chapter 12 Transactions concurrency and the outbox

Consider two requests that both read "no position" and then submit an entry. An application-level check alone does not prevent both. You need a serialized decision, durable reservation, or other concurrency control at the ownership boundary. A transaction cannot roll back an order already submitted to an external broker.

PostgreSQL isolation determines which changes a transaction can observe. Row locks can serialize updates to an existing row. They do not lock a nonexistent row in the same simple way, so unique constraints or explicit account/strategy guard rows matter. Serializable transactions can fail and require retry of the database transaction; never automatically replay external broker side effects inside that retry. [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html)

For a manual exit, first atomically reserve the position for an exit intent. Commit that intent and an outbox row together. A worker later reads the intent and talks to the broker. Its crash recovery is based on the durable intent ID and broker evidence. This separates database atomicity from uncertain external execution.

The transactional outbox solves a specific problem: committing a state change and then crashing before publishing its event. Write both in one database transaction. A publisher sends pending outbox rows and marks them sent. It may publish twice if it crashes after sending but before marking. Consumers must deduplicate by event ID. Outbox does not magically create exactly-once broker execution.

Use optimistic concurrency for editable settings: update where `version = expected_version`, increment the version, and return conflict if another edit won. This avoids one browser tab overwriting another's changes unnoticed.

Learn parameterized SQL. `WHERE name = ?` or driver-specific placeholders keep data separate from SQL structure. Do not use f-strings to inject names into a query. SQLAlchemy's expression API provides parameters, but raw SQL inside it still requires care.

**Build:** Launch two concurrent entry requests and prove exactly one local intent is reserved. Crash a publisher after send but before acknowledgment and prove duplicate consumption does not duplicate position quantity. Add a migration rollback exercise on a disposable database, then explain why destructive rollback may not be safe for real fills.

## Chapter 13 Redis as a cache queue and coordination tool

Redis stores typed data structures: strings, hashes, lists, sets, sorted sets, and streams. A key name is a convention, not access control. Scope it with environment and account. `paper:account:17:tick:NSE_EQ:1333` is clearer than a global `ltp` key. Different logical database numbers are not a strong tenant isolation boundary.

TTL is appropriate for transient quotes and session expiry, not for deleting the only copy of an open position. Expired cache data should cause a fresh fetch or an explicit unavailable state, not a fabricated zero price. A daily rollover should archive dashboard history while preserving carry positions and audit records.

Pub/Sub delivers to active subscribers; it is not a durable backlog. Lists can implement queues but need a processing list and recovery policy. Streams provide consumer groups and pending entries. Whichever mechanism you choose, model worker crashes, redelivery, poison messages, and retention explicitly. Pin and consult your Redis version's command documentation before implementing recovery features.

A lock with `SET key token NX PX duration` has an owner token and expiry. Release it only if its stored token still equals yours, using an atomic script. Otherwise an old worker can delete a new worker's lock. Lease renewal can fail. For database writes, use a fencing version when possible. Brokers generally cannot validate your fencing token, so unknown submissions still require reconciliation and conservative ownership rules.

Connection pools avoid repeated handshakes. Pipelining reduces round trips, but is not automatically a transaction. Measure network latency, command latency, queue wait, and event-loop lag separately before blaming Redis. Your previous `AuthenticationError` was a credentials mismatch, not proof of a slow datastore.

**Build:** Implement a quote cache with a receipt timestamp and TTL; a durable signal queue with a dead-letter path; and a session store. Test restart, lost connections, password mismatch, duplicate delivery, and a lease expiring during a slow operation. Keep critical ledger data outside any eviction policy that can discard it unnoticed.

## Chapter 14 Email ownership and secure login

An email address can serve as an identifier. It is not a password and does not prove ownership until verified. An administrator allowlist answers "which identity is permitted?" Email verification answers "can this person receive mail at this address?" Password and TOTP answer different authentication questions.

For your single-admin product, bootstrap must not be first-public-visitor-wins. Provision the allowed email and a one-time bootstrap secret out of band, or keep setup restricted to a trusted local/IAP session. Create the admin atomically. Once configured, setup must remain closed until an authorized backend recovery operation resets it.

Use a maintained password-hashing library and a contemporary password KDF such as Argon2id; choose work parameters by measuring your server and checking current guidance. Password hashes are not reversible encryption. Broker credentials and TOTP seeds must be recoverable by the application and therefore require protected encryption instead. Rate-limit password work so expensive verification cannot exhaust the server. [OWASP authentication guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)

### Email verification flow

1. Validate and canonicalize the address using a deliberate policy. Do not invent Gmail dot-removal rules for every domain.
2. Return a generic response so public callers cannot enumerate registered accounts.
3. Generate a cryptographically random, high-entropy token for a verification link.
4. Store a digest, purpose, account binding, creation time, expiry, and consumed time.
5. Send an HTTPS link through an email provider, using a trusted configured base URL rather than an untrusted Host header.
6. On confirmation, atomically mark the token consumed and the account verified.
7. Reject reuse, expiry, purpose mismatch, and a different account binding.

```python
import hashlib
import secrets

token = secrets.token_urlsafe(32)
token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
assert len(token_digest) == 64
```

Only the digest belongs in the token table. This unkeyed digest is reasonable for a high-entropy random token; a six-digit OTP is a different case because its entire space is small. For OTPs, use a server-secret HMAC bound to challenge ID, purpose, and account, plus atomic attempt limits and expiry. Never log either token type.

Email scanners may prefetch links. Prefer a landing GET that does not consume the token and an explicit confirmation POST. Avoid third-party resources on the landing page, use an appropriate Referrer-Policy, and avoid retaining tokens in analytics or proxy query logs. Password-reset tokens must not be interchangeable with email-verification tokens. [OWASP reset-token guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)

### Sending mail reliably

Implement an email-provider interface. Use a local fake inbox in tests and a sandbox account in staging. SMTP acceptance means the receiving server accepted the message, not that it reached the inbox. Retry transient sending failures with a bounded notification queue; do not block order processing while email is down.

Configure SPF for permitted senders, DKIM signing, and a staged DMARC policy for your domain. Monitor delivery failures and complaints. These controls improve domain authentication, not recipient identity. Compute Engine restricts some outbound SMTP traffic; use a documented provider integration or supported authenticated submission path rather than assuming port 25 works. [Google Cloud mail guidance](https://docs.cloud.google.com/compute/docs/tutorials/sending-mail)

**Build:** Implement verification and password reset against a fake email sender. Test resend limits, email enumeration, token reuse, concurrent redemption, wrong purpose, provider failure, and link scanning. A failed email send must never expose the OTP as a debugging fallback.

## Chapter 15 TOTP sessions and broker authorization

TOTP calculates a short code from a shared seed and time. Use a maintained library such as PyOTP; do not implement cryptography yourself. Display the enrollment QR only after password verification. Keep enrollment pending until a valid code is confirmed. Encrypt the seed, never log it, and restrict access to it.

On login, verify the password first, then challenge for TOTP, then issue a full session. A pending MFA session must not access trading routes. Record the accepted time step atomically to prevent simultaneous reuse where your policy requires replay protection. Keep the tolerated clock window narrow and monitor time synchronization.

Recovery is part of authentication, not a bypass. Generate one-use recovery codes, show them once, store their digests, rate-limit attempts, and audit use. A developer reset must require trusted VM/IAM access, revoke existing sessions, remove pending challenges, and document who approved it. Do not delete strategy or position records while resetting authentication.

Use opaque, unpredictable session tokens stored server-side by digest. Cookies should have `HttpOnly`, `Secure` under HTTPS, a constrained path, and an appropriate `SameSite` policy. Rotate the session after authentication and privilege changes. Revoke it on logout. Check authorization for both HTTP and WebSocket routes.

Cookie-authenticated state changes need CSRF defenses. CORS controls browser cross-origin reading; it is not authentication and does not stop non-browser attackers. Validate WebSocket origins and authenticate the handshake. A terminal `curl` does not inherit the browser's session, explaining `ADMIN_AUTH_REQUIRED` even after browser login.

Keep application login separate from broker authorization. A logged-in administrator is allowed to manage the app but may still have an expired Dhan token. Broker consent callbacks must bind to a short-lived server-side pending flow and the intended account. Do not consume any callback token on behalf of whichever user ID appears in a query. Only use state or PKCE mechanisms the provider actually supports; add a secure local transaction binding when designing around provider constraints. Check the current [Dhan authentication contract](https://dhanhq.co/docs/v2/authentication/) before implementing its consent flow.

**Build:** Draw the states `UNCONFIGURED`, `PASSWORD_SET`, `MFA_PENDING`, `ACTIVE`, and `RECOVERY_REQUIRED`. Test every forbidden transition. Verify that neither email verification nor a successful broker login accidentally grants an admin application session.

