from __future__ import annotations

from collections.abc import Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LifeReview


pytestmark = pytest.mark.integration

REVIEW_PAYLOAD = {
    "review_type": "protein_meal_prep",
    "title": "東寧路高蛋白備餐採買",
    "content": "雞胸肉與板豆腐價格穩定，適合學生一週備餐。",
    "location_name": "東寧市場",
    "area": "東寧路",
    "rating": 5,
    "price_level": 2,
    "tags": ["高蛋白", "備餐"],
    "metadata": {"source": "student_review"},
}


async def test_guest_can_browse_but_cannot_create_review(
    client: AsyncClient,
) -> None:
    browse_response = await client.get("/api/life/reviews")
    assert browse_response.status_code == 200
    assert browse_response.json() == []

    create_response = await client.post(
        "/api/life/reviews",
        json=REVIEW_PAYLOAD,
    )
    assert create_response.status_code == 401
    assert create_response.json()["detail"] == "請先登入"
    assert create_response.headers["www-authenticate"] == "Bearer"


async def test_non_ncku_email_cannot_create_review(
    client: AsyncClient,
    make_access_token: Callable[[str], str],
) -> None:
    token = make_access_token("test@gmail.com")

    response = await client.post(
        "/api/life/reviews",
        json=REVIEW_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "發布評價需綁定成大信箱"
    assert response.json()["error_code"] == "http_error"


async def test_ncku_email_can_create_and_persist_review(
    client: AsyncClient,
    db_session: AsyncSession,
    make_access_token: Callable[[str], str],
) -> None:
    token = make_access_token("student@gs.ncku.edu.tw")

    response = await client.post(
        "/api/life/reviews",
        json=REVIEW_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["review_type"] == "protein_meal_prep"
    assert body["title"] == REVIEW_PAYLOAD["title"]
    assert body["author_alias"] == "匿名同學"
    assert body["is_verified"] is False
    assert body["metadata_json"] == {"source": "student_review"}

    persisted = await db_session.scalar(
        select(LifeReview).where(LifeReview.id == body["id"])
    )
    assert persisted is not None
    assert persisted.content == REVIEW_PAYLOAD["content"]
    assert persisted.author_alias == "匿名同學"

    browse_response = await client.get("/api/life/reviews")
    assert browse_response.status_code == 200
    assert [item["id"] for item in browse_response.json()] == [body["id"]]
