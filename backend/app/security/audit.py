from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from os import getenv
from typing import Any, Protocol

import httpx
from fastapi import BackgroundTasks, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth import AuthUser
from app.database import AsyncSessionLocal
from app.models import AuditLog
from app.security.rate_limit import is_enabled


logger = logging.getLogger(__name__)

SECURITY_ALERT_COLORS = {
    "critical": 0xDC2626,
    "high": 0xEA580C,
    "medium": 0xD97706,
    "low": 0x2563EB,
}


class SecurityRedis(Protocol):
    async def incr(self, name: str, amount: int = 1) -> int: ...

    async def expire(self, name: str, time: int) -> Any: ...

    async def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> Any: ...


@dataclass
class MemoryCounter:
    started_at: float
    count: int = 0
    alerted_counts: set[int] = field(default_factory=set)


def get_request_ip(request: Request) -> str | None:
    if is_enabled("TRUST_PROXY_HEADERS", default=False):
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else None


def get_scope_ip(scope: dict[str, Any], headers: dict[str, str]) -> str | None:
    if is_enabled("TRUST_PROXY_HEADERS", default=False):
        forwarded_for = headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        real_ip = headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return None


def get_request_id(request: Request | None) -> str | None:
    if request is None:
        return None
    request_id = getattr(request.state, "request_id", None)
    return str(request_id) if request_id else None


def operator_identifier(user: AuthUser | None) -> str | None:
    if user is None:
        return None
    return user.user_id or user.email


def serialize_changes(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    encoded = jsonable_encoder(value)
    return encoded if isinstance(encoded, dict) else {"value": encoded}


async def write_audit_log(
    *,
    action: str,
    target_resource: str,
    target_id: str | None = None,
    changes: dict[str, Any] | None = None,
    operator_id: str | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
    user_agent: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> None:
    """
    Append one immutable audit row.

    The caller should run this as a background task after a privileged action
    succeeds. Failures are logged and optionally alerted, but never mutate the
    original business operation result.
    """

    try:
        async with session_factory() as db:
            db.add(
                AuditLog(
                    operator_id=operator_id,
                    action=action,
                    target_resource=target_resource,
                    target_id=target_id,
                    changes=serialize_changes(changes),
                    ip_address=ip_address,
                    request_id=request_id,
                    user_agent=user_agent,
                )
            )
            await db.commit()
    except SQLAlchemyError:
        logger.exception(
            "audit_log_write_failed",
            extra={
                "action": action,
                "target_resource": target_resource,
                "target_id": target_id,
                "request_id": request_id,
            },
        )
        await send_security_alert(
            title="Audit log write failed",
            event_type="AUDIT_WRITE_FAILED",
            severity="critical",
            details={
                "action": action,
                "target_resource": target_resource,
                "target_id": target_id,
                "request_id": request_id,
            },
        )


def set_audit_changes(
    request: Request,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    request.state.audit_changes = {
        "before": serialize_changes(before),
        "after": serialize_changes(after),
        "metadata": serialize_changes(metadata),
    }


def _resolve_value(
    value: str | Callable[[dict[str, Any], Any], str],
    values: dict[str, Any],
    result: Any,
) -> str:
    return value(values, result) if callable(value) else value


def _schedule_audit_log(
    *,
    values: dict[str, Any],
    result: Any,
    action: str | Callable[[dict[str, Any], Any], str],
    target_resource: str | Callable[[dict[str, Any], Any], str],
    target_id_getter: Callable[[dict[str, Any], Any], Any] | None,
    changes_getter: Callable[[dict[str, Any], Any], dict[str, Any]] | None,
) -> None:
    request = values.get("request")
    if request is not None and not isinstance(request, Request):
        request = None

    admin_user = values.get("admin_user") or values.get("current_user")
    if admin_user is not None and not isinstance(admin_user, AuthUser):
        admin_user = None

    background_tasks = values.get("background_tasks")
    if background_tasks is not None and not isinstance(
        background_tasks,
        BackgroundTasks,
    ):
        background_tasks = None

    target_id = (
        target_id_getter(values, result)
        if target_id_getter is not None
        else values.get("target_id") or values.get("review_id")
    )
    changes = (
        changes_getter(values, result)
        if changes_getter is not None
        else getattr(request.state, "audit_changes", {})
        if request is not None
        else {}
    )
    user_agent = request.headers.get("user-agent") if request is not None else None
    audit_kwargs = {
        "action": _resolve_value(action, values, result),
        "target_resource": _resolve_value(target_resource, values, result),
        "target_id": str(target_id) if target_id is not None else None,
        "changes": changes,
        "operator_id": operator_identifier(admin_user),
        "ip_address": get_request_ip(request) if request is not None else None,
        "request_id": get_request_id(request),
        "user_agent": user_agent,
    }

    if background_tasks is not None:
        background_tasks.add_task(write_audit_log, **audit_kwargs)
        return

    try:
        asyncio.create_task(write_audit_log(**audit_kwargs))
    except RuntimeError:
        logger.warning("audit_log_not_scheduled", extra=audit_kwargs)


def audit_admin_action(
    *,
    action: str | Callable[[dict[str, Any], Any], str],
    target_resource: str | Callable[[dict[str, Any], Any], str],
    target_id_getter: Callable[[dict[str, Any], Any], Any] | None = None,
    changes_getter: Callable[[dict[str, Any], Any], dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorate successful admin mutation routes with append-only audit logging.

    The wrapped endpoint keeps its original FastAPI signature. Audit logging
    runs only after the wrapped function returns without raising.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(func)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                result = await func(*args, **kwargs)
                _schedule_audit_log(
                    values=dict(bound.arguments),
                    result=result,
                    action=action,
                    target_resource=target_resource,
                    target_id_getter=target_id_getter,
                    changes_getter=changes_getter,
                )
                return result

            async_wrapper.__signature__ = signature  # type: ignore[attr-defined]
            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            result = func(*args, **kwargs)
            _schedule_audit_log(
                values=dict(bound.arguments),
                result=result,
                action=action,
                target_resource=target_resource,
                target_id_getter=target_id_getter,
                changes_getter=changes_getter,
            )
            return result

        sync_wrapper.__signature__ = signature  # type: ignore[attr-defined]
        return sync_wrapper

    return decorator


def _alert_webhook_url() -> str | None:
    return getenv("SECURITY_ALERT_WEBHOOK_URL") or getenv("DISCORD_WEBHOOK_URL")


def _webhook_kind() -> str:
    configured = getenv("SECURITY_ALERT_WEBHOOK_KIND", "").strip().lower()
    if configured in {"slack", "discord"}:
        return configured
    url = _alert_webhook_url() or ""
    return "slack" if "hooks.slack.com" in url else "discord"


def _detail_fields(details: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for key, value in details.items():
        if value is None:
            continue
        text = str(value)
        fields.append(
            {
                "name": key,
                "value": text[:1000],
                "inline": len(text) <= 80,
            }
        )
    return fields[:12]


def _discord_payload(
    *,
    title: str,
    event_type: str,
    severity: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "username": "NCKUall Security",
        "embeds": [
            {
                "title": title,
                "description": f"`{event_type}` security event detected.",
                "color": SECURITY_ALERT_COLORS.get(severity, 0x6B7280),
                "fields": _detail_fields({"severity": severity, **details}),
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ],
    }


def _slack_payload(
    *,
    title: str,
    event_type: str,
    severity: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    fields = "\n".join(
        f"*{key}*: `{str(value)[:500]}`"
        for key, value in {"severity": severity, **details}.items()
        if value is not None
    )
    return {
        "text": f"{title} ({event_type})",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"NCKUall Security: {title}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": fields or f"*event_type*: `{event_type}`",
                },
            },
        ],
    }


async def send_security_alert(
    *,
    title: str,
    event_type: str,
    severity: str = "high",
    details: dict[str, Any] | None = None,
) -> None:
    if not is_enabled("SECURITY_ALERTS_ENABLED", default=True):
        return

    webhook_url = _alert_webhook_url()
    if not webhook_url:
        logger.info(
            "security_alert_webhook_not_configured",
            extra={"event_type": event_type, "severity": severity},
        )
        return

    payload_factory = (
        _slack_payload if _webhook_kind() == "slack" else _discord_payload
    )
    payload = payload_factory(
        title=title,
        event_type=event_type,
        severity=severity,
        details=serialize_changes(details),
    )
    timeout = float(getenv("SECURITY_ALERT_TIMEOUT_SECONDS", "3"))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
    except Exception:
        logger.warning(
            "security_alert_delivery_failed",
            extra={"event_type": event_type, "severity": severity},
            exc_info=True,
        )


class SecurityAlertMonitor:
    """Realtime security alert coordinator with Redis and memory fallback."""

    def __init__(self, redis: SecurityRedis | None = None) -> None:
        self.redis = redis
        self.rate_limit_threshold = int(
            getenv("SECURITY_RATE_LIMIT_ALERT_THRESHOLD", "50")
        )
        self.rate_limit_window_seconds = int(
            getenv("SECURITY_RATE_LIMIT_ALERT_WINDOW_SECONDS", "600")
        )
        self.server_error_dedupe_seconds = int(
            getenv("SECURITY_500_ALERT_DEDUPE_SECONDS", "60")
        )
        self._memory_counters: dict[str, MemoryCounter] = {}
        self._memory_dedupe: dict[str, float] = {}

    async def record_rate_limit_violation(
        self,
        *,
        ip_address: str | None,
        method: str,
        path: str,
        request_id: str,
    ) -> None:
        if not ip_address:
            return

        digest = hashlib.sha256(ip_address.encode("utf-8")).hexdigest()
        key = f"security:429:{digest}"
        count = await self._increment_windowed_count(key)
        if count < self.rate_limit_threshold:
            return
        if count != self.rate_limit_threshold and count % self.rate_limit_threshold:
            return

        await send_security_alert(
            title="Repeated HTTP 429 from one IP",
            event_type="RATE_LIMIT_ABUSE",
            severity="high",
            details={
                "ip_address": ip_address,
                "count": count,
                "window_seconds": self.rate_limit_window_seconds,
                "method": method,
                "path": path,
                "request_id": request_id,
            },
        )

    async def record_server_error(
        self,
        *,
        ip_address: str | None,
        method: str,
        path: str,
        status_code: int,
        request_id: str,
    ) -> None:
        dedupe_key = f"security:500:{method}:{path}"
        if not await self._should_emit_deduped(
            dedupe_key,
            self.server_error_dedupe_seconds,
        ):
            return

        await send_security_alert(
            title="HTTP 500 detected",
            event_type="SERVER_ERROR",
            severity="critical",
            details={
                "ip_address": ip_address,
                "method": method,
                "path": path,
                "status_code": status_code,
                "request_id": request_id,
            },
        )

    async def record_data_export_attempt(
        self,
        *,
        ip_address: str | None,
        method: str,
        path: str,
        status_code: int,
        request_id: str,
    ) -> None:
        await send_security_alert(
            title="Admin data export route was accessed",
            event_type="EXPORT_DATA_ATTEMPT",
            severity="critical",
            details={
                "ip_address": ip_address,
                "method": method,
                "path": path,
                "status_code": status_code,
                "request_id": request_id,
            },
        )

    async def _increment_windowed_count(self, key: str) -> int:
        if self.redis is not None:
            try:
                count = int(await self.redis.incr(key))
                if count == 1:
                    await self.redis.expire(
                        key,
                        self.rate_limit_window_seconds,
                    )
                return count
            except Exception:
                logger.warning("security_counter_redis_failed", exc_info=True)

        now = time.monotonic()
        counter = self._memory_counters.get(key)
        if (
            counter is None
            or now - counter.started_at > self.rate_limit_window_seconds
        ):
            counter = MemoryCounter(started_at=now)
            self._memory_counters[key] = counter
        counter.count += 1
        return counter.count

    async def _should_emit_deduped(self, key: str, ttl_seconds: int) -> bool:
        if self.redis is not None:
            try:
                inserted = await self.redis.set(
                    key,
                    "1",
                    ex=ttl_seconds,
                    nx=True,
                )
                return bool(inserted)
            except Exception:
                logger.warning("security_dedupe_redis_failed", exc_info=True)

        now = time.monotonic()
        expires_at = self._memory_dedupe.get(key, 0)
        if expires_at > now:
            return False
        self._memory_dedupe[key] = now + ttl_seconds
        return True


async def alert_data_export_attempt(
    request: Request,
    *,
    target_resource: str = "all",
) -> None:
    await send_security_alert(
        title="Explicit data export attempt",
        event_type="EXPORT_DATA",
        severity="critical",
        details={
            "target_resource": target_resource,
            "ip_address": get_request_ip(request),
            "request_id": get_request_id(request),
        },
    )
