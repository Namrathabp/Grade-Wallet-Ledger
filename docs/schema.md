# Database Schema Design & ERD

## 1. Noun Analysis & Table Decisions

| Noun | Table? | Reason |
|---|---|---|
| **User** | Yes (`users`) | Stores KYC status, contact info, and identity. |
| **Wallet / System Account** | Yes (`accounts`) | Core entity holding balance and ownership. User wallets and system accounts share one table to make double-entry math symmetrical. |
| **Transfer** | Yes (`transfers`) | The business event documenting the intent to move money. |
| **Ledger Entry** | Yes (`ledger_entries`) | Atomic debit/credit records. Append-only source of truth. |
| **Idempotency Key** | Yes (`idempotency_keys`) | Enforces exact-once semantics on API write requests. |
| **Audit Log** | Yes (`audit_logs`) | Documents administrative state changes and approvals. |
| **Risk Rule / Flag** | Yes (`risk_rules`, `risk_flags`) | Manages pre-commit transaction thresholds and holds. |
| **Currency** | No | Fixed to 'INR' per requirement 2. Stored as column attribute. |
| **Amount** | No | Stored as a positive `BIGINT` column in whole paise. |

## 2. Cardinality Decisions
- **User to Accounts:** `1 : M` (A user can have a Main wallet and a Savings wallet).
- **Transfer to Ledger Entries:** `1 : M` (Every transfer writes 2 or more balanced entries).
- **Transfer to Reversal Transfer:** `1 : 1` (Self-referencing foreign key `parent_transfer_id`).
- **User to Idempotency Keys:** `1 : M` (Unique constraint on `(user_id, key)`).

## 3. Entity-Relationship Diagram

```mermaid
erDiagram
    users ||--o{ accounts : owns
    users ||--o{ idempotency_keys : submits
    transfers ||--|{ ledger_entries : generates
    accounts ||--o{ ledger_entries : records
    transfers ||--o{ audit_logs : tracks
    transfers ||--o{ risk_flags : flags
    transfers ||--o| transfers : reverses

    users {
        bigint id PK
        varchar name
        varchar email UK
        varchar phone UK
        varchar kyc_status
    }
    accounts {
        bigint id PK
        bigint user_id FK
        account_type_enum account_type
        varchar name
        bigint opening_balance
        bigint balance
    }
    transfers {
        bigint id PK
        bigint source_account_id FK
        bigint destination_account_id FK
        bigint amount
        transfer_status_enum status
    }
    ledger_entries {
        bigint id PK
        bigint transfer_id FK
        bigint account_id FK
        entry_direction_enum direction
        bigint amount
    }