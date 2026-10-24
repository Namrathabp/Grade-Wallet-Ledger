# app/db/session.py
import os
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql://wallet_user:wallet_pass@localhost:5432/wallet_db"
)

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=10,
    max_size=200,
    max_idle=30.0,
    timeout=30,
    kwargs={"row_factory": dict_row}
)

def get_db_connection():
    with pool.connection() as conn:
        yield conn