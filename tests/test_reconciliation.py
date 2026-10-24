# tests/test_reconciliation.py
import os
import uuid
import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://wallet_user:wallet_pass@localhost:5432/wallet_db"
)

def setup_baseline_data(conn: psycopg.Connection, num_transfers: int = 100):
    """
    Seeds test users, wallets, and valid balanced transfers.
    Returns: list of transfer IDs and list of account IDs created.
    """
    transfer_ids = []
    account_ids = []

    with conn.cursor() as cur:
        # Create 2 users
        unique_suffix = uuid.uuid4().hex[:8]
        cur.execute(
            """
            INSERT INTO users (name, email, phone, kyc_status)
            VALUES 
                (%s, %s, %s, 'verified'),
                (%s, %s, %s, 'verified')
            RETURNING id;
            """,
            (
                f"Audit User 1 {unique_suffix}",
                f"audit_1_{unique_suffix}@test.com",
                f"+91{unique_suffix}31",
                f"Audit User 2 {unique_suffix}",
                f"audit_2_{unique_suffix}@test.com",
                f"+91{unique_suffix}32",
            )
        )
        user_rows = cur.fetchall()
        u1, u2 = user_rows[0]["id"], user_rows[1]["id"]

        # Create wallets with starting balance
        initial_balance = 10000000  # 100,000 INR in paise
        cur.execute(
            """
            INSERT INTO accounts (user_id, account_type, name, currency, balance)
            VALUES 
                (%s, 'user', 'Main', 'INR', %s),
                (%s, 'user', 'Main', 'INR', %s)
            RETURNING id;
            """,
            (u1, initial_balance, u2, initial_balance)
        )
        acc_rows = cur.fetchall()
        a1, a2 = acc_rows[0]["id"], acc_rows[1]["id"]
        account_ids.extend([a1, a2])

        # Seed balanced transfers
        transfer_amount = 5000  # 50 INR in paise
        for i in range(num_transfers):
            cur.execute(
                """
                INSERT INTO transfers (source_account_id, destination_account_id, amount, type, status, note)
                VALUES (%s, %s, %s, 'peer_transfer', 'completed', %s)
                RETURNING id;
                """,
                (a1, a2, transfer_amount, f"Valid transfer {i}")
            )
            t_id = cur.fetchone()["id"]
            transfer_ids.append(t_id)

            cur.execute(
                """
                INSERT INTO ledger_entries (transfer_id, account_id, direction, amount)
                VALUES 
                    (%s, %s, 'debit', %s),
                    (%s, %s, 'credit', %s);
                """,
                (t_id, a1, transfer_amount, t_id, a2, transfer_amount)
            )

            cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s;", (transfer_amount, a1))
            cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s;", (transfer_amount, a2))

    conn.commit()
    return transfer_ids, account_ids


def cleanup_previous_test_data():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM users
                WHERE name LIKE 'Audit User 1 %'
                   OR name LIKE 'Audit User 2 %'
                   OR email LIKE 'caller!_%@test.com' ESCAPE '!'
                   OR email LIKE 'rahul!_%@test.com' ESCAPE '!'
                   OR email LIKE 'victim!_%@test.com' ESCAPE '!';
                """
            )
            user_ids = [row["id"] for row in cur.fetchall()]
            if not user_ids:
                return

            cur.execute("SET session_replication_role = 'replica';")
            cur.execute(
                """
                DELETE FROM ledger_entries
                WHERE transfer_id IN (
                    SELECT id FROM transfers
                    WHERE source_account_id IN (
                        SELECT id FROM accounts WHERE user_id = ANY(%s)
                    )
                    OR destination_account_id IN (
                        SELECT id FROM accounts WHERE user_id = ANY(%s)
                    )
                );
                """,
                (user_ids, user_ids),
            )
            cur.execute(
                """
                DELETE FROM transfers
                WHERE source_account_id IN (
                    SELECT id FROM accounts WHERE user_id = ANY(%s)
                )
                OR destination_account_id IN (
                    SELECT id FROM accounts WHERE user_id = ANY(%s)
                );
                """,
                (user_ids, user_ids),
            )
            cur.execute("DELETE FROM accounts WHERE user_id = ANY(%s);", (user_ids,))
            cur.execute("DELETE FROM users WHERE id = ANY(%s);", (user_ids,))
            cur.execute("SET session_replication_role = 'origin';")
        conn.commit()


@pytest.mark.asyncio
async def test_reconciliation_detects_corruptions():
    """
    Milestone 4 Acceptance Test:
    1. Seed valid transfer data.
    2. Confirm clean audit run.
    3. Inject exactly 3 database anomalies:
       - Corruption A: Alter one ledger entry amount.
       - Corruption B: Delete one credit entry.
       - Corruption C: Tamper with one account's stored balance directly.
    4. Execute reconciliation and verify exactly those 3 problems are flagged.
    """
    cleanup_previous_test_data()
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        transfer_ids, account_ids = setup_baseline_data(conn, num_transfers=50)

    # Step 1: Run clean audit before corruption
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_BASE_URL}/admin/reconciliation/run")
        assert resp.status_code == 200, f"Reconciliation API failed: {resp.text}"
        data = resp.json()
        assert data["problems_detected"] == 0, f"Expected 0 anomalies before corruption, got {data}"

    # Step 2: Inject corruptions directly into PostgreSQL
    corrupted_transfer_1 = transfer_ids[0]
    corrupted_transfer_2 = transfer_ids[1]
    corrupted_account = account_ids[0]

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # Bypass immutability triggers for controlled corruption injection
            cur.execute("SET session_replication_role = 'replica';")

            # Corruption A: Alter one ledger entry's amount
            cur.execute(
                """
                UPDATE ledger_entries 
                SET amount = amount + 999 
                WHERE transfer_id = %s AND direction = 'debit';
                """,
                (corrupted_transfer_1,)
            )

            # Corruption B: Delete one credit entry
            cur.execute(
                """
                DELETE FROM ledger_entries 
                WHERE id = (
                    SELECT id FROM ledger_entries 
                    WHERE transfer_id = %s AND direction = 'credit' 
                    LIMIT 1
                );
                """,
                (corrupted_transfer_2,)
            )

            # Corruption C: Change one stored account balance directly
            cur.execute(
                """
                UPDATE accounts 
                SET balance = balance + 12345 
                WHERE id = %s;
                """,
                (corrupted_account,)
            )

            # Restore normal trigger operation
            cur.execute("SET session_replication_role = 'origin';")
        conn.commit()

    # Step 3: Trigger reconciliation via API
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(f"{API_BASE_URL}/admin/reconciliation/run")
        assert resp.status_code == 200, f"Reconciliation endpoint failed: {resp.text}"
        report = resp.json()

    # Step 4: Verify problems reported
    problems = report.get("problems", [])
    problem_checks = [p["check"] for p in problems]

    # Verify overall balance failure
    assert "total_ledger_parity" in problem_checks, "Audit failed to detect global debit/credit parity mismatch"

    # Verify Corruption A & B: Unbalanced transfers
    unbalanced_transfer_ids = [p["transfer_id"] for p in problems if p["check"] == "unbalanced_transfer"]
    assert corrupted_transfer_1 in unbalanced_transfer_ids, (
        f"Audit missed altered ledger amount on transfer {corrupted_transfer_1}"
    )
    assert corrupted_transfer_2 in unbalanced_transfer_ids, (
        f"Audit missed missing credit on transfer {corrupted_transfer_2}"
    )

    # Verify Corruption C: Account balance mismatch
    mismatched_account_ids = [p["account_id"] for p in problems if p["check"] == "balance_mismatch"]
    assert corrupted_account in mismatched_account_ids, (
        f"Audit missed tampered balance on account {corrupted_account}"
    )

    # Step 5: Verify report record persistence in database
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, problems_count, details 
                FROM reconciliation_reports 
                ORDER BY id DESC LIMIT 1;
                """
            )
            saved_report = cur.fetchone()
            assert saved_report["status"] == "failed"
            assert saved_report["problems_count"] == len(problems)

    cleanup_previous_test_data()