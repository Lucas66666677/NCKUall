from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.cache import AsyncCacheManager, course_detail_cache_key
from app.models import CourseDifficulty
from app.schemas import CourseResponse


class InMemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.ttls: dict[str, int | None] = {}
        self.deleted: list[str] = []

    async def get(self, name: str) -> str | bytes | None:
        return self.values.get(name)

    async def set(
        self,
        name: str,
        value: str | bytes,
        ex: int | None = None,
    ) -> bool:
        self.values[name] = value
        self.ttls[name] = ex
        return True

    async def delete(self, *names: str) -> int:
        self.deleted.extend(names)
        removed = 0
        for name in names:
            if name in self.values:
                removed += 1
                del self.values[name]
        return removed


class BrokenRedis(InMemoryRedis):
    async def get(self, name: str) -> str | bytes | None:
        raise ConnectionError("redis unavailable")

    async def set(
        self,
        name: str,
        value: str | bytes,
        ex: int | None = None,
    ) -> bool:
        raise ConnectionError("redis unavailable")

    async def delete(self, *names: str) -> int:
        raise ConnectionError("redis unavailable")


def course_response() -> CourseResponse:
    now = datetime.now(UTC)
    return CourseResponse(
        id=uuid4(),
        department_id=uuid4(),
        department=None,
        course_code="DPS1001",
        title_zh="光電導論",
        title_en=None,
        instructor_name="林教授",
        academic_year=115,
        semester=1,
        credits=Decimal("3.0"),
        required_for_major=True,
        tags=["核心"],
        syllabus_url=None,
        description="課程簡介",
        difficulty=CourseDifficulty.UNKNOWN,
        grade_distributions=[],
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_cache_manager_round_trips_pydantic_models() -> None:
    redis = InMemoryRedis()
    cache = AsyncCacheManager(redis)
    key = course_detail_cache_key("course-1")
    payload = course_response()

    await cache.set_model(
        key,
        payload,
        model_type=CourseResponse,
        ttl_seconds=60,
    )
    cached = await cache.get_model(key, CourseResponse)

    assert cached.hit is True
    assert cached.value == payload
    assert redis.ttls[key] == 60


@pytest.mark.asyncio
async def test_cache_manager_degrades_when_redis_fails() -> None:
    cache = AsyncCacheManager(BrokenRedis())
    key = course_detail_cache_key("course-1")
    payload = course_response()

    cached = await cache.get_model(key, CourseResponse)
    await cache.set_model(
        key,
        payload,
        model_type=CourseResponse,
        ttl_seconds=60,
    )
    await cache.delete(key)

    assert cached.hit is False
