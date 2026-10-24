# tests/test_idempotency.py
import asyncio
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

def setup_idempotency_accounts(initial_balance_paise: int = 100000):
    """
    Sets up two isolated users and wallets for idempotency testing.
    Returns: (source_account_id, destination_account_id)
    """
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
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
                    f"Idem Sender {unique_suffix}",
                    f"idem_sender_{unique_suffix}@test.com",
                    f"+91{unique_suffix}11",
                    f"Idem Receiver {unique_suffix}",
                    f"idem_receiver_{unique_suffix}@test.com",
                    f"+91{unique_suffix}12",
                ),
            )
            users = cur.fetchall()
            user_a_id = users[0]["id"]
            user_b_id = users[1]["id"]

            cur.execute(
                """
                INSERT INTO accounts (user_id, account_type, name, currency, balance)
                VALUES 
                    (%s, 'user', 'Main', 'INR', %s),
                    (%s, 'user', 'Main', 'INR', 0)
                RETURNING id;
                """,
                (user_a_id, initial_balance_paise, user_b_id),
            )
            accounts = cur.fetchall()
            src_id = accounts[0]["id"]
            dst_id = accounts[1]["id"]
        conn.commit()
    return src_id, dst_id

@pytest.mark.asyncio
async def test_sequential_replays():
    """
    Acceptance Test 1:
    The same request sent 5 times in a row creates 1 transfer and returns 5 identical responses.
    """
    src_id, dst_id = setup_idempotency_accounts(initial_balance_paise=50000)
    idempotency_key = str(uuid.uuid4())
    payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount": 2000,  # ₹20
        "note": "Sequential replay test",
    }
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }

    responses = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for _ in range(5):
            res = await client.post(f"{API_BASE_URL}/transfers", json=payload, headers=headers)
            responses.append(res)

    # 1. Verify status codes are all 200
    for res in responses:
        assert res.status_code == 200, f"Expected 200 OK, got {res.status_code}: {res.text}"

    # 2. Verify all response bodies are identical
    first_body = responses[0].json()
    for res in responses[1:]:
        assert res.json() == first_body, "Replayed response body differed from original response"

    # 3. Verify exactly one transfer and one set of ledger entries exist in database
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM transfers WHERE source_account_id = %s;", (src_id,))
            transfer_count = cur.fetchone()["cnt"]
            assert transfer_count == 1, f"Expected 1 transfer record, found {transfer_count}"

            cur.execute("SELECT balance FROM accounts WHERE id = %s;", (src_id,))
            final_balance = cur.fetchone()["balance"]
            assert final_balance == 48000, f"Expected balance 48000 paise, found {final_balance}"

@pytest.mark.asyncio
async def test_parallel_replays_race():
    """
    Acceptance Test 2:
    The exact same key sent 10 times in parallel still creates exactly 1 transfer.
    """
    src_id, dst_id = setup_idempotency_accounts(initial_balance_paise=50000)
    idempotency_key = str(uuid.uuid4())
    payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount": 5000,  # ₹50
        "note": "Parallel replay test",
    }
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency_key,
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = [
            client.post(f"{API_BASE_URL}/transfers", json=payload, headers=headers)
            for _ in range(10)
        ]
        responses = await asyncio.gather(*tasks)

    # Responses must either be 200 (completed/served from cache) or 409 (if request was caught in-flight)
    success_responses = [r for r in responses if r.status_code == 200]
    assert len(success_responses) >= 1, "At least one request must succeed with 200 OK"

    first_transfer_id = success_responses[0].json()["transfer_id"]
    for r in success_responses:
        assert r.json()["transfer_id"] == first_transfer_id, "Inconsistent transfer IDs among parallel responses"

    # Verify only one transfer committed and balance was charged once
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM transfers WHERE source_account_id = %s;", (src_id,))
            transfer_count = cur.fetchone()["cnt"]
            assert transfer_count == 1, f"Expected 1 transfer, found {transfer_count}"

            cur.execute("SELECT balance FROM accounts WHERE id = %s;", (src_id,))
            final_balance = cur.fetchone()["balance"]
            assert final_balance == 45000, f"Expected balance 45000 paise, found {final_balance}"

@pytest.mark.asyncio
async def test_key_reuse_different_payload_fails():
    """
    Acceptance Test 3:
    The same key with a different amount (or destination) returns 422, and no money moves.
    """
    src_id, dst_id = setup_idempotency_accounts(initial_balance_paise=50000)
    idempotency_key = str(uuid.uuid4())
    
    initial_payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount": 3000,  # ₹30
        "note": "Original payload",
    }
    mismatched_payload = {
        "source_account_id": src_id,
        "destination_account_id": dst_id,
        "amount": 9000,  # ₹90 (Different amount!)
        "note": "Tampered payload with same key",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Send the first legitimate request
        res1 = await client.post(
            f"{API_BASE_URL}/transfers", 
            json=initial_payload, 
            headers={"Idempotency-Key": idempotency_key}
        )
        assert res1.status_code == 200, f"Original request failed: {res1.text}"

        # 2. Resend with the same key but different body
        res2 = await client.post(
            f"{API_BASE_URL}/transfers", 
            json=mismatched_payload, 
            headers={"Idempotency-Key": idempotency_key}
        )
        assert res2.status_code == 422, f"Expected 422 Unprocessable Entity, got {res2.status_code}: {res2.text}"
        body = res2.json()
        assert body.get("code") == "IDEMPOTENCY_KEY_MISMATCH"

    # 3. Assert money moved only once for the first transfer (₹30, not ₹30 + ₹90)
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT balance FROM accounts WHERE id = %s;", (src_id,))
            final_balance = cur.fetchone()["balance"]
            assert final_balance == 47000, (
                f"Expected balance to reflect only the first deduction (47000 paise), got {final_balance}"
            )