import os
import sys
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wallet_user:wallet_pass@localhost:5432/wallet_db")

def run_migrations():
    print("Connecting to database for automated schema initialization...")
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.accounts') AS accounts_table;")
            if cur.fetchone()[0] is None:
                with open("app/db/migrations/001_initial.sql", "r") as f:
                    cur.execute(f.read())

            with open("app/db/migrations/002_account_opening_balance.sql", "r") as f:
                cur.execute(f.read())
            
            # 2. Guarantee System Accounts Exist
            cur.execute("""
                INSERT INTO accounts (account_type, name, currency, balance)
                VALUES 
                    ('system', 'cash_in', 'INR', 0),
                    ('system', 'cash_out', 'INR', 0),
                    ('system', 'fee_revenue', 'INR', 0)
                ON CONFLICT DO NOTHING;
            """)

            # 3. Insert Default Risk Rules
            cur.execute("""
                INSERT INTO risk_rules (name, rule_type, threshold_value, action, is_active)
                VALUES 
                    ('high_value_transfer', 'high_amount', 10000000, 'hold', TRUE) -- ₹100,000 threshold
                ON CONFLICT (name) DO NOTHING;
            """)

            # 4. Ensure Job Locks Row Exists
            cur.execute("""
                INSERT INTO job_locks (job_name) 
                VALUES ('nightly_reconciliation') 
                ON CONFLICT DO NOTHING;
            """)
        conn.commit()
    print("Schema initialized successfully.")

if __name__ == "__main__":
    try:
        run_migrations()
    except Exception as e:
        print(f"Migration error: {e}", file=sys.stderr)
        sys.exit(1)