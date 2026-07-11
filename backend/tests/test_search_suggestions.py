from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Activity, ActivityType, Course, Department


pytestmark = pytest.mark.integration


async def test_search_suggestions_rank_and_filter_resources(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    photonics = Department(
        code="DPS",
        name_zh="光電科學與工程學系",
    )
    electrical = Department(
        code="EE",
        name_zh="電機工程學系",
    )
    db_session.add_all(
        [
            Course(
                department=photonics,
                course_code="DPS2001",
                title_zh="光電工程導論",
                instructor_name="陳光電",
                required_for_major=True,
            ),
            Course(
                department=photonics,
                course_code="DPS3001",
                title_zh="雷射光學",
                instructor_name="林明德",
                required_for_major=False,
            ),
            Course(
                department=electrical,
                course_code="EE2001",
                title_zh="電機工程導論",
                instructor_name="陳光電",
                required_for_major=True,
            ),
            Activity(
                activity_type=ActivityType.LECTURE,
                title="光電產業校園講座",
                location="理學大樓",
            ),
        ]
    )
    await db_session.commit()
    await db_session.refresh(photonics)

    response = await client.get(
        "/api/search/suggestions",
        params={
            "keyword": "光電",
            "department_id": str(photonics.id),
        },
    )

    assert response.status_code == 200, response.text
    suggestions = response.json()
    assert len(suggestions) <= 8
    assert suggestions[0]["resource_type"] == "course"
    assert suggestions[0]["label"] == "光電工程導論"
    assert any(
        item["resource_type"] == "instructor"
        and item["label"] == "陳光電"
        for item in suggestions
    )
    assert any(
        item["resource_type"] == "event"
        and item["label"] == "光電產業校園講座"
        for item in suggestions
    )
    assert not any(
        item["label"] == "電機工程導論"
        for item in suggestions
    )


async def test_search_suggestions_ignore_one_character_queries(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/api/search/suggestions",
        params={"keyword": "光"},
    )

    assert response.status_code == 200
    assert response.json() == []
