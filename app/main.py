from fastapi import FastAPI, Request
from uuid import uuid4

from app.core.errors import WalletAPIException, wallet_exception_handler
from app.api.v1.wallets import router as wallet_router
from app.api.v1.statement_qa import router as qa_router
from app.api.v1.admin import router as admin_router

app = FastAPI(
    title="Digital Wallet Core Engine",
    version="1.0.0",
    docs_url="/docs"
)

@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response

app.add_exception_handler(WalletAPIException, wallet_exception_handler)

app.include_router(wallet_router)
app.include_router(qa_router)
app.include_router(admin_router)

@app.get("/health")
def health_check():
    return {"status": "healthy"}