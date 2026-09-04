"""One error shape for the whole API.

Every failure returns ``{"error": {"code", "message", "details"}}``. The code is
for the client to branch on; the message is written to be shown to a person as
is, because an error the UI has to rewrite is an error the UI will get wrong.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("marketdiff.errors")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class NotFound(ApiError):
    def __init__(self, message: str = "Not found", code: str = "not_found") -> None:
        super().__init__(404, code, message)


class Conflict(ApiError):
    def __init__(self, message: str, code: str = "conflict", details: dict | None = None) -> None:
        super().__init__(409, code, message, details)


class BadRequest(ApiError):
    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(400, code, message)


def _payload(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=_payload(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in {"body", "query"})
        message = first.get("msg", "Request was not valid")
        message = message.replace("Value error, ", "")
        return JSONResponse(
            status_code=422,
            content=_payload(
                "invalid_request",
                f"{field}: {message}" if field else message,
                {"fields": [str(e.get("loc")) for e in exc.errors()[:5]]},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload("http_error", str(exc.detail)),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        log.exception("Database error")
        return JSONResponse(
            status_code=503,
            content=_payload(
                "database_unavailable",
                "Could not reach the database. Your watchlists are safe — try again in a moment.",
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error")
        return JSONResponse(
            status_code=500,
            content=_payload("internal_error", "Something went wrong on our side."),
        )
