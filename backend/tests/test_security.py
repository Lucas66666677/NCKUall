from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response

from app.auth import AuthUser, verify_admin_user
from app.schemas import ChatRequest
from app.security.cors import (
    get_cors_headers,
    get_cors_methods,
    get_cors_origins,
)
from app.security.exceptions import install_exception_handlers
from app.security.guardrails import enforce_chat_guardrails
from app.security.rate_limit import (
    RedisRateLimiter,
    enforce_chat_rate_limit,
)


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


def make_request(app: FastAPI, *, client_ip: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat",
            "headers": [],
            "client": (client_ip, 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": app,
        }
    )


def test_admin_role_requires_trusted_app_metadata() -> None:
    admin = AuthUser(
        user_id="admin-123",
        email="admin@ncku.edu.tw",
        claims={"app_metadata": {"is_admin": True}},
    )

    assert verify_admin_user(admin) is admin


def test_user_metadata_cannot_self_assign_admin_role() -> None:
    user = AuthUser(
        user_id="user-123",
        email="student@gs.ncku.edu.tw",
        claims={"user_metadata": {"is_admin": True}},
    )

    with pytest.raises(HTTPException) as exc_info:
        verify_admin_user(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "僅限管理員存取"


async def test_guest_rate_limit_blocks_sixth_request_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_RATE_LIMIT_ENABLED", "true")
    app = FastAPI()
    app.state.chat_rate_limiter = RedisRateLimiter(FakeRedis())
    request = make_request(app)

    for remaining in range(4, -1, -1):
        response = Response()
        await enforce_chat_rate_limit(request, response, None)
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert response.headers["X-RateLimit-Remaining"] == str(remaining)

    with pytest.raises(HTTPException) as exc_info:
        await enforce_chat_rate_limit(request, Response(), None)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers is not None
    assert exc_info.value.headers["Retry-After"] == "60"
    assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"


async def test_authenticated_user_receives_twenty_request_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.auth import AuthUser

    monkeypatch.setenv("CHAT_RATE_LIMIT_ENABLED", "true")
    app = FastAPI()
    app.state.chat_rate_limiter = RedisRateLimiter(FakeRedis())
    request = make_request(app)
    user = AuthUser(
        user_id="user-123",
        email="student@gs.ncku.edu.tw",
        claims={},
    )

    for _ in range(20):
        response = Response()
        await enforce_chat_rate_limit(request, response, user)

    assert response.headers["X-RateLimit-Limit"] == "20"
    assert response.headers["X-RateLimit-Remaining"] == "0"

    with pytest.raises(HTTPException) as exc_info:
        await enforce_chat_rate_limit(request, Response(), user)
    assert exc_info.value.status_code == 429


async def test_guardrail_rejects_flagged_input_before_rag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHAT_MODERATION_ENABLED", "true")
    app = FastAPI()
    guardrail = SimpleNamespace(is_blocked=AsyncMock(return_value=True))
    app.state.chat_moderation_guardrail = guardrail
    payload = ChatRequest(
        session_id="security-test",
        user_query="unsafe content",
        department_filter="光電科學與工程學系",
    )

    with pytest.raises(HTTPException) as exc_info:
        await enforce_chat_guardrails(
            payload,
            make_request(app),
            None,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "輸入內容違反社群安全規範"
    guardrail.is_blocked.assert_awaited_once_with("unsafe content")


async def test_unexpected_exception_response_hides_internal_details() -> None:
    test_app = FastAPI()
    install_exception_handlers(test_app)

    @test_app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("database password=do-not-leak")

    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/explode")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "系統暫時無法處理請求，請稍後再試。"
    assert body["error_code"] == "server_error"
    assert body["safe_error_id"].startswith("NCKUALL-500-")
    assert "request_id" in body
    assert "password" not in response.text


async def test_http_500_exception_response_hides_detail() -> None:
    test_app = FastAPI()
    install_exception_handlers(test_app)

    @test_app.get("/http-500")
    async def http_500() -> None:
        raise HTTPException(
            status_code=500,
            detail="SELECT * FROM private_table password=do-not-leak",
        )

    transport = ASGITransport(app=test_app, raise_app_exceptions=False)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/http-500")

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "系統暫時無法處理請求，請稍後再試。"
    assert body["error_code"] == "server_error"
    assert body["safe_error_id"].startswith("NCKUALL-500-")
    assert "SELECT" not in response.text
    assert "password" not in response.text


def test_cors_credentials_rejects_wildcard_origins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
    monkeypatch.setenv("CORS_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        get_cors_origins()


def test_cors_credentials_uses_explicit_methods_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://nckuall.vercel.app, https://nckuall.example",
    )
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    assert get_cors_origins() == [
        "https://nckuall.vercel.app",
        "https://nckuall.example",
    ]
    assert "*" not in get_cors_methods()
    assert "*" not in get_cors_headers()
