from fastapi import APIRouter, Depends
from pydantic import BaseModel
import psycopg

from app.db.session import pool, get_db_connection
from app.core.errors import WalletAPIException
from app.services.reconciliation import run_reconciliation

router = APIRouter(tags=["Admin"])

class ReleaseTransferRequest(BaseModel):
    action: str  # 'approve' or 'reject'
    actor: str
    reason: str

@router.post("/admin/reconciliation/run")
def trigger_reconciliation():
    summary = run_reconciliation(pool)
    return summary

@router.post("/admin/transfers/{transfer_id}/release")
def release_held_transfer(
    transfer_id: int,
    payload: ReleaseTransferRequest,
    conn: psycopg.Connection = Depends(get_db_connection)
):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM transfers WHERE id = %s FOR UPDATE;", (transfer_id,))
        t = cur.fetchone()
        if not t:
            raise WalletAPIException("TRANSFER_NOT_FOUND", "Transfer not found", 404)
        if t["status"] != "held_for_review":
            raise WalletAPIException("INVALID_STATE", f"Transfer is in state {t['status']}, not held", 400)

        new_status = "completed" if payload.action == "approve" else "rejected"

        if new_status == "completed":
            # Apply balance updates & ledger rows
            cur.execute("UPDATE accounts SET balance = balance - %s WHERE id = %s;", (t["amount"], t["source_account_id"]))
            cur.execute("UPDATE accounts SET balance = balance + %s WHERE id = %s;", (t["amount"], t["destination_account_id"]))
            cur.execute(
                """
                INSERT INTO ledger_entries (transfer_id, account_id, direction, amount)
                VALUES (%s, %s, 'debit', %s), (%s, %s, 'credit', %s);
                """,
                (t["id"], t["source_account_id"], t["amount"], t["id"], t["destination_account_id"], t["amount"])
            )

        cur.execute("UPDATE transfers SET status = %s WHERE id = %s;", (new_status, transfer_id))
        cur.execute(
            """
            INSERT INTO audit_logs (transfer_id, from_status, to_status, actor, reason)
            VALUES (%s, 'held_for_review', %s, %s, %s);
            """,
            (transfer_id, new_status, payload.actor, payload.reason)
        )
        conn.commit()

        return {"transfer_id": transfer_id, "status": new_status}