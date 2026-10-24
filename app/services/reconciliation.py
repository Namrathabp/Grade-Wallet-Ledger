# app/services/reconciliation.py
import json
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row


def _json_default(value):
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def run_reconciliation(conn_factory):
    with conn_factory.connection() as lock_conn:
        # Acquire advisory/table lock with NOWAIT
        with lock_conn.cursor() as cur:
            try:
                cur.execute("SELECT * FROM job_locks WHERE job_name = 'nightly_reconciliation' FOR UPDATE NOWAIT;")
            except psycopg.errors.LockNotAvailable:
                return {"status": "skipped", "reason": "Another reconciliation is actively executing."}

            with conn_factory.connection() as audit_conn:
                with audit_conn.cursor() as isolation_cur:
                    isolation_cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;")
                with audit_conn.cursor(row_factory=dict_row) as audit_cur:
                    problems = []

                    # 1. Total debits must equal total credits
                    audit_cur.execute(
                        """
                        SELECT 
                            SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) AS total_debit,
                            SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END) AS total_credit
                        FROM ledger_entries;
                        """
                    )
                    res = audit_cur.fetchone()
                    if (res['total_debit'] or 0) != (res['total_credit'] or 0):
                        problems.append({
                            "check": "total_ledger_parity",
                            "expected": res['total_debit'],
                            "actual": res['total_credit']
                        })

                    # 2. Every individual transfer must balance
                    audit_cur.execute(
                        """
                        SELECT transfer_id,
                               SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) AS d,
                               SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END) AS c
                        FROM ledger_entries
                        GROUP BY transfer_id
                        HAVING SUM(CASE WHEN direction = 'debit' THEN amount ELSE 0 END) != 
                               SUM(CASE WHEN direction = 'credit' THEN amount ELSE 0 END);
                        """
                    )
                    unbalanced = audit_cur.fetchall()
                    for row in unbalanced:
                        problems.append({
                            "check": "unbalanced_transfer",
                            "transfer_id": row['transfer_id'],
                            "debits": row['d'],
                            "credits": row['c']
                        })

                    # 3. Stored balances must match opening balances plus ledger activity
                    audit_cur.execute(
                        """
                        WITH calculated AS (
                            SELECT account_id,
                                   SUM(CASE WHEN direction = 'credit' THEN amount ELSE -amount END) AS computed_balance
                            FROM ledger_entries
                            GROUP BY account_id
                        )
                        SELECT a.id,
                               a.balance AS stored,
                               a.opening_balance + COALESCE(c.computed_balance, 0) AS computed
                        FROM accounts a
                        LEFT JOIN calculated c ON a.id = c.account_id
                        WHERE a.balance != a.opening_balance + COALESCE(c.computed_balance, 0);
                        """
                    )
                    balance_mismatches = audit_cur.fetchall()
                    for row in balance_mismatches:
                        problems.append({
                            "check": "balance_mismatch",
                            "account_id": row['id'],
                            "stored": row['stored'],
                            "computed": row['computed']
                        })

                    # Record report
                    audit_cur.execute(
                        """
                        INSERT INTO reconciliation_reports (started_at, ended_at, status, problems_count, details)
                        VALUES (NOW(), NOW(), %s, %s, %s) RETURNING id;
                        """,
                        ('failed' if problems else 'passed', len(problems), json.dumps(problems, default=_json_default))
                    )
                    audit_conn.commit()

            lock_conn.commit()
    return {"status": "completed", "problems_detected": len(problems), "problems": problems}