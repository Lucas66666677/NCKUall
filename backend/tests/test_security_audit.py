from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, Request

from app.auth import AuthUser
from app.security.audit import (
    SecurityAlertMonitor,
    audit_admin_action,
    set_audit_changes,
    write_audit_log,
)


def make_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/admin/reviews/123/status",
            "headers": [
                (b"user-agent", b"pytest"),
                (b"x-forwarded-for", b"203.0.113.5"),
            ],
            "client": ("127.0.0.1", 49200),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": b"",
        }
    )
    request.state.request_id = "req-test"
    return request


def test_admin_audit_decorator_schedules_immutable_append_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    review_id = uuid4()
    background_tasks = BackgroundTasks()
    request = make_request()
    admin = AuthUser(
        user_id="admin-1",
        email="admin@ncku.edu.tw",
        claims={"app_metadata": {"is_admin": True}},
    )

    @audit_admin_action(
        action="HIDE_REVIEW",
        target_resource="life_reviews",
        target_id_getter=lambda values, _result: values["review_id"],
    )
    def handler(
        review_id: Any,
        background_tasks: BackgroundTasks,
        request: Request,
        admin_user: AuthUser,
    ) -> dict[str, bool]:
        set_audit_changes(
            request,
            before={"moderation_status": "PENDING"},
            after={"moderation_status": "HIDDEN"},
        )
        return {"ok": True}

    assert handler(review_id, background_tasks, request, admin) == {"ok": True}

    assert len(background_tasks.tasks) == 1
    task = background_tasks.tasks[0]
    assert task.func is write_audit_log
    assert task.kwargs["action"] == "HIDE_REVIEW"
    assert task.kwargs["target_resource"] == "life_reviews"
    assert task.kwargs["target_id"] == str(review_id)
    assert task.kwargs["operator_id"] == "admin-1"
    assert task.kwargs["ip_address"] == "203.0.113.5"
    assert task.kwargs["request_id"] == "req-test"
    assert task.kwargs["changes"]["before"]["moderation_status"] == "PENDING"
    assert task.kwargs["changes"]["after"]["moderation_status"] == "HIDDEN"


@pytest.mark.asyncio
async def test_rate_limit_alert_fires_only_after_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alerts: list[dict[str, Any]] = []

    async def fake_alert(**kwargs: Any) -> None:
        alerts.append(kwargs)

    monkeypatch.setenv("SECURITY_RATE_LIMIT_ALERT_THRESHOLD", "3")
    monkeypatch.setattr("app.security.audit.send_security_alert", fake_alert)
    monitor = SecurityAlertMonitor()

    for index in range(2):
        await monitor.record_rate_limit_violation(
            ip_address="203.0.113.9",
            method="POST",
            path="/api/chat",
            request_id=f"req-{index}",
        )
    assert alerts == []

    await monitor.record_rate_limit_violation(
        ip_address="203.0.113.9",
        method="POST",
        path="/api/chat",
        request_id="req-3",
    )

    assert len(alerts) == 1
    assert alerts[0]["event_type"] == "RATE_LIMIT_ABUSE"
    assert alerts[0]["details"]["count"] == 3
    assert alerts[0]["details"]["ip_address"] == "203.0.113.9"


@pytest.mark.asyncio
async def test_server_error_alert_is_deduped_per_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alerts: list[dict[str, Any]] = []

    async def fake_alert(**kwargs: Any) -> None:
        alerts.append(kwargs)

    monkeypatch.setenv("SECURITY_500_ALERT_DEDUPE_SECONDS", "60")
    monkeypatch.setattr("app.security.audit.send_security_alert", fake_alert)
    monitor = SecurityAlertMonitor()

    await monitor.record_server_error(
        ip_address="203.0.113.10",
        method="GET",
        path="/api/courses/not-found",
        status_code=500,
        request_id="req-1",
    )
    await monitor.record_server_error(
        ip_address="203.0.113.10",
        method="GET",
        path="/api/courses/not-found",
        status_code=500,
        request_id="req-2",
    )

    assert len(alerts) == 1
    assert alerts[0]["event_type"] == "SERVER_ERROR"
    assert alerts[0]["details"]["request_id"] == "req-1"
