from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Activity,
    ActivityType,
    CareerDocumentChunk,
    Course,
    Department,
    SearchLog,
)


pytestmark = pytest.mark.integration


async def test_course_detail_records_anonymous_background_interaction(
    client: AsyncClient,
    db_session: AsyncSession,
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

    response = await client.get(f"/api/courses/{course.id}")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(course.id)
    logged = await db_session.scalar(
        select(SearchLog).where(
            SearchLog.resource_type == "course",
            SearchLog.resource_id == course.id,
        )
    )
    assert logged is not None
    assert not hasattr(logged, "user_id")
    assert not hasattr(logged, "ip_address")


async def test_trending_returns_ranked_courses_labs_and_events(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    department = Department(code="EE", name_zh="電機工程學系")
    course = Course(
        department=department,
        course_code="EE2001",
        title_zh="訊號與系統",
        instructor_name="王教授",
        required_for_major=True,
    )
    lab = CareerDocumentChunk(
        department=department,
        source_type="html",
        source_title="智慧光電實驗室",
        category="lab_project",
        chunk_index=0,
        content="研究智慧感測與光電系統。",
        metadata_json={"professor_name": "林教授實驗室"},
    )
    event = Activity(
        activity_type=ActivityType.BIKE_FESTIVAL,
        title="成大單車節",
        location="光復校區",
    )
    db_session.add_all([course, lab, event])
    await db_session.flush()

    recent = datetime.now(UTC) - timedelta(hours=2)
    old = datetime.now(UTC) - timedelta(hours=80)
    db_session.add_all(
        [
            SearchLog(
                resource_type="course",
                resource_id=course.id,
                created_at=recent,
            ),
            SearchLog(
                resource_type="course",
                resource_id=course.id,
                created_at=recent,
            ),
            SearchLog(
                resource_type="lab",
                resource_id=lab.id,
                created_at=recent,
            ),
            SearchLog(
                resource_type="event",
                resource_id=event.id,
                created_at=recent,
            ),
            SearchLog(
                resource_type="event",
                resource_id=event.id,
                created_at=old,
            ),
            SearchLog(
                resource_type="search",
                resource_id=None,
                created_at=recent,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/api/analytics/trending")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window_hours"] == 74
    assert body["courses"][0] == {
        "resource_type": "course",
        "resource_id": str(course.id),
        "title": "訊號與系統",
        "subtitle": "王教授",
        "interaction_count": 2,
        "href": f"/courses/{course.id}",
    }
    assert body["labs"][0]["resource_id"] == str(lab.id)
    assert body["labs"][0]["title"] == "林教授實驗室"
    assert body["labs"][0]["interaction_count"] == 1
    assert body["events"][0]["resource_id"] == str(event.id)
    assert body["events"][0]["interaction_count"] == 1

    total_logs = await db_session.scalar(select(func.count(SearchLog.id)))
    assert total_logs == 6


async def test_event_visit_records_click_and_redirects_safely(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    event = Activity(
        activity_type=ActivityType.OFFICIAL_EVENT,
        title="校園講座",
        official_url="https://example.edu.tw/events/lecture",
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)

    response = await client.get(
        f"/api/events/{event.id}/visit",
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == event.official_url
    logged = await db_session.scalar(
        select(SearchLog).where(
            SearchLog.resource_type == "event",
            SearchLog.resource_id == event.id,
        )
    )
    assert logged is not None
