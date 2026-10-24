# app/services/llm.py
import re
import json
from pydantic import BaseModel, ValidationError

class StatementQueryIntent(BaseModel):
    query_type: int             # 1: Sent to person, 2: Received, 3: Largest, 4: Count
    counterparty_name: str | None = None
    from_date: str              # YYYY-MM-DD
    to_date: str                # YYYY-MM-DD

class MockLLMService:
    @staticmethod
    def parse_user_statement_intent(prompt: str) -> dict:
        # Resilient against prompt injection: Treats input strictly as conversational text
        if "Rahul" in prompt:
            return {"query_type": 1, "counterparty_name": "Rahul", "from_date": "2026-08-01", "to_date": "2026-08-31"}
        if "largest" in prompt.lower():
            return {"query_type": 3, "counterparty_name": None, "from_date": "2026-01-01", "to_date": "2026-12-31"}
        return {"query_type": 4, "counterparty_name": None, "from_date": "2026-01-01", "to_date": "2026-12-31"}

def execute_safe_statement_qa(conn, user_id: int, user_prompt: str):
    raw_intent = MockLLMService.parse_user_statement_intent(user_prompt)
    try:
        intent = StatementQueryIntent(**raw_intent)
    except ValidationError:
        return {"error": "Unsupported query type. Only 4 standard statement questions are supported."}

    with conn.cursor() as cur:
        if intent.query_type == 1:
            # Total sent to a counterparty
            query = """
                SELECT COALESCE(SUM(t.amount), 0) AS total, array_agg(t.id) AS transfer_ids
                FROM transfers t
                JOIN accounts src ON t.source_account_id = src.id
                JOIN accounts dst ON t.destination_account_id = dst.id
                JOIN users dest_user ON dst.user_id = dest_user.id
                WHERE src.user_id = %s
                  AND dest_user.name ILIKE %s
                  AND t.status = 'completed'
                  AND t.created_at >= %s::date
                  AND t.created_at < (%s::date + INTERVAL '1 day');
            """
            cur.execute(query, (user_id, f"%{intent.counterparty_name}%", intent.from_date, intent.to_date))
            res = cur.fetchone()
            return {"amount_paise": res["total"], "transfer_ids": res["transfer_ids"] or []}

        if intent.query_type == 2:
            query = """
                SELECT COALESCE(SUM(t.amount), 0) AS total, array_agg(t.id) AS transfer_ids
                FROM transfers t
                JOIN accounts src ON t.source_account_id = src.id
                JOIN accounts dst ON t.destination_account_id = dst.id
                JOIN users source_user ON src.user_id = source_user.id
                WHERE dst.user_id = %s
                  AND source_user.name ILIKE %s
                  AND t.status = 'completed'
                  AND t.created_at >= %s::date
                  AND t.created_at < (%s::date + INTERVAL '1 day');
            """
            cur.execute(query, (user_id, f"%{intent.counterparty_name}%", intent.from_date, intent.to_date))
            res = cur.fetchone()
            return {"amount_paise": res["total"], "transfer_ids": res["transfer_ids"] or []}

        if intent.query_type == 3:
            query = """
                SELECT t.amount, t.id
                FROM transfers t
                JOIN accounts src ON t.source_account_id = src.id
                WHERE src.user_id = %s
                  AND t.status = 'completed'
                  AND t.created_at >= %s::date
                  AND t.created_at < (%s::date + INTERVAL '1 day')
                ORDER BY t.amount DESC, t.id
                LIMIT 1;
            """
            cur.execute(query, (user_id, intent.from_date, intent.to_date))
            res = cur.fetchone()
            return {
                "amount_paise": res["amount"] if res else 0,
                "transfer_ids": [res["id"]] if res else [],
            }

        if intent.query_type == 4:
            query = """
                SELECT COUNT(*) AS count, array_agg(t.id) AS transfer_ids
                FROM transfers t
                JOIN accounts src ON t.source_account_id = src.id
                WHERE src.user_id = %s
                  AND t.status = 'completed'
                  AND t.created_at >= %s::date
                  AND t.created_at < (%s::date + INTERVAL '1 day');
            """
            cur.execute(query, (user_id, intent.from_date, intent.to_date))
            res = cur.fetchone()
            return {"count": res["count"], "transfer_ids": res["transfer_ids"] or []}

    return {"error": "Unsupported query type. Only 4 standard statement questions are supported."}