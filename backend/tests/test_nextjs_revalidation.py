from __future__ import annotations

import json

import httpx
import pytest

from app.integrations import nextjs


@pytest.mark.asyncio
async def test_revalidate_life_page_sends_authenticated_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_request: httpx.Request | None = None

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"revalidated": True})

    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handle_request)
    monkeypatch.setenv(
        "FRONTEND_REVALIDATE_URL",
        "https://nckuall.example/api/revalidate/life",
    )
    monkeypatch.setenv("REVALIDATION_SECRET", "shared-secret")
    monkeypatch.setattr(
        nextjs.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=transport,
            **kwargs,
        ),
    )

    await nextjs.revalidate_life_page(
        "44444444-4444-4444-8444-444444444444",
    )

    assert captured_request is not None
    assert captured_request.headers["authorization"] == "Bearer shared-secret"
    assert captured_request.url == (
        "https://nckuall.example/api/revalidate/life"
    )
    assert json.loads(captured_request.content) == {
        "event": "life.review.created",
        "review_id": "44444444-4444-4444-8444-444444444444",
    }
