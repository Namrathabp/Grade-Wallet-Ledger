from typing import Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import psycopg

from app.db.session import get_db_connection
from app.core.errors import WalletAPIException
from app.services.transfer import InsufficientFundsException, execute_transfer
from app.services.idempotency import (
    handle_idempotency_complete,
    handle_idempotency_failure,
    handle_idempotency_start,
)

router = APIRouter(tags=["Wallets & Transfers"])

class CreateWalletRequest(BaseModel):
    user_id: int
    name: str = Field(..., max_length=64)

class TransferRequest(BaseModel):
    source_account_id: int
    destination_account_id: int
    amount: int = Field(..., gt=0, description="Amount in whole paise")
    note: Optional[str] = None

class TopUpRequest(BaseModel):
    account_id: int
    amount: int = Field(..., gt=0, description="Amount in whole paise")

class WithdrawRequest(BaseModel):
    account_id: int
    amount: int = Field(..., gt=0, description="Amount in whole paise")

@router.post("/wallets", status_code=201)
def create_wallet(
    req: CreateWalletRequest,
    conn: psycopg.Connection = Depends(get_db_connection)
):
    with conn.cursor() as cur:
        # Check user existence
        cur.execute("SELECT id FROM users WHERE id = %s;", (req.user_id,))
        if not cur.fetchone():
            raise WalletAPIException("USER_NOT_FOUND", "Specified user does not exist", 404)

        cur.execute(
            """
            INSERT INTO accounts (user_id, account_type, name, currency, balance)
            VALUES (%s, 'user', %s, 'INR', 0)
            RETURNING id, user_id, name, currency, balance, created_at;
            """,
            (req.user_id, req.name)
        )
        wallet = cur.fetchone()
        conn.commit()
        return wallet

@router.get("/wallets/{account_id}/balance")
def get_balance(
    account_id: int,
    conn: psycopg.Connection = Depends(get_db_connection)
):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, balance, currency, account_type FROM accounts WHERE id = %s;",
            (account_id,)
        )
        account = cur.fetchone()
        if not account:
            raise WalletAPIException("ACCOUNT_NOT_FOUND", "Account does not exist", 404)
        return account

@router.post("/transfers")
def transfer_money(
    payload: TransferRequest,
    request: Request,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    conn: psycopg.Connection = Depends(get_db_connection)
):
    if not idempotency_key:
        raise WalletAPIException("MISSING_IDEMPOTENCY_KEY", "Idempotency-Key header is mandatory", 400)

    # Determine user context by resolving source account owner
    with conn.cursor() as cur:
        cur.execute("SELECT user_id, account_type FROM accounts WHERE id = %s;", (payload.source_account_id,))
        src_row = cur.fetchone()
        if not src_row:
            raise WalletAPIException("SOURCE_ACCOUNT_NOT_FOUND", "Source account does not exist", 404)
        user_id = src_row["user_id"] or 0

    try:
        # Idempotency pre-check
        raw_payload = payload.model_dump()
        saved_code, saved_body = handle_idempotency_start(conn, user_id, idempotency_key, raw_payload)
        if saved_code is not None:
            return JSONResponse(content=saved_body, status_code=saved_code)

        result = execute_transfer(
            conn=conn,
            source_id=payload.source_account_id,
            destination_id=payload.destination_account_id,
            amount_paise=payload.amount,
            transfer_type="peer_transfer",
            note=payload.note
        )
        conn.commit()
        handle_idempotency_complete(conn, user_id, idempotency_key, 200, result)
        conn.commit()
        return result
    except WalletAPIException:
        conn.rollback()
        raise
    except InsufficientFundsException:
        conn.rollback()
        err_resp = {"code": "INSUFFICIENT_FUNDS", "message": "Balance is insufficient for transfer"}
        handle_idempotency_failure(
            conn, user_id, idempotency_key, raw_payload, 400, err_resp
        )
        conn.commit()
        raise WalletAPIException("INSUFFICIENT_FUNDS", "Balance is insufficient for transfer", 400)
    except Exception:
        conn.rollback()
        raise

@router.post("/wallets/topup")
def topup_wallet(
    payload: TopUpRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    conn: psycopg.Connection = Depends(get_db_connection)
):
    if not idempotency_key:
        raise WalletAPIException("MISSING_IDEMPOTENCY_KEY", "Idempotency-Key header is mandatory", 400)

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE account_type = 'system' AND name = 'cash_in' LIMIT 1;")
        cash_in = cur.fetchone()
        cur.execute("SELECT user_id FROM accounts WHERE id = %s;", (payload.account_id,))
        wallet = cur.fetchone()

    if not cash_in or not wallet:
        raise WalletAPIException("SETUP_ERROR", "Target wallet or Cash-In account not found", 404)

    raw_payload = payload.model_dump()
    saved_code, saved_body = handle_idempotency_start(conn, wallet["user_id"], idempotency_key, raw_payload)
    if saved_code is not None:
        return JSONResponse(content=saved_body, status_code=saved_code)

    result = execute_transfer(
        conn=conn,
        source_id=cash_in["id"],
        destination_id=payload.account_id,
        amount_paise=payload.amount,
        transfer_type="top_up",
        note="Top-up from external gateway"
    )
    conn.commit()
    handle_idempotency_complete(conn, wallet["user_id"], idempotency_key, 200, result)
    conn.commit()
    return result

@router.post("/wallets/withdraw")
def withdraw_wallet(
    payload: WithdrawRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    conn: psycopg.Connection = Depends(get_db_connection)
):
    if not idempotency_key:
        raise WalletAPIException("MISSING_IDEMPOTENCY_KEY", "Idempotency-Key header is mandatory", 400)

    with conn.cursor() as cur:
        cur.execute("SELECT id FROM accounts WHERE account_type = 'system' AND name = 'cash_out' LIMIT 1;")
        cash_out = cur.fetchone()
        cur.execute("SELECT user_id FROM accounts WHERE id = %s;", (payload.account_id,))
        wallet = cur.fetchone()

    if not cash_out or not wallet:
        raise WalletAPIException("SETUP_ERROR", "Target wallet or Cash-Out account not found", 404)

    raw_payload = payload.model_dump()
    saved_code, saved_body = handle_idempotency_start(conn, wallet["user_id"], idempotency_key, raw_payload)
    if saved_code is not None:
        return JSONResponse(content=saved_body, status_code=saved_code)

    result = execute_transfer(
        conn=conn,
        source_id=payload.account_id,
        destination_id=cash_out["id"],
        amount_paise=payload.amount,
        transfer_type="withdrawal",
        note="Withdrawal to linked bank"
    )
    conn.commit()
    handle_idempotency_complete(conn, wallet["user_id"], idempotency_key, 200, result)
    conn.commit()
    return result

@router.get("/wallets/{account_id}/statement")
def get_statement(
    account_id: int,
    limit: int = Query(20, ge=1, le=100),
    cursor_id: Optional[int] = Query(None, description="Cursor pagination: entry id cutoff"),
    conn: psycopg.Connection = Depends(get_db_connection)
):
    """
    High-performance keyset pagination utilizing (account_id, id DESC).
    """
    with conn.cursor() as cur:
        if cursor_id:
            query = """
                SELECT id, transfer_id, account_id, direction, amount, created_at
                FROM ledger_entries
                WHERE account_id = %s AND id < %s
                ORDER BY id DESC
                LIMIT %s;
            """
            cur.execute(query, (account_id, cursor_id, limit))
        else:
            query = """
                SELECT id, transfer_id, account_id, direction, amount, created_at
                FROM ledger_entries
                WHERE account_id = %s
                ORDER BY id DESC
                LIMIT %s;
            """
            cur.execute(query, (account_id, limit))
        
        entries = cur.fetchall()
        next_cursor = entries[-1]["id"] if len(entries) == limit else None
        return {
            "account_id": account_id,
            "entries": entries,
            "next_cursor": next_cursor
        }