from __future__ import annotations

import inspect
import logging
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.observability.logging import (
    bind_request_context,
    reset_request_context,
)
from app.security.audit import SecurityAlertMonitor, get_scope_ip


logger = logging.getLogger("nckuall.http")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ObservabilityMiddleware:
    """Pure ASGI middleware for request IDs, latency, and access logs."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        supplied_request_id = headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
            else uuid4().hex
        )
        method = str(scope.get("method", "UNKNOWN"))
        path = str(scope.get("path", "/"))
        app = scope.get("app")
        tokens = bind_request_context(
            request_id=request_id,
            method=method,
            path=path,
        )
        scope.setdefault("state", {})["request_id"] = request_id

        started_at = perf_counter()
        status_code = 500

        async def send_with_context(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (b"x-request-id", request_id.encode("ascii"))
                )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            duration_ms = round((perf_counter() - started_at) * 1000, 3)
            endpoint = scope.get("endpoint")
            async_function = self._endpoint_name(endpoint)
            level = (
                logging.ERROR
                if status_code >= 500
                else logging.WARNING
                if status_code >= 400
                else logging.INFO
            )
            logger.log(
                level,
                "http_request_completed",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "async_function": async_function,
                },
            )
            monitor: SecurityAlertMonitor | None = getattr(
                getattr(app, "state", None),
                "security_alert_monitor",
                None,
            )
            if monitor is not None:
                ip_address = get_scope_ip(scope, headers)
                try:
                    if status_code == 429:
                        await monitor.record_rate_limit_violation(
                            ip_address=ip_address,
                            method=method,
                            path=path,
                            request_id=request_id,
                        )
                    elif status_code >= 500:
                        await monitor.record_server_error(
                            ip_address=ip_address,
                            method=method,
                            path=path,
                            status_code=status_code,
                            request_id=request_id,
                        )

                    if (
                        path.startswith("/api/admin")
                        and "export" in path.lower()
                        and status_code < 500
                    ):
                        await monitor.record_data_export_attempt(
                            ip_address=ip_address,
                            method=method,
                            path=path,
                            status_code=status_code,
                            request_id=request_id,
                        )
                except Exception:
                    logger.warning(
                        "security_alert_monitor_failed",
                        extra={
                            "request_id": request_id,
                            "method": method,
                            "path": path,
                            "status_code": status_code,
                        },
                        exc_info=True,
                    )
            reset_request_context(tokens)

    @staticmethod
    def _endpoint_name(endpoint: Any) -> str:
        if endpoint is None:
            return "unresolved_endpoint"
        name = getattr(endpoint, "__qualname__", endpoint.__class__.__name__)
        return name if inspect.iscoroutinefunction(endpoint) else f"sync:{name}"
