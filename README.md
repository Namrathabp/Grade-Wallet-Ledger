# High-Throughput Digital Wallet & Double-Entry Ledger Core Engine
A production-grade, distributed payment and ledger backend engine built with FastAPI and PostgreSQL 15+. Designed with strict financial consistency, this wallet ensures money never appears from nowhere, disappears, or moves twice.

### Key Highlights
- Strict Double-Entry Bookkeeping: Every monetary transaction atomically generates at least two immutable ledger entries (debit and credit) such that $\sum \text{Debits} = \sum \text{Credits}$. Immutability is strictly enforced at the database level via triggers disallowing UPDATE and DELETE operations.
- Race Condition & Deadlock Safety: Eliminates race conditions and lost updates under high concurrency using deterministic row-level locking (SELECT ... FOR UPDATE ordered ascendingly by account ID). Successfully handles 100 concurrent requests competing for a single wallet across 20 consecutive iterations with zero overdrafts.
- Network Fault Tolerance & Idempotency: Implements an idempotency layer storing SHA-256 request body hashes with unique keys. Safely returns cached responses on network retries, rejects in-flight duplicates (409 Conflict), and flags payload mismatches (422 Unprocessable Entity).
- Nightly Self-Auditing Reconciliation: An asynchronous background worker running under REPEATABLE READ snapshot isolation that validates global balance parity, transfer parity, stored-vs-computed ledger balances, and non-negative constraints using non-blocking distributed advisory locks (NOWAIT).
- Automated Fraud Controls & Statement Q&A: Pre-commit risk rules hold high-value transfers for administrative review. A prompt-injection-safe natural language interface maps plain-English statement questions into strictly parameterized SQL queries.

## Architecture Overview
```
                      +-----------------------------+
                      |         HTTP Client         |
                      |   (Postman / Web / Tests)   |
                      +--------------+--------------+
                                     |
                       (Idempotency-Key: <UUID>)
                                     v
                      +-----------------------------+
                      |        FastAPI Engine       |
                      |   - Idempotency Middleware  |
                      |   - Uniform Error Handler   |
                      +--------------+--------------+
                                     |
               +---------------------+---------------------+
               |                     |                     |
               v                     v                     v
     +-------------------+ +-------------------+ +-------------------+
     |  Transfer Service | |   Admin & Audit   | |   Statement Q&A   |
     | (FOR UPDATE Locks)| | (Held Tx Release) | | (Mock LLM / SQL)  |
     +---------+---------+ +---------+---------+ +---------+---------+
               |                     |                     |
               +---------------------+---------------------+
                                     | (psycopg3 Connection Pool)
                                     v
     +---------------------------------------------------------------+
     |                       PostgreSQL 15+                          |
     |  - users / accounts (unified user & system accounts)          |
     |  - transfers / ledger_entries (append-only trigger)           |
     |  - idempotency_keys / risk_rules / risk_flags / job_locks     |
     +-------------------------------+-------------------------------+
                                     ^
                                     | (Audit Snapshot - REPEATABLE READ)
                      +--------------+--------------+
                      |    Background Worker Task   |
                      |  - Nightly Reconciliation   |
                      |  - Non-blocking Lock NOWAIT |
                      +-----------------------------+
```

## Tech Stack
- Language & Framework: Python 3.11+, FastAPI, Pydantic v2
- Database & Persistence: PostgreSQL 15 (Alpine), psycopg3 with native connection pooling (ConnectionPool)
- Testing & Concurrency: Pytest, pytest-asyncio, HTTPX
- Infrastructure: Docker, Docker Compose, WSL 2 (Ubuntu)

## Prerequisites
- Docker & Docker Compose
- Alternatively, Python 3.11+ and PostgreSQL 15+ for local development without containers.

## Steps to Recreate the Entire Project:
step1: Clone the Repo
step2: Run the following command
```
docker compose up -d --build
```
step3: Verify 
```
docker compose ps
```
step4: Access the interactive API documentation at
```
Swagger UI: http://localhost:8000/docs

Health Check: http://localhost:8000/health
```

## Core Design Decisions
1. Stored Balances vs. Computed Balances
- Stored balances on the accounts table provide $O(1)$ read performance and allow database-enforced non-negative balance checks (CHECK (balance >= 0)).
- The ledger_entries table acts as the definitive source of truth.
- The nightly reconciliation worker bridges the two by aggregating ledger rows and reporting discrepancies.

2. Unified Accounts Table
- Both user wallets and platform accounts (cash_in, cash_out, fee_revenue) reside in the accounts table.
- This creates uniform transfer semantics across top-ups, peer transfers, withdrawals, and fee collections using identical double-entry logic and constraints.

3. Isolation Level: Read Committed with Row Locks vs. Serializable
- Transfers run under READ COMMITTED paired with explicit SELECT ... FOR UPDATE row locks acquired in ascending ID order.
- This prevents deadlocks and guarantees balance consistency without incurring the transaction aborts (40001 serialization_failure) and retry penalties characteristic of SERIALIZABLE under heavy row contention.


## API Overview
```
Method     Endpoint                               Description
POST       /wallets                               Create a user wallet
GET        /wallets/{id}/balance                  Fetch account balance
GET        /wallets/{id}/statement                "Keyset paginated statement (cursor_id, limit)"
POST       /wallets/topup                         Top up funds from system cash_in
POST       /wallets/withdraw                      Withdraw funds to system cash_out
POST       /transfers                             Execute peer-to-peer transfer
POST       /admin/transfers/{id}/release          Approve or reject held transfer
POST       /admin/reconciliation/run              Trigger manual reconciliation audit
POST       /users/{id}/statement/ask              Natural language statement query
```

## Demo Video Link

