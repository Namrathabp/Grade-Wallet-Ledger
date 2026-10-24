ALTER TABLE accounts
ADD COLUMN IF NOT EXISTS opening_balance BIGINT;

UPDATE accounts a
SET opening_balance = a.balance - COALESCE(
    (
        SELECT SUM(
            CASE
                WHEN le.direction = 'credit' THEN le.amount
                ELSE -le.amount
            END
        )
        FROM ledger_entries le
        WHERE le.account_id = a.id
    ),
    0
)
WHERE a.opening_balance IS NULL;

ALTER TABLE accounts
ALTER COLUMN opening_balance SET DEFAULT 0,
ALTER COLUMN opening_balance SET NOT NULL;

CREATE OR REPLACE FUNCTION set_account_opening_balance()
RETURNS TRIGGER AS $$
BEGIN
    NEW.opening_balance := NEW.balance;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_account_opening_balance ON accounts;
CREATE TRIGGER trg_set_account_opening_balance
BEFORE INSERT ON accounts
FOR EACH ROW EXECUTE FUNCTION set_account_opening_balance();
