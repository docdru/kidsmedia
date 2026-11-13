"""API errors carry a machine-readable reason code plus parameters (SPEC §3.2):
the server never sends finished sentences; clients localize."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, reason: str, **params: Any) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.params = params


def install_error_handler(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            {"reason": exc.reason, "params": exc.params}, status_code=exc.status_code
        )
