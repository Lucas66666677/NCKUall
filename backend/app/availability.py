from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from os import getenv
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.responses import JSONResponse

from app.database import READ_METHODS, async_read_engine, async_write_engine


logger = logging.getLogger(__name__)
WRITE_BLOCKED_DETAIL = "系統目前處於唯讀模式，暫停新增、修改與刪除操作。"


@dataclass(frozen=True)
class DatabaseHealth:
    write_ok: bool
    read_ok: bool
    read_only_mode: bool
    checked_at: datetime


async def _ping(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("database_health_ping_failed", exc_info=True)
        return False


async def check_database_health(app: FastAPI) -> DatabaseHealth:
    write_ok = await _ping(async_write_engine)
    read_ok = await _ping(async_read_engine)
    read_only = not write_ok and read_ok
    app.state.read_only_mode = read_only
    app.state.database_write_ok = write_ok
    app.state.database_read_ok = read_ok
    app.state.database_health_checked_at = datetime.now(UTC)
    return DatabaseHealth(
        write_ok=write_ok,
        read_ok=read_ok,
        read_only_mode=read_only,
        checked_at=app.state.database_health_checked_at,
    )


def read_only_probe_interval_seconds() -> int:
    return max(1, int(getenv("READ_ONLY_PROBE_INTERVAL_SECONDS", "15")))


def read_only_guard_enabled() -> bool:
    return getenv("READ_ONLY_GUARD_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class ReadOnlyModeMiddleware:
    """Block unsafe HTTP methods when the primary database is unavailable."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http" or not read_only_guard_enabled():
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        if method in READ_METHODS:
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        if app is None:
            await self.app(scope, receive, send)
            return

        await self._refresh_if_needed(app)
        if getattr(app.state, "read_only_mode", False):
            response = JSONResponse(
                status_code=503,
                content={
                    "detail": WRITE_BLOCKED_DETAIL,
                    "error_code": "read_only_mode",
                    "read_only": True,
                },
                headers={
                    "Retry-After": str(read_only_probe_interval_seconds()),
                    "X-NCKUall-Read-Only": "true",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _refresh_if_needed(app: FastAPI) -> None:
        checked_at = getattr(app.state, "database_health_checked_at", None)
        interval = timedelta(seconds=read_only_probe_interval_seconds())
        if checked_at is not None and datetime.now(UTC) - checked_at < interval:
            return
        await check_database_health(app)
