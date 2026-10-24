from fastapi import APIRouter, Depends
from pydantic import BaseModel
import psycopg

from app.db.session import get_db_connection
from app.services.llm import execute_safe_statement_qa

router = APIRouter(tags=["Statement Q&A"])

class StatementAskRequest(BaseModel):
    question: str

@router.post("/users/{user_id}/statement/ask")
def ask_statement_question(
    user_id: int,
    payload: StatementAskRequest,
    conn: psycopg.Connection = Depends(get_db_connection)
):
    result = execute_safe_statement_qa(conn, user_id, payload.question)
    return result