# tests/test_stampede.py
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

def setup_test_accounts():
    """
    Sets up two isolated users and wallets:
    - Source wallet funded with ₹500 (50,000 paise).
    - Destination wallet with ₹0 balance.
    Returns: (source_account_id, destination_account_id)
    """
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 1. Create unique test users
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
                    f"Sender {unique_suffix}",
                    f"sender_{unique_suffix}@test.com",
                    f"+91{unique_suffix}01",
                    f"Receiver {unique_suffix}",
                    f"receiver_{unique_suffix}@test.com",
                    f"+91{unique_suffix}02",
                ),
            )
            users = cur.fetchall()
            user_a_id = users[0]["id"]
            user_b_id = users[1]["id"]

            # 2. Create wallets: Wallet A with 50,000 paise (₹500), Wallet B with 0 paise
            cur.execute(
                """
                INSERT INTO accounts (user_id, account_type, name, currency, balance)
                VALUES 
                    (%s, 'user', 'Main', 'INR', 50000),
                    (%s, 'user', 'Main', 'INR', 0)
                RETURNING id;
                """,
                (user_a_id, user_b_id),
            )
            accounts = cur.fetchall()
            src_id = accounts[0]["id"]
            dst_id = accounts[1]["id"]
        conn.commit()
    return src_id, dst_id

async def send_single_transfer(
    client: httpx.AsyncClient, 
    source_id: int, 
    destination_id: int, 
    amount_paise: int
) -> tuple[int, str]:
    """
    Sends one transfer request with a unique Idempotency-Key.
    Returns the HTTP response status code and body for diagnostics.
    """
    headers = {
        "Content-Type": "application/json",
        "Idempotency-Key": str(uuid.uuid4()),
    }
    payload = {
        "source_account_id": source_id,
        "destination_account_id": destination_id,
        "amount": amount_paise,
        "note": "Stampede test concurrent fire",
    }
    resp = await client.post(f"{API_BASE_URL}/transfers", json=payload, headers=headers)
    return resp.status_code, resp.text

async def execute_stampede_run():
    """
    Executes one single stampede run:
    100 parallel ₹10 (1,000 paise) transfers against a ₹500 account.
    """
    source_id, destination_id = setup_test_accounts()
    transfer_amount = 1000  # ₹10 in paise
    num_requests = 100

    # Fire 100 concurrent requests simultaneously using an async HTTP client pool
    limits = httpx.Limits(max_keepalive_connections=100, max_connections=120)
    async with httpx.AsyncClient(limits=limits, timeout=30.0) as client:
        tasks = [
            send_single_transfer(client, source_id, destination_id, transfer_amount)
            for _ in range(num_requests)
        ]
        responses = await asyncio.gather(*tasks)

    success_count = sum(1 for code, _ in responses if code == 200)
    insufficient_funds_count = sum(1 for code, _ in responses if code == 400)
    unexpected_responses = [response for response in responses if response[0] not in (200, 400)]

    # Database state verification
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 1. Verify Wallet Balances
            cur.execute("SELECT id, balance FROM accounts WHERE id IN (%s, %s);", (source_id, destination_id))
            acc_rows = {row["id"]: row["balance"] for row in cur.fetchall()}
            src_balance = acc_rows[source_id]
            dst_balance = acc_rows[destination_id]

            # 2. Verify Double-Entry Accounting on all ledger entries created for these accounts
            cur.execute(
                """
                SELECT 
                    direction,
                    SUM(amount) AS total_amount
                FROM ledger_entries
                WHERE account_id IN (%s, %s)
                GROUP BY direction;
                """,
                (source_id, destination_id),
            )
            direction_rows = cur.fetchall()
            ledger_totals = {row["direction"]: row["total_amount"] for row in direction_rows}
            total_debits = ledger_totals.get("debit", 0)
            total_credits = ledger_totals.get("credit", 0)

    # Acceptance Criteria Assertions
    assert success_count == 50, (
        f"Expected exactly 50 successful transfers, got {success_count}. "
        f"Failed requests count: {insufficient_funds_count}"
    )
    assert insufficient_funds_count == 50, (
        f"Expected exactly 50 transfers rejected with 400, got {insufficient_funds_count}. "
        f"Unexpected responses: {unexpected_responses[:5]}"
    )
    assert src_balance == 0, f"Source wallet balance should be 0, but is {src_balance}"
    assert dst_balance == 50000, f"Destination wallet balance should be 50,000 paise (₹500), but is {dst_balance}"
    assert total_debits == total_credits, (
        f"Ledger imbalance: Total Debits ({total_debits}) != Total Credits ({total_credits})"
    )
    assert total_debits == 50000, f"Expected exactly 50,000 paise moved, got {total_debits}"

@pytest.mark.asyncio
async def test_stampede_twenty_iterations():
    """
    Milestone 2 Acceptance Test:
    Run the 100-concurrent-requests stampede 20 times in a row.
    """
    iterations = 20
    for iteration in range(1, iterations + 1):
        try:
            await execute_stampede_run()
            print(f"[Stampede Test] Iteration {iteration}/{iterations} PASSED.")
            # Brief yield to allow the socket pool to settle
            await asyncio.sleep(0.1)
        except AssertionError as exc:
            pytest.fail(f"Concurrency race detected on iteration {iteration}/{iterations}: {exc}")