# app/services/idempotency.py
import hashlib
import json
import psycopg
from psycopg.rows import dict_row

from app.core.errors import WalletAPIException


def compute_request_hash(payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def handle_idempotency_start(conn: psycopg.Connection, user_id: int, key: str, payload: dict):
    req_hash = compute_request_hash(payload)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO idempotency_keys (user_id, key, request_hash, status)
            VALUES (%s, %s, %s, 'processing')
            ON CONFLICT (user_id, key) DO NOTHING
            RETURNING status, request_hash, response_code, response_body;
            """,
            (user_id, key, req_hash),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return None, None

        cur.execute(
            "SELECT status, request_hash, response_code, response_body FROM idempotency_keys WHERE user_id = %s AND key = %s;",
            (user_id, key),
        )
        existing = cur.fetchone()
        if not existing:
            return None, None

        if existing['request_hash'] != req_hash:
            raise WalletAPIException(
                "IDEMPOTENCY_KEY_MISMATCH",
                "Key re-used with different payload.",
                422,
            )
        if existing['status'] == 'processing':
            raise WalletAPIException(
                "REQUEST_IN_FLIGHT",
                "Request is currently being processed.",
                409,
            )
        return existing['response_code'], existing['response_body']


def handle_idempotency_complete(conn: psycopg.Connection, user_id: int, key: str, code: int, body: dict):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE idempotency_keys
            SET status = 'completed', response_code = %s, response_body = %s
            WHERE user_id = %s AND key = %s;
            """,
            (code, json.dumps(body), user_id, key),
        )


def handle_idempotency_failure(
    conn: psycopg.Connection,
    user_id: int,
    key: str,
    payload: dict,
    code: int,
    body: dict,
):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO idempotency_keys
                (user_id, key, request_hash, status, response_code, response_body)
            VALUES (%s, %s, %s, 'completed', %s, %s)
            ON CONFLICT (user_id, key) DO UPDATE
            SET status = 'completed',
                response_code = EXCLUDED.response_code,
                response_body = EXCLUDED.response_body
            WHERE idempotency_keys.request_hash = EXCLUDED.request_hash
            RETURNING id;
            """,
            (
                user_id,
                key,
                compute_request_hash(payload),
                code,
                json.dumps(body),
            ),
        )
        if cur.fetchone() is None:
            raise RuntimeError("Could not store idempotent failure response for a different request payload.")