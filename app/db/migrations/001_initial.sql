-- migrations/001_initial.sql

CREATE TYPE account_type_enum AS ENUM ('user', 'system');
CREATE TYPE transfer_type_enum AS ENUM ('top_up', 'peer_transfer', 'withdrawal', 'fee', 'reversal');
CREATE TYPE transfer_status_enum AS ENUM ('pending', 'completed', 'held_for_review', 'rejected', 'reversed');
CREATE TYPE entry_direction_enum AS ENUM ('debit', 'credit');

-- 1. Users
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(32) UNIQUE NOT NULL,
    kyc_status VARCHAR(32) DEFAULT 'pending' NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- 2. Accounts (Both User Wallets and System Accounts)
CREATE TABLE accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE RESTRICT, -- NULL for system accounts
    account_type account_type_enum NOT NULL,
    name VARCHAR(64) NOT NULL,                              -- 'main', 'savings', 'cash_in', 'cash_out', 'fee_revenue'
    currency VARCHAR(3) DEFAULT 'INR' NOT NULL,
    balance BIGINT DEFAULT 0 NOT NULL,                      -- Stored in paise
    opening_balance BIGINT DEFAULT 0 NOT NULL,               -- Balance before ledger activity
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT chk_non_negative_user_balance CHECK (account_type = 'system' OR balance >= 0),
    CONSTRAINT chk_user_account_ownership CHECK (
        (account_type = 'user' AND user_id IS NOT NULL) OR 
        (account_type = 'system' AND user_id IS NULL)
    )
);

-- 3. Transfers
CREATE TABLE transfers (
    id BIGSERIAL PRIMARY KEY,
    source_account_id BIGINT NOT NULL REFERENCES accounts(id),
    destination_account_id BIGINT NOT NULL REFERENCES accounts(id),
    amount BIGINT NOT NULL CHECK (amount > 0),             -- Absolute value in paise
    type transfer_type_enum NOT NULL,
    status transfer_status_enum DEFAULT 'pending' NOT NULL,
    parent_transfer_id BIGINT REFERENCES transfers(id),     -- For reversals
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- 4. Ledger Entries (Double-Entry: Sum of debits == Sum of credits per transfer)
CREATE TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers(id) ON DELETE RESTRICT,
    account_id BIGINT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    direction entry_direction_enum NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0),             -- Always positive
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Enforce Immutability on Ledger Entries
CREATE OR REPLACE FUNCTION prevent_ledger_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Ledger entries are strictly append-only. Updates and deletes are prohibited.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_ledger_entries
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION prevent_ledger_mutation();

-- 5. Idempotency Keys
CREATE TABLE idempotency_keys (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    key VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,                     -- SHA-256 of the JSON request body
    status VARCHAR(32) NOT NULL,                           -- 'processing', 'completed', 'failed'
    response_code INT,
    response_body JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_user_id_key UNIQUE (user_id, key)
);

-- 6. Audit Logs
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers(id),
    from_status transfer_status_enum NOT NULL,
    to_status transfer_status_enum NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- 7. Risk Rules and Flags
CREATE TABLE risk_rules (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,
    rule_type VARCHAR(64) NOT NULL,
    threshold_value BIGINT NOT NULL,
    action VARCHAR(32) NOT NULL,                           -- 'hold', 'flag'
    is_active BOOLEAN DEFAULT TRUE NOT NULL
);

CREATE TABLE risk_flags (
    id BIGSERIAL PRIMARY KEY,
    transfer_id BIGINT NOT NULL REFERENCES transfers(id),
    rule_name VARCHAR(64) NOT NULL,
    llm_analysis JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- 8. Job Locks & Reconciliation Reports
CREATE TABLE job_locks (
    job_name VARCHAR(64) PRIMARY KEY,
    locked_at TIMESTAMPTZ
);
INSERT INTO job_locks (job_name) VALUES ('nightly_reconciliation');

CREATE TABLE reconciliation_reports (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL,
    problems_count INT DEFAULT 0 NOT NULL,
    details JSONB
);

-- 9. Core Query Indexes
CREATE INDEX idx_ledger_account_created ON ledger_entries(account_id, created_at DESC, id DESC);
CREATE INDEX idx_transfers_source_dest ON transfers(source_account_id, destination_account_id, created_at DESC);
CREATE INDEX idx_transfers_created_at ON transfers(created_at DESC);
CREATE INDEX idx_idempotency_lookup ON idempotency_keys(user_id, key);