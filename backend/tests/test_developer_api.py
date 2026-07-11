from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models import DeveloperKey
from app.security.developer_api import (
    COURSES_READ_SCOPE,
    EVENTS_READ_SCOPE,
    generate_api_key,
    hash_api_key,
)
from app.security.rate_limit import RedisRateLimiter


pytestmark = pytest.mark.integration
TEST_HASH_SECRET = "test-developer-key-hash-secret-32-characters"


class AsyncRedis(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any: ...


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


async def issue_key(
    db_session: AsyncSession,
    *,
    scopes: list[str],
    expires_at: datetime | None = None,
) -> str:
    raw_api_key, key_prefix = generate_api_key(production=False)
    db_session.add(
        DeveloperKey(
            hashed_key=hash_api_key(
                raw_api_key,
                secret=TEST_HASH_SECRET,
            ),
            key_prefix=key_prefix,
            owner_name="測試開發團隊",
            owner_email="developer@example.org",
            scopes=scopes,
            expires_at=expires_at
            or datetime.now(UTC) + timedelta(days=30),
            is_active=True,
        )
    )
    await db_session.commit()
    return raw_api_key


async def test_public_reads_stay_available_without_api_key(
    client: AsyncClient,
) -> None:
    courses_response = await client.get("/api/courses")
    events_response = await client.get("/api/events")

    assert courses_response.status_code == 200
    assert events_response.status_code == 200


async def test_valid_key_receives_quota_headers(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    monkeypatch.setenv("API_KEY_RATE_LIMIT_PER_MINUTE", "60")
    monkeypatch.setattr(
        app.state,
        "developer_api_rate_limiter",
        RedisRateLimiter(FakeRedis()),
        raising=False,
    )
    raw_api_key = await issue_key(
        db_session,
        scopes=[COURSES_READ_SCOPE],
    )

    response = await client.get(
        "/api/courses",
        headers={"X-API-KEY": raw_api_key},
    )

    assert response.status_code == 200, response.text
    assert response.headers["X-RateLimit-Limit"] == "60"
    assert response.headers["X-RateLimit-Remaining"] == "59"


async def test_invalid_expired_and_under_scoped_keys_are_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    monkeypatch.setattr(
        app.state,
        "developer_api_rate_limiter",
        RedisRateLimiter(FakeRedis()),
        raising=False,
    )
    courses_only_key = await issue_key(
        db_session,
        scopes=[COURSES_READ_SCOPE],
    )
    expired_key = await issue_key(
        db_session,
        scopes=[EVENTS_READ_SCOPE],
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    unknown_key, _ = generate_api_key(production=False)

    invalid_response = await client.get(
        "/api/courses",
        headers={"X-API-KEY": unknown_key},
    )
    expired_response = await client.get(
        "/api/events",
        headers={"X-API-KEY": expired_key},
    )
    scope_response = await client.get(
        "/api/events",
        headers={"X-API-KEY": courses_only_key},
    )

    assert invalid_response.status_code == 401
    assert expired_response.status_code == 401
    assert scope_response.status_code == 403
    assert EVENTS_READ_SCOPE in scope_response.json()["detail"]


async def test_developer_key_rate_limit_returns_retry_after(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY_HASH_SECRET", TEST_HASH_SECRET)
    monkeypatch.setenv("API_KEY_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setattr(
        app.state,
        "developer_api_rate_limiter",
        RedisRateLimiter(FakeRedis()),
        raising=False,
    )
    raw_api_key = await issue_key(
        db_session,
        scopes=[COURSES_READ_SCOPE],
    )
    headers = {"X-API-KEY": raw_api_key}

    assert (await client.get("/api/courses", headers=headers)).status_code == 200
    assert (await client.get("/api/courses", headers=headers)).status_code == 200
    limited_response = await client.get(
        "/api/courses",
        headers=headers,
    )

    assert limited_response.status_code == 429
    assert limited_response.headers["Retry-After"] == "60"
    assert limited_response.headers["X-RateLimit-Remaining"] == "0"


def test_openapi_exposes_developer_api_key_scheme() -> None:
    schema = app.openapi()

    security_scheme = schema["components"]["securitySchemes"][
        "DeveloperApiKey"
    ]
    assert security_scheme["type"] == "apiKey"
    assert security_scheme["in"] == "header"
    assert security_scheme["name"] == "X-API-KEY"
    assert {} in schema["paths"]["/api/courses"]["get"]["security"]
    assert {} in schema["paths"]["/api/events"]["get"]["security"]
