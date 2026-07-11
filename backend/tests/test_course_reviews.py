from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import CacheReadResult, course_detail_cache_key
from app.main import app
from app.models import Course, CourseDifficulty, CourseReview, Department
from app.schemas import CourseResponse


pytestmark = pytest.mark.integration


class AccessTokenFactory(Protocol):
    def __call__(
        self,
        email: str,
        *,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        ...


class RouteCacheDouble:
    def __init__(self, cached_model: CourseResponse | None = None) -> None:
        self.cached_model = cached_model
        self.deleted_keys: list[str] = []
        self.written_keys: list[str] = []
        self.enabled = True

    async def get_model(
        self,
        key: str,
        model_type: type[CourseResponse],
    ) -> CacheReadResult:
        if self.cached_model is None:
            return CacheReadResult(hit=False)
        return CacheReadResult(hit=True, value=self.cached_model)

    async def set_model(
        self,
        key: str,
        value: CourseResponse | object,
        *,
        model_type: type[CourseResponse],
        ttl_seconds: int,
    ) -> None:
        self.written_keys.append(key)

    async def delete(self, *keys: str) -> None:
        self.deleted_keys.extend(keys)


def cached_course_response(course_id: str) -> CourseResponse:
    now = datetime.now(UTC)
    return CourseResponse(
        id=course_id,
        department_id=uuid4(),
        department=None,
        course_code="DPS9999",
        title_zh="快取中的光電專題",
        title_en=None,
        instructor_name="快取教授",
        academic_year=115,
        semester=1,
        credits=Decimal("3.0"),
        required_for_major=False,
        tags=[],
        syllabus_url=None,
        description="這筆資料只存在快取，不存在資料庫。",
        difficulty=CourseDifficulty.UNKNOWN,
        grade_distributions=[],
        created_at=now,
        updated_at=now,
    )


async def test_course_detail_can_be_served_from_cache(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    course_id = uuid4()
    fake_cache = RouteCacheDouble(cached_course_response(str(course_id)))
    monkeypatch.setattr(app.state, "cache_manager", fake_cache, raising=False)

    response = await client.get(f"/api/courses/{course_id}")

    assert response.status_code == 200, response.text
    assert response.headers["X-Cache"] == "HIT"
    assert response.json()["title_zh"] == "快取中的光電專題"
    assert fake_cache.written_keys == []


async def test_course_detail_cache_miss_is_written(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    department = Department(code="DPS", name_zh="光電科學與工程學系")
    course = Course(
        department=department,
        course_code="DPS1001",
        title_zh="光電導論",
        required_for_major=True,
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)
    fake_cache = RouteCacheDouble()
    monkeypatch.setattr(app.state, "cache_manager", fake_cache, raising=False)

    response = await client.get(f"/api/courses/{course.id}")

    assert response.status_code == 200, response.text
    assert response.headers["X-Cache"] == "MISS"
    assert fake_cache.written_keys == [course_detail_cache_key(course.id)]


async def test_verified_user_course_review_is_anonymized_before_insert(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    make_access_token: AccessTokenFactory,
) -> None:
    department = Department(code="DPS", name_zh="光電科學與工程學系")
    course = Course(
        department=department,
        course_code="DPS2001",
        title_zh="光電工程",
        required_for_major=True,
    )
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)
    token = make_access_token("student@gs.ncku.edu.tw")
    fake_cache = RouteCacheDouble()
    monkeypatch.setattr(app.state, "cache_manager", fake_cache, raising=False)

    response = await client.post(
        f"/api/courses/{course.id}/reviews",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "reviewer_department_id": str(department.id),
            "overall_rating": 5,
            "workload_rating": 4,
            "difficulty_rating": 4,
            "grading_fairness_rating": 5,
            "content": (
                "陳教授講解清楚，但請不要公開我的學號 E14012345，"
                "手機是 0912-345-678。"
            ),
            "tags": ["理學大樓", "E14012345"],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert "陳教授" in body["content"]
    assert "E14012345" not in body["content"]
    assert "0912-345-678" not in body["content"]
    assert body["content"].count("[隱私屏蔽]") == 2
    assert body["tags"] == ["理學大樓", "[隱私屏蔽]"]

    persisted = await db_session.scalar(
        select(CourseReview).where(CourseReview.id == body["id"])
    )
    assert persisted is not None
    assert persisted.content == body["content"]
    assert persisted.is_verified is True
    assert fake_cache.deleted_keys == [course_detail_cache_key(course.id)]
