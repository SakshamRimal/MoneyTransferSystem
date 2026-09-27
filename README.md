# Money Transfer System

An API-only money transfer service built with **FastAPI + PostgreSQL + SQLAlchemy 2.0 (async) + Alembic**.
It prioritises the things that make ledgers hard: **atomic transfers, no lost updates or negative
balances under concurrency, idempotent retries, consistent errors, and tests that actually prove it**.

* Interactive API docs: `http://localhost:8000/docs` (also `/redoc`)
* Health probe: `GET /health`

---

## Table of contents

1. [Quick start](#quick-start)
2. [API](#api)
3. [Architecture](#architecture)
4. [Key design decisions & trade-offs](#key-design-decisions--trade-offs)
5. [Testing](#testing)
6. [Configuration](#configuration)
7. [Logging & observability](#logging--observability)
8. [What was skipped, and what I'd do next](#what-was-skipped-and-what-id-do-next)

---

## Quick start

### Option A — Docker (recommended, nothing to install)

```bash
docker compose up --build
# → db (Postgres 16) becomes healthy, migrations run, app starts on :8000
curl http://localhost:8000/health
```

Run the test suite in containers:

```bash
docker compose --profile test run --rm test
```

Tear down (add `-v` to also delete the database volume):

```bash
docker compose down
```

> **Port conflicts:** override with a `.env` file or environment variables, e.g.
> `APP_PORT=8080 POSTGRES_PORT=5433 docker compose up --build`.

### Option B — Run on the host

Requirements: Python 3.11+, PostgreSQL 14+.

```bash
# 1. Databases (skip if you already run Postgres)
docker compose up -d db          # or use your own instance

createdb money_transfer          # app database
createdb money_transfer_test     # test database (name MUST end in _test)

# 2. Dependencies + configuration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env             # adjust URLs/credentials if needed

# 3. Schema + app
make migrate                     # alembic upgrade head
make run                         # uvicorn with --reload on :8000

# 4. Tests / lint (in another shell)
make test                        # whole suite (needs money_transfer_test)
make test-unit                   # unit tests only — no database needed
make lint
```

Common commands are listed in the [`Makefile`](Makefile) (`make help`).

---

## API

All endpoints are prefixed with `/api/v1`.

| Method | Path | Purpose | Success | Typical errors |
|---|---|---|---|---|
| `POST` | `/accounts` | Create an account | `201` | `422` validation |
| `GET` | `/accounts/{id}` | Account details | `200` | `404`, `422` |
| `POST` | `/transfers` | Move money between accounts | `201` | `404`, `409`, `422` |
| `GET` | `/accounts/{id}/transactions` | Paginated ledger history | `200` | `404`, `422` |
| `GET` | `/health` | Liveness probe | `200` | — |

### Examples

```bash
# Create two accounts
curl -s -X POST localhost:8000/api/v1/accounts \
  -H 'content-type: application/json' \
  -d '{"owner_name":"Alice","currency":"USD","initial_balance":"100.00"}'

curl -s -X POST localhost:8000/api/v1/accounts \
  -H 'content-type: application/json' \
  -d '{"owner_name":"Bob","currency":"USD"}'          # balance defaults to 0.00

# Transfer (Idempotency-Key makes retries safe)
curl -s -X POST localhost:8000/api/v1/transfers \
  -H 'content-type: application/json' \
  -H 'Idempotency-Key: 8f14e45f-ea4c-4f3b-9c1d-2b6a5e7c9d01' \
  -d '{"from_account_id":"<alice-id>","to_account_id":"<bob-id>","amount":"25.50","currency":"USD"}'

# History, page 2 of 20
curl -s "localhost:8000/api/v1/accounts/<alice-id>/transactions?limit=20&offset=20"
```

### Money representation

Amounts cross the wire as **human-readable `Decimal` strings with at most 2 decimal places**
(`"25.50"`), and are stored in the database as **`BIGINT` minor units** (cents). No floats ever
appear anywhere in the system — see [trade-offs](#key-design-decisions--trade-offs).

### Idempotency (`POST /transfers`)

Send an `Idempotency-Key` header (recommended for any client that may retry):

* **Same key + same body, request already completed** → the original response is replayed
  verbatim with `Idempotent-Replay: true`, and **the money does not move twice**.
* **Same key + different body** → `409 idempotency_key_conflict`.
* **Key from a failed attempt** (e.g. insufficient funds) is not consumed — the retry executes
  normally.
* **No key** → every request executes (at-least-once semantics are the client's choice).

Keys are scoped to `POST /api/v1/transfers` and kept in the `idempotency_keys` table
(retention target `IDEMPOTENCY_KEY_TTL_HOURS`, default 24h — the cleanup job is not implemented,
see [below](#what-was-skipped-and-what-id-do-next)).

### Error envelope

Every 4xx/5xx response uses one shape:

```json
{ "error": { "code": "insufficient_funds", "message": "Account '…' has insufficient funds for this transfer." } }
```

| HTTP | `code` | When |
|---|---|---|
| 404 | `account_not_found` | Unknown account id |
| 409 | `idempotency_key_conflict` | Key reused with a different payload |
| 409 | `concurrent_modification` | Optimistic-lock retry budget exhausted (reserved) |
| 422 | `validation_error` | Malformed payload (schema-level) |
| 422 | `insufficient_funds` | Balance would go negative |
| 422 | `currency_mismatch` | Accounts (or payload) disagree on currency |
| 422 | `account_inactive` | Frozen/closed account involved |
| 422 | `same_account_transfer` | Defence-in-depth (schema rejects this first) |
| 500 | `internal_server_error` | Unexpected failure (logged with stack trace) |

### Pagination

`GET /accounts/{id}/transactions?limit=&offset=` returns:

```json
{
  "items": [ { "id": "…", "transfer_id": "…", "direction": "debit", "amount": "25.50",
               "balance_after": "74.50", "currency": "USD",
               "counterparty_account_id": "…", "created_at": "…" } ],
  "meta": { "total": 5, "limit": 20, "offset": 0, "has_more": true }
}
```

Newest first, `limit` ∈ [1, 100] (default 20). Each account sees its own side of every transfer
(debit/credit) with a running balance — the history doubles as an audit log.

---

## Architecture

### Layering (controller → service → repository)

```
HTTP request
   │
   ▼
┌─────────────────────────────  app/api/v1  ─────────────────────────────┐
│ Controllers (accounts.py, transfers.py)        "controllers"           │
│  · parse/validate input (Pydantic schemas)                             │
│  · pick status codes, headers                                          │
│  · know nothing about SQL or business rules                            │
└───────────────┬────────────────────────────────────────────────────────┘
                │ FastAPI dependency injection (app/api/deps.py)
                ▼
┌──────────────────────────  app/services  ──────────────────────────────┐
│ Services (AccountService, TransferService)     "business logic"        │
│  · rules: currency match, active accounts, sufficient funds            │
│  · locking strategy: FOR UPDATE in a fixed global order                │
│  · idempotency: advisory lock, fingerprint, replay                     │
│  · raises domain exceptions — no HTTP knowledge                        │
└───────────────┬────────────────────────────────────────────────────────┘
                ▼
┌──────────────────────  app/repositories  ──────────────────────────────┐
│ Repositories (account/transfer/idempotency)   "data access"            │
│  · the only place that writes SQL                                      │
│  · knows about SELECT … FOR UPDATE, indexes, pagination                │
└───────────────┬────────────────────────────────────────────────────────┘
                ▼
        PostgreSQL (asyncpg)   ←  one session/transaction per request
```

Cross-cutting:

* **`app/core`** — config (12-factor `Settings`), DB engine/session, domain exceptions, logging.
* **`app/models`** — SQLAlchemy 2.0 `Mapped` models (accounts, transfers, ledger, idempotency).
* **`app/schemas`** — Pydantic request/response models; the only place Decimals meet the wire.
* **`app/middleware`** — per-request correlation id + access log line.
* **`app/utils/money.py`** — the *only* place minor↔major unit conversion happens.

### Directory layout

```
app/
├── main.py                 # app factory, middleware, exception handlers, /health
├── api/
│   ├── deps.py             # composition root: session → repos → services
│   └── v1/{accounts,transfers}.py
├── core/                   # config, database, exceptions, logging
├── models/                 # Account, Transfer, LedgerEntry, IdempotencyKey
├── repositories/           # BaseRepository (generic) + concrete repos
├── schemas/                # Pydantic I/O models
├── services/               # AccountService, TransferService
├── middleware/             # request logging + correlation id
└── utils/money.py          # Decimal ↔ integer minor units
migrations/                 # Alembic (initial schema)
tests/
├── conftest.py             # DB fixtures: savepoint-rollback client + committed client
├── support.py              # shared API helpers
├── unit/                   # 73 tests — no database (money, schemas, services w/ mocks)
└── integration/            # 37 tests — real API + real Postgres (incl. concurrency)
Dockerfile                  # multi-stage: base (app) + dev (tests)
docker-compose.yml          # db → migrate → app, opt-in test service
```

### Request lifecycle (one transfer)

1. `POST /api/v1/transfers` → controller validates the payload (Pydantic) and reads `Idempotency-Key`.
2. `get_db_session` opens **one session/transaction per request** (commit on success, rollback on error).
3. `TransferService.create_transfer`:
   * takes a **Postgres advisory transaction lock** on the idempotency key (if present) so
     concurrent retries serialise;
   * replays the stored response if this key already completed;
   * locks both account rows with `SELECT … FOR UPDATE`, **always in sorted-UUID order** (deadlock-free);
   * validates status/currency/funds, mutates balances, inserts the `Transfer` + two `LedgerEntry`
     rows (double-entry), flushes;
   * stores the response body against the idempotency key.
4. The dependency commits; the controller sets `201` (or the replay header).
5. Any `DomainError` bubbles up to the exception handler in `main.py`, which rolls the transaction
   back and renders the standard error envelope.

---

## Key design decisions & trade-offs

**Money as integer minor units (not float, not `NUMERIC(…)`).**
`balance_minor_units BIGINT` + `CHECK (balance >= 0)`. Integer arithmetic is exact, portable, and
immune to binary-float error; conversion to/from `Decimal` happens only at the API boundary.
Trade-off: only 2-decimal currencies are supported today — extending to JPY (0) or BHD (3) means a
per-currency exponent lookup (documented, not implemented).

**Double-entry ledger behind the history endpoint.**
Every transfer writes exactly two append-only `ledger_entries` (debit + credit) in the same
transaction as the balance update. This gives an audit trail, an O(indexed) history query, and a
reconciliation invariant (`Σ ledger == balance`). Trade-off: two extra rows per transfer.

**Concurrency: pessimistic row locks with a global lock order.**
`SELECT … FOR UPDATE` on both accounts, always sorted by UUID first. Read-modify-write then cannot
race, and opposite-direction transfers (A→B vs B→A) cannot deadlock. Rejected in favour of
optimistic retry loops (simpler to reason about, no retry storms) and in favour of `SERIALIZABLE`
isolation (abort storms under load; `READ COMMITTED` + row locks is sufficient here because every
balance read happens *under the lock*). An optimistic `version` column is kept as defence-in-depth.

**Idempotency: advisory lock *before* executing, not unique-constraint-after-the-fact.**
`pg_advisory_xact_lock(hashtext(endpoint:key))` serialises same-key requests *before* money moves,
so a duplicate can never execute twice; the loser then replays the stored response. A plain
"insert, catch unique violation" approach would let both racers run business logic first. Trade-off:
advisory locks are Postgres-specific (acceptable — the whole stack is Postgres), and lock names
could theoretically hash-collide (negligible in practice).

**`clock_timestamp()` for ledger `created_at`, not `now()`.**
`now()` is the *transaction* timestamp: rows written in one transaction would tie on `created_at`
and make `ORDER BY created_at DESC, id DESC` (history pagination) ambiguous. This was caught by a
failing test during development and fixed in the model + migration.

**Consistent error envelope + domain exceptions.**
Services raise `DomainError` subclasses carrying a machine-readable `code` and an HTTP status;
handlers in `main.py` translate them. Business logic stays framework-agnostic and unit-testable.

**Transactions are request-scoped.**
One session per request, commit at the end (`get_db_session`). Repositories never commit — the
service/composition layer owns the unit of work, so a request is exactly one atomic transaction.

**12-factor configuration.**
Everything is environment-driven via `pydantic-settings` (`.env` is a convenience for local dev,
never committed); the container gets its config from compose environment variables. Logging is JSON
to stdout with a per-request correlation id (reusing an inbound `X-Request-ID` if provided).

**Schema via Alembic in deploys, `create_all` in tests.**
Migrations are the source of truth for real environments (`alembic upgrade head` runs as a separate
compose service before app replicas start). The test suite recreates the schema from the models in
a dedicated `*_test` database — faster and always in sync, at the cost of not exercising migrations
themselves (a known gap; see below).

---

## Testing

```bash
make test              # everything       (needs money_transfer_test)
make test-unit         # 73 tests, no DB needed
make test-integration  # 37 tests, needs Postgres
docker compose --profile test run --rm test   # in Docker
```

**110 tests total.**

| Suite | What it proves |
|---|---|
| `tests/unit/test_money.py` | Minor-unit conversion, HALF_UP rounding, exact round-trips |
| `tests/unit/test_schemas.py` | Payload validation (precision, currency shape, distinct accounts…) |
| `tests/unit/test_account_service.py` | Account mapping/lookup with a mocked repository |
| `tests/unit/test_transfer_service.py` | Business rules with mocked repos: lock order, balance arithmetic, insufficient funds leaves state untouched, currency/status rules, replay vs conflict, fingerprinting, pagination metadata |
| `tests/integration/test_accounts_api.py` | Create/get accounts, validation and 404 envelopes over HTTP |
| `tests/integration/test_transfers_api.py` | Transfers move money, write both ledger sides, reject bad requests without side effects |
| `tests/integration/test_idempotency_api.py` | Replay returns the original result and moves money once; key conflict → 409; failed attempts don't burn the key |
| `tests/integration/test_transactions_api.py` | Pagination, ordering, running balances, bounds validation |
| `tests/integration/test_concurrency_api.py` | **The concurrency tests** (below) |

### Test isolation strategy (`tests/conftest.py`)

* **`client`** — every test runs inside one outer DB transaction; app sessions join it with
  `join_transaction_mode="create_savepoint"` and the whole thing is **rolled back after each
  test**. Fast, parallel-safe, no cleanup. (All requests in a test share one connection, so row
  locks would never actually contend — which is why it must not be used for concurrency tests.)
* **`committed_client`** — each request gets its **own real connection and commits for real**,
  so `FOR UPDATE` and advisory locks behave exactly as in production; tables are truncated
  afterwards.
* A safety check refuses to run tests unless the configured test database name ends in `_test`.

### The concurrency test

`tests/integration/test_concurrency_api.py` fires simultaneous requests at the *same* account pair
via `asyncio.gather` against the real ASGI app (bounded by a 60s timeout so a deadlock fails fast):

1. **Overdraw:** 40 concurrent `10.00` debits against a `100.00` balance → **exactly 10 succeed**,
   30 are rejected with `insufficient_funds`, final balances are exactly `0.00` / `100.00`, and the
   ledger debits sum to exactly `100.00`. A lost update would show a negative balance or too much
   money leaving the account.
2. **Bidirectional:** 10× `A→B` and 10× `B→A` concurrently → all `201`, both balances exactly
   `50.00`, no deadlock (this is what the sorted lock order guarantees).
3. **Concurrent idempotent retries:** 10 simultaneous requests sharing one `Idempotency-Key` →
   one transfer id, one execution, balances moved once.

---

## Configuration

All settings live in `app/core/config.py` and are read from the environment (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/money_transfer` | App (async) |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://…/money_transfer` | Alembic only |
| `TEST_DATABASE_URL` | `…/money_transfer_test` | Test suite (**must end in `_test`**) |
| `APP_ENV`, `DEBUG` | `local`, `false` | Environment flags |
| `LOG_LEVEL`, `LOG_JSON` | `INFO`, `true` | Logging (JSON to stdout in prod, text locally) |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_ECHO` | `10`, `20`, `false` | Engine tuning |
| `DEFAULT_CURRENCY` | `USD` | Fallback currency |
| `DEFAULT/MAX_TRANSACTION_HISTORY_PAGE_SIZE` | `20`, `100` | Pagination bounds |
| `IDEMPOTENCY_KEY_TTL_HOURS` | `24` | Idempotency record retention |
| `APP_PORT`, `POSTGRES_PORT` | `8000`, `5432` | Compose host-port overrides (`.env`) |

---

## Logging & observability

* Structured **JSON logs to stdout** (log-aggregator friendly); `LOG_JSON=false` for readable
  local output.
* Every request gets a **correlation id** (reuses inbound `X-Request-ID`, echoes it on the
  response) that is attached to every log line emitted while handling it.
* One `request_completed` line per request: method, path, status, duration.
* Domain events are logged explicitly: `account_created`, `transfer_completed`,
  `idempotent_replay`, `domain_error` (warning), `unhandled_exception` (with stack trace).
* `GET /health` for liveness/readiness; Docker `HEALTHCHECK` wired to it.

---

## What was skipped, and what I'd do next

Skipped **deliberately** (out of scope / not over-engineered):

* **Authentication/authorisation** — the assignment doesn't ask for it; ownership checks would be a
  thin middleware + `created_by` column.
* **Idempotency-key TTL cleanup job** — `IDEMPOTENCY_KEY_TTL_HOURS` is read from config, but no
  sweeper deletes expired rows yet (a small periodic `DELETE … WHERE created_at < now() - interval`
  or a pg_cron job).
* **Per-currency exponents** — only 2-decimal currencies (documented above).
* **Multi-currency conversion / FX** — accounts must share a currency.
* **A migrations test** — tests rebuild the schema from models rather than running Alembic, so
  migration/model drift isn't caught automatically (an `alembic upgrade head` + autogenerate
  empty-diff CI step would close that gap).
* **Outbox/events, CQRS, message queues** — no requirement justifies the complexity.

With more time:

1. CI pipeline (lint → unit → integration → Docker build) and a coverage gate.
2. `SELECT … FOR UPDATE` contention metrics + Prometheus instrumentation; alert on lock wait time.
3. Soft-delete/`status` transitions for accounts (freeze/close) with an admin API.
4. Reconciliation job asserting `Σ ledger_entries == accounts.balance` continuously.
5. Property-based tests (Hypothesis) for the money arithmetic and randomised transfer graphs.

---

## Status against the assignment brief

| Priority | Item | Status |
|---|---|---|
| P0 | Create/get account, transfer, history endpoints | ✅ |
| P0 | Atomic DB transactions, no negative balances under concurrency | ✅ (+ tests) |
| P0 | Validation + correct status codes, safe money representation | ✅ |
| P0 | Automated tests incl. concurrency | ✅ 110 tests |
| P1 | Idempotency, constraints/indexes, error envelope, pagination, API docs | ✅ |
| P2 | Clean structure, migrations, Docker, unit + integration tests | ✅ |
