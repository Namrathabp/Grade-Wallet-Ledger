# scripts/seed.py
import io
import os
import random
import time
import psycopg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wallet_user:wallet_pass@localhost:5432/wallet_db")

def seed_database():
    start_time = time.time()
    print("Starting high-performance seed process...")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # 1. Seed 1,000 Users
            print("1. Ingesting 1,000 users...")
            user_buffer = io.StringIO()
            for i in range(1, 1001):
                user_buffer.write(f"User {i}\tuser_{i}@wallet.internal\t+9198000{i:05d}\tverified\tNOW()\n")
            user_buffer.seek(0)
            with cur.copy("COPY users (name, email, phone, kyc_status, created_at) FROM STDIN") as copy:
                copy.write(user_buffer.read())

            cur.execute("SELECT id FROM users ORDER BY id ASC;")
            user_ids = [row[0] for row in cur.fetchall()]

            # 2. Seed 2,000 Wallets (2 wallets per user: Main and Savings)
            print("2. Ingesting 2,000 wallets...")
            wallet_buffer = io.StringIO()
            for uid in user_ids:
                wallet_buffer.write(f"{uid}\tuser\tMain\tINR\t10000000\tNOW()\n")     # ₹100,000 initial balance
                wallet_buffer.write(f"{uid}\tuser\tSavings\tINR\t10000000\tNOW()\n")  # ₹100,000 initial balance
            wallet_buffer.seek(0)
            with cur.copy("COPY accounts (user_id, account_type, name, currency, balance, created_at) FROM STDIN") as copy:
                copy.write(wallet_buffer.read())

            cur.execute("SELECT id FROM accounts WHERE account_type = 'user' ORDER BY id ASC;")
            account_ids = [row[0] for row in cur.fetchall()]
            num_accounts = len(account_ids)

            # 3. Seed 100,000 Balanced Transfers & 200,000 Ledger Entries
            print("3. Generating 100,000 transfers and 200,000 ledger entries...")
            transfers_buffer = io.StringIO()
            ledger_buffer = io.StringIO()

            cur.execute("SELECT COALESCE(MAX(id), 0) FROM transfers;")
            transfer_start_id = cur.fetchone()[0] + 1

            cur.execute("SELECT COALESCE(MAX(id), 0) FROM ledger_entries;")
            ledger_start_id = cur.fetchone()[0] + 1

            for i in range(100000):
                t_id = transfer_start_id + i
                src_acc = account_ids[random.randint(0, num_accounts - 1)]
                dst_acc = account_ids[random.randint(0, num_accounts - 1)]
                while dst_acc == src_acc:
                    dst_acc = account_ids[random.randint(0, num_accounts - 1)]

                amount = random.randint(100, 50000)  # ₹1.00 to ₹500.00
                transfers_buffer.write(f"{t_id}\t{src_acc}\t{dst_acc}\t{amount}\tpeer_transfer\tcompleted\tSeed transfer {i}\tNOW()\n")

                l_debit_id = ledger_start_id + (i * 2)
                l_credit_id = l_debit_id + 1
                ledger_buffer.write(f"{l_debit_id}\t{t_id}\t{src_acc}\tdebit\t{amount}\tNOW()\n")
                ledger_buffer.write(f"{l_credit_id}\t{t_id}\t{dst_acc}\tcredit\t{amount}\tNOW()\n")

            transfers_buffer.seek(0)
            with cur.copy("COPY transfers (id, source_account_id, destination_account_id, amount, type, status, note, created_at) FROM STDIN") as copy:
                copy.write(transfers_buffer.read())

            ledger_buffer.seek(0)
            with cur.copy("COPY ledger_entries (id, transfer_id, account_id, direction, amount, created_at) FROM STDIN") as copy:
                copy.write(ledger_buffer.read())

            # Synchronize PostgreSQL sequences after explicit ID inserts
            cur.execute("SELECT setval('transfers_id_seq', (SELECT MAX(id) FROM transfers));")
            cur.execute("SELECT setval('ledger_entries_id_seq', (SELECT MAX(id) FROM ledger_entries));")

            # 4. Synchronize Account Balances to match ledger sums
            print("4. Synchronizing account balance aggregates...")
            cur.execute("""
                WITH calculated AS (
                    SELECT account_id,
                           SUM(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) AS net
                    FROM ledger_entries
                    GROUP BY account_id
                )
                UPDATE accounts a
                SET balance = 10000000 + COALESCE(c.net, 0)
                FROM calculated c
                WHERE a.id = c.account_id AND a.account_type = 'user';
            """)

        conn.commit()

    print(f"Seed finished in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    seed_database()