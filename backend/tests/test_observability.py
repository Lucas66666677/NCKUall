from __future__ import annotations

import json
import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.observability.logging import (
    JsonFormatter,
    bind_request_context,
    reset_request_context,
)
from app.observability.middleware import ObservabilityMiddleware
from app.observability.sentry import FILTERED, before_send


def test_json_formatter_contains_context_timing_and_redacts_secrets() -> None:
    formatter = JsonFormatter()
    tokens = bind_request_context(
        request_id="request-123",
        method="GET",
        path="/api/courses",
    )
    try:
        record = logging.LogRecord(
            name="test.observability",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="completed password=super-secret",
            args=(),
            exc_info=None,
        )
        record.status_code = 200
        record.duration_ms = 12.345
        record.async_function = "get_courses"
        payload = json.loads(formatter.format(record))
    finally:
        reset_request_context(tokens)

    assert payload["request_id"] == "request-123"
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/courses"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.345
    assert payload["async_function"] == "get_courses"
    assert payload["message"] == "completed password=[Filtered]"
    assert payload["timestamp"].endswith("+00:00")


async def test_observability_middleware_logs_every_request(
    caplog,
) -> None:
    app = FastAPI()
    app.add_middleware(ObservabilityMiddleware)

    @app.get("/observed")
    async def observed_endpoint() -> dict[str, bool]:
        return {"ok": True}

    caplog.set_level(logging.INFO, logger="nckuall.http")
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/observed",
            headers={"X-Request-ID": "trace-abc"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "trace-abc"
    records = [
        record
        for record in caplog.records
        if record.name == "nckuall.http"
        and record.getMessage() == "http_request_completed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.method == "GET"
    assert record.path == "/observed"
    assert record.status_code == 200
    assert record.duration_ms >= 0
    assert record.async_function.endswith("observed_endpoint")


def test_sentry_before_send_recursively_filters_sensitive_data() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "session=secret",
                "Accept": "application/json",
            },
            "data": {
                "password": "do-not-send",
                "nested": {
                    "database_url": "postgresql://user:password@db/prod",
                },
            },
        },
        "message": "connection failed token=abc123",
    }

    scrubbed = before_send(event, {})

    assert scrubbed["request"]["headers"]["Authorization"] == FILTERED
    assert scrubbed["request"]["headers"]["Cookie"] == FILTERED
    assert scrubbed["request"]["headers"]["Accept"] == "application/json"
    assert scrubbed["request"]["data"]["password"] == FILTERED
    assert scrubbed["request"]["data"]["nested"]["database_url"] == FILTERED
    assert scrubbed["message"] == "connection failed token=[Filtered]"
    assert event["request"]["data"]["password"] == "do-not-send"
