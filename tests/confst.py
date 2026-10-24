# tests/conftest.py
import os
import pytest
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://wallet_user:wallet_pass@postgres:5432/wallet_db"
)

@pytest.fixture(scope="session")
def test_db_pool():
    pool = ConnectionPool(
        conninfo=DATABASE_URL,
        min_size=2,
        max_size=20,
        kwargs={"row_factory": dict_row}
    )
    yield pool
    pool.close()