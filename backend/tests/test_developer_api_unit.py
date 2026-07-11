from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.security.developer_api import (
    COURSES_READ_SCOPE,
    EVENTS_READ_SCOPE,
    generate_api_key,
    hash_api_key,
    require_developer_scope,
    validate_api_key,
)
from app.security.rate_limit import RedisRateLimiter


TEST_HASH_SECRET = "test-developer-key-hash-secret-32-characters"


class FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def eval(
        self,
        _script: str,
        _numkeys: int,
        key: str,
        window_seconds: int,
    ) -> list[int]:
        self.counts[key] = self.counts.get(key, 0) + 1
        return [self.counts[key], window_seconds]


def make_request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/courses",
            "headers": [],
            "client": ("203.0.113.10", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
    )


def make_record(raw_api_key: str, **overrides):
    values = {
        "id": uuid4(),
        "hashed_key": hash_api_key(
            raw_api_key,
            secret=TEST_HASH_SECRET,
        ),
        "key_prefix": raw_api_key[:20],
        "owner_name": "測試團隊",
        "scopes": [COURSES_READ_SCOPE],
        "expires_at": datetime.now(UTC) + timedelta(days=1),
        "is_active": True,
        "revoked_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_optional_key_validation_and_quota_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    app = FastAPI()
    app.state.developer_api_rate_limiter = RedisRateLimiter(FakeRedis())
    request = make_request(app)
    db = SimpleNamespace(scalar=AsyncMock())

    assert await validate_api_key(request, Response(), None, db) is None
    db.scalar.assert_not_awaited()

    raw_api_key, _ = generate_api_key(production=False)
    db.scalar.return_value = make_record(raw_api_key)
    response = Response()
    principal = await validate_api_key(
        request,
        response,
        raw_api_key,
        db,
    )

    assert principal is not None
    assert principal.permits(COURSES_READ_SCOPE)
    assert not principal.permits(EVENTS_READ_SCOPE)
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"


async def test_expired_key_and_missing_scope_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    app = FastAPI()
    app.state.developer_api_rate_limiter = RedisRateLimiter(FakeRedis())
    request = make_request(app)
    raw_api_key, _ = generate_api_key(production=False)
    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=make_record(
                raw_api_key,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    )

    with pytest.raises(HTTPException) as expired_error:
        await validate_api_key(
            request,
            Response(),
            raw_api_key,
            db,
        )
    assert expired_error.value.status_code == 401

    scope_dependency = require_developer_scope(EVENTS_READ_SCOPE)
    active_record = make_record(raw_api_key)
    db.scalar.return_value = active_record
    principal = await validate_api_key(
        request,
        Response(),
        raw_api_key,
        db,
    )
    with pytest.raises(HTTPException) as scope_error:
        await scope_dependency(principal)
    assert scope_error.value.status_code == 403


async def test_developer_quota_blocks_after_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    monkeypatch.setenv("API_KEY_RATE_LIMIT_PER_MINUTE", "2")
    app = FastAPI()
    app.state.developer_api_rate_limiter = RedisRateLimiter(FakeRedis())
    request = make_request(app)
    raw_api_key, _ = generate_api_key(production=False)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=make_record(raw_api_key))
    )

    await validate_api_key(request, Response(), raw_api_key, db)
    await validate_api_key(request, Response(), raw_api_key, db)
    with pytest.raises(HTTPException) as rate_error:
        await validate_api_key(
            request,
            Response(),
            raw_api_key,
            db,
        )

    assert rate_error.value.status_code == 429
    assert rate_error.value.headers is not None
    assert rate_error.value.headers["Retry-After"] == "60"
