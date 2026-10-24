# app/services/transfer.py
import psycopg
from psycopg.rows import dict_row

class InsufficientFundsException(Exception):
    pass

class TransferHeldException(Exception):
    def __init__(self, transfer_id: int):
        self.transfer_id = transfer_id

def execute_transfer(
    conn: psycopg.Connection, 
    source_id: int, 
    destination_id: int, 
    amount_paise: int, 
    transfer_type: str, 
    note: str = None
) -> dict:
    """
    Executes a transfer under Read Committed using row-level locks.
    Locks are acquired in ascending ID order to prevent deadlocks.
    """
    # 1. Order IDs to avoid deadlocks
    first_id, second_id = (source_id, destination_id) if source_id < destination_id else (destination_id, source_id)

    with conn.cursor(row_factory=dict_row) as cur:
        # 2. Acquire row-level locks
        cur.execute(
            "SELECT id, balance, account_type FROM accounts WHERE id IN (%s, %s) ORDER BY id ASC FOR UPDATE;",
            (first_id, second_id)
        )
        locked_accounts = {row['id']: row for row in cur.fetchall()}

        source_acc = locked_accounts[source_id]
        dest_acc = locked_accounts[destination_id]

        # 3. Balance verification
        if source_acc['account_type'] == 'user' and source_acc['balance'] < amount_paise:
            raise InsufficientFundsException("Insufficient balance.")

        # 4. Pre-commit risk rule checks
        cur.execute("SELECT threshold_value, action FROM risk_rules WHERE rule_type = 'high_amount' AND is_active = TRUE;")
        rule = cur.fetchone()
        is_held = rule and amount_paise > rule['threshold_value']

        # 5. Insert transfer record
        status = 'held_for_review' if is_held else 'completed'
        cur.execute(
            """
            INSERT INTO transfers (source_account_id, destination_account_id, amount, type, status, note)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, created_at;
            """,
            (source_id, destination_id, amount_paise, transfer_type, status, note)
        )
        transfer = cur.fetchone()
        transfer_id = transfer['id']

        if is_held:
            cur.execute(
                "INSERT INTO risk_flags (transfer_id, rule_name) VALUES (%s, %s);",
                (transfer_id, 'high_amount')
            )
            # Money does not move while held
            return {"transfer_id": transfer_id, "status": "held_for_review", "amount": amount_paise}

        # 6. Apply updates: Debit source, Credit destination
        cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s;", (amount_paise, source_id))
        cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s;", (amount_paise, destination_id))

        # 7. Insert balanced ledger entries
        cur.execute(
            """
            INSERT INTO ledger_entries (transfer_id, account_id, direction, amount)
            VALUES (%s, %s, 'debit', %s), (%s, %s, 'credit', %s);
            """,
            (transfer_id, source_id, amount_paise, transfer_id, destination_id, amount_paise)
        )

        return {"transfer_id": transfer_id, "status": "completed", "amount": amount_paise}