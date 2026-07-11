from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.database import AsyncSessionLocal
from app.models import (
    Activity,
    CareerDocumentChunk,
    Course,
    Department,
    SearchLog,
)
from app.schemas import TrendingResourceResponse, TrendingResponse


logger = logging.getLogger(__name__)
TRENDING_WINDOW_HOURS = 74
TRENDING_LIMIT = 5


async def record_search_log(
    resource_type: str,
    resource_id: UUID | None,
    *,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> None:
    """
    Persist one anonymous interaction in an isolated background transaction.

    Analytics failures are intentionally swallowed after structured logging:
    a metrics write must never turn a successful resource request into an
    application error.
    """

    try:
        async with session_factory() as db:
            db.add(
                SearchLog(
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
            )
            await db.commit()
    except SQLAlchemyError:
        logger.warning(
            "anonymous_search_log_write_failed",
            extra={
                "resource_type": resource_type,
                "resource_id": str(resource_id) if resource_id else None,
            },
            exc_info=True,
        )


def _course_trends(
    db: Session,
    *,
    since: datetime,
    limit: int,
) -> list[TrendingResourceResponse]:
    interaction_count = func.count(SearchLog.id).label("interaction_count")
    rows = db.execute(
        select(
            Course.id,
            Course.title_zh,
            Course.instructor_name,
            interaction_count,
        )
        .join(SearchLog, SearchLog.resource_id == Course.id)
        .where(
            SearchLog.resource_type == "course",
            SearchLog.created_at >= since,
        )
        .group_by(Course.id, Course.title_zh, Course.instructor_name)
        .order_by(interaction_count.desc(), Course.title_zh)
        .limit(limit)
    ).all()
    return [
        TrendingResourceResponse(
            resource_type="course",
            resource_id=row.id,
            title=row.title_zh,
            subtitle=row.instructor_name,
            interaction_count=int(row.interaction_count),
            href=f"/courses/{row.id}",
        )
        for row in rows
    ]


def _lab_trends(
    db: Session,
    *,
    since: datetime,
    limit: int,
) -> list[TrendingResourceResponse]:
    interaction_count = func.count(SearchLog.id).label("interaction_count")
    professor_name = CareerDocumentChunk.metadata_json[
        "professor_name"
    ].astext
    title = func.coalesce(
        professor_name,
        CareerDocumentChunk.source_title,
        "實驗室資源",
    ).label("title")
    rows = db.execute(
        select(
            CareerDocumentChunk.id,
            title,
            Department.name_zh.label("department_name"),
            interaction_count,
        )
        .join(
            SearchLog,
            SearchLog.resource_id == CareerDocumentChunk.id,
        )
        .outerjoin(
            Department,
            CareerDocumentChunk.department_id == Department.id,
        )
        .where(
            SearchLog.resource_type == "lab",
            SearchLog.created_at >= since,
        )
        .group_by(
            CareerDocumentChunk.id,
            Department.name_zh,
        )
        .order_by(interaction_count.desc(), title)
        .limit(limit)
    ).all()
    return [
        TrendingResourceResponse(
            resource_type="lab",
            resource_id=row.id,
            title=row.title,
            subtitle=row.department_name,
            interaction_count=int(row.interaction_count),
            href="/careers?category=實驗室",
        )
        for row in rows
    ]


def _event_trends(
    db: Session,
    *,
    since: datetime,
    limit: int,
) -> list[TrendingResourceResponse]:
    interaction_count = func.count(SearchLog.id).label("interaction_count")
    rows = db.execute(
        select(
            Activity.id,
            Activity.title,
            Activity.location,
            interaction_count,
        )
        .join(SearchLog, SearchLog.resource_id == Activity.id)
        .where(
            SearchLog.resource_type == "event",
            SearchLog.created_at >= since,
        )
        .group_by(Activity.id, Activity.title, Activity.location)
        .order_by(interaction_count.desc(), Activity.title)
        .limit(limit)
    ).all()
    return [
        TrendingResourceResponse(
            resource_type="event",
            resource_id=row.id,
            title=row.title,
            subtitle=row.location,
            interaction_count=int(row.interaction_count),
            href=f"/events#event-{row.id}",
        )
        for row in rows
    ]


def get_trending_resources(
    db: Session,
    *,
    window_hours: int = TRENDING_WINDOW_HOURS,
    limit: int = TRENDING_LIMIT,
) -> TrendingResponse:
    """Return ranked resources from the rolling anonymous event window."""

    since = datetime.now(UTC) - timedelta(hours=window_hours)
    return TrendingResponse(
        window_hours=window_hours,
        courses=_course_trends(db, since=since, limit=limit),
        labs=_lab_trends(db, since=since, limit=limit),
        events=_event_trends(db, since=since, limit=limit),
    )
