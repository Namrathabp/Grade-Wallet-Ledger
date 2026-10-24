from uuid import uuid4
from fastapi import Request
from fastapi.responses import JSONResponse

class WalletAPIException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code

async def wallet_exception_handler(request: Request, exc: WalletAPIException):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id
        }
    )