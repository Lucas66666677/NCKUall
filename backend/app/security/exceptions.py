from __future__ import annotations

import logging
from uuid import uuid4

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


logger = logging.getLogger(__name__)
SAFE_SERVER_ERROR_DETAIL = "系統暫時無法處理請求，請稍後再試。"


def safe_error_code(status_code: int, request_id: str) -> str:
    suffix = request_id[:8].upper()
    return f"NCKUALL-{status_code}-{suffix}"


def get_request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return str(request_id)
    return uuid4().hex


def capture_server_error(
    request: Request,
    exc: Exception,
    *,
    request_id: str,
) -> None:
    """Send sanitized request context and the exception to Sentry."""

    with sentry_sdk.isolation_scope() as scope:
        scope.set_tag("request_id", request_id)
        scope.set_tag("http.status_code", 500)
        scope.set_context(
            "request",
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        sentry_sdk.capture_exception(exc)


def install_exception_handlers(app: FastAPI) -> None:
    """Install sanitized JSON handlers while preserving HTTP headers."""

    @app.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        request_id = get_request_id(request)
        if exc.status_code >= 500:
            capture_server_error(
                request,
                exc,
                request_id=request_id,
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "detail": SAFE_SERVER_ERROR_DETAIL,
                    "error_code": "server_error",
                    "safe_error_id": safe_error_code(
                        exc.status_code,
                        request_id,
                    ),
                    "request_id": request_id,
                },
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "error_code": "http_error",
                "request_id": request_id,
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "請求格式不正確",
                "error_code": "validation_error",
                "request_id": get_request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        request_id = get_request_id(request)
        capture_server_error(
            request,
            exc,
            request_id=request_id,
        )
        logger.exception(
            "Unhandled API exception request_id=%s path=%s",
            request_id,
            request.url.path,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": SAFE_SERVER_ERROR_DETAIL,
                "error_code": "server_error",
                "safe_error_id": safe_error_code(500, request_id),
                "request_id": request_id,
            },
        )
