from __future__ import annotations

import pytest
from app.models import Course, CourseReview, Department
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_course_search_uses_primary_database_without_supabase_credentials(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    photonics = Department(code="DPS", name_zh="光電科學與工程學系")
    electrical = Department(code="EE", name_zh="電機工程學系")
    db_session.add_all(
        [
            Course(
                department=photonics,
                course_code="DPS2001",
                title_zh="光電工程導論",
                title_en="Introduction to Photonics",
                instructor_name="陳光電",
                required_for_major=True,
                tags=["核心"],
            ),
            Course(
                department=electrical,
                course_code="EE2001",
                title_zh="電機工程導論",
                instructor_name="陳光電",
                tags=[],
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(photonics)

    response = await client.get(
        "/api/courses/search",
        params={"query": "光電", "department_id": str(photonics.id)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["query"] == "光電"
    assert payload["count"] == 1
    assert payload["results"][0]["course_code"] == "DPS2001"
    assert payload["results"][0]["href"].startswith("/courses/")


async def test_course_filter_uses_review_ratings_and_tags_from_primary_database(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    department = Department(code="CSIE", name_zh="資訊工程學系")
    recommended = Course(
        department=department,
        course_code="CSIE3001",
        title_zh="資料庫系統",
        instructor_name="林教授",
        description="週一 3-4 節",
        tags=["選修"],
    )
    demanding = Course(
        department=department,
        course_code="CSIE4001",
        title_zh="進階演算法",
        instructor_name="王教授",
        description="週一 5-6 節",
        tags=[],
    )
    db_session.add_all([recommended, demanding])
    await db_session.flush()
    db_session.add_all(
        [
            CourseReview(
                course=recommended,
                content="內容清楚，作業適中。",
                tags=["推薦", "點名"],
                grading_fairness_rating=5,
                difficulty_rating=2,
                is_approved=True,
            ),
            CourseReview(
                course=recommended,
                content="評分透明。",
                tags=["推薦"],
                grading_fairness_rating=4,
                difficulty_rating=3,
                is_approved=True,
            ),
            CourseReview(
                course=demanding,
                content="內容扎實但負擔較高。",
                tags=["推薦"],
                grading_fairness_rating=3,
                difficulty_rating=5,
                is_approved=True,
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(department)

    response = await client.get(
        "/api/courses/filter",
        params={
            "min_sweetness": 4,
            "max_hardness": 3,
            "tags": "推薦",
            "department_id": str(department.id),
            "weekday": "mon",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 1
    result = payload["results"][0]
    assert result["course_code"] == "CSIE3001"
    assert result["sweetness"] == 4.5
    assert result["hardness"] == 2.5
    assert result["review_count"] == 2
    assert result["review_tags"] == ["推薦", "點名"]
