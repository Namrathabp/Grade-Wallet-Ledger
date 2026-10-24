import os
import time
from apscheduler.schedulers.blocking import BlockingScheduler
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row

from app.services.reconciliation import run_reconciliation

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://wallet_user:wallet_pass@localhost:5432/wallet_db")

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=2,
    max_size=5,
    kwargs={"row_factory": dict_row}
)

def scheduled_nightly_job():
    print("[Worker] Running scheduled reconciliation audit...")
    result = run_reconciliation(pool)
    print(f"[Worker] Audit complete: {result}")

if __name__ == "__main__":
    print("[Worker] Background scheduler started.")
    scheduler = BlockingScheduler()
    # Runs audit every night at 00:00 (or every 10 minutes for testing if modified)
    scheduler.add_job(scheduled_nightly_job, 'cron', hour=0, minute=0)
    
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pool.close()