from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Activity,
    ActivityType,
    Course,
    CourseDifficulty,
    CourseSubmissionStatus,
    CourseVisualSubmission,
    Department,
)
from app.visual_ingestion.schemas import (
    CourseVisualExtraction,
    EventVisualExtraction,
)


EVENT_TAGS = ["AI 視覺匯入", "校園活動"]
COURSE_TAGS = ["AI 視覺匯入", "課程簡章"]


@dataclass(frozen=True)
class PersistedVisualResource:
    action: str
    resource_id: UUID
    title: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def merged_tags(
    existing: list[str] | None,
    required: list[str],
) -> list[str]:
    return list(dict.fromkeys([*(existing or []), *required]))


async def resolve_department(
    db: AsyncSession,
    extraction: CourseVisualExtraction,
) -> Department:
    conditions = [Department.is_active.is_(True)]
    if extraction.department_code:
        conditions.append(
            func.lower(Department.code)
            == normalize_text(extraction.department_code).lower()
        )
    if extraction.department_name:
        conditions.append(
            Department.name_zh
            == normalize_text(extraction.department_name)
        )
    departments = list(
        (
            await db.scalars(
                select(Department).where(and_(*conditions))
            )
        ).all()
    )
    if len(departments) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "department_not_resolved",
                "message": "無法將課程唯一對應至成大科系",
            },
        )
    return departments[0]


async def upsert_event_from_visual(
    db: AsyncSession,
    extraction: EventVisualExtraction,
    *,
    is_admin: bool,
) -> PersistedVisualResource:
    assert extraction.event_name is not None
    assert extraction.start_at is not None
    assert extraction.location is not None
    assert extraction.organizer is not None
    assert extraction.summary is not None

    title = normalize_text(extraction.event_name)
    lock_key = (
        f"visual:event:{title.casefold()}:"
        f"{extraction.start_at.isoformat()}"
    )
    try:
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(:lock_key))"
            ),
            {"lock_key": lock_key},
        )
        activity = await db.scalar(
            select(Activity).where(
                func.lower(Activity.title) == title.lower(),
                Activity.start_at == extraction.start_at,
            )
        )
        action = "updated" if activity is not None else "created"
        if activity is None:
            activity = Activity(
                activity_type=ActivityType.OFFICIAL_EVENT,
                title=title,
                organizer_name=normalize_text(extraction.organizer),
                description=normalize_text(extraction.summary),
                location=normalize_text(extraction.location),
                start_at=extraction.start_at,
                end_at=extraction.end_at,
                registration_url=(
                    extraction.registration_url
                    if extraction.registration_url
                    else None
                ),
                tags=EVENT_TAGS.copy(),
                is_official=is_admin,
            )
            db.add(activity)
        else:
            activity.organizer_name = normalize_text(
                extraction.organizer
            )
            activity.description = normalize_text(
                extraction.summary
            )
            activity.location = normalize_text(extraction.location)
            activity.end_at = extraction.end_at
            activity.registration_url = extraction.registration_url
            activity.tags = merged_tags(activity.tags, EVENT_TAGS)
            activity.is_official = activity.is_official or is_admin
            activity.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(activity)
        return PersistedVisualResource(
            action=action,
            resource_id=activity.id,
            title=activity.title,
            tags=activity.tags,
            created_at=activity.created_at,
            updated_at=activity.updated_at,
        )
    except Exception:
        await db.rollback()
        raise


async def queue_course_submission(
    db: AsyncSession,
    *,
    course: Course,
    extraction: CourseVisualExtraction,
    submitted_by_user_id: str | None,
    upload_sha256: str | None,
) -> PersistedVisualResource:
    """Record a proposed edit for admin review, leaving the course untouched."""

    submission = CourseVisualSubmission(
        course_id=course.id,
        submitted_by_user_id=submitted_by_user_id,
        status=CourseSubmissionStatus.PENDING,
        proposed=extraction.model_dump(mode="json"),
        confidence=Decimal(str(extraction.confidence)),
        upload_sha256=upload_sha256,
    )
    db.add(submission)
    await db.commit()
    await db.refresh(submission)
    return PersistedVisualResource(
        action="pending_review",
        resource_id=submission.id,
        title=course.title_zh,
        tags=list(course.tags or []),
        created_at=submission.created_at,
        updated_at=submission.updated_at,
    )


async def upsert_course_from_visual(
    db: AsyncSession,
    extraction: CourseVisualExtraction,
    *,
    is_admin: bool,
    submitted_by_user_id: str | None = None,
    upload_sha256: str | None = None,
) -> PersistedVisualResource:
    assert extraction.course_code is not None
    assert extraction.title_zh is not None

    try:
        department = await resolve_department(db, extraction)
        course_code = normalize_text(
            extraction.course_code
        ).upper()
        await db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(:lock_key))"
            ),
            {
                "lock_key": (
                    f"visual:course:{department.id}:{course_code}"
                )
            },
        )
        course = await db.scalar(
            select(Course).where(
                Course.department_id == department.id,
                Course.course_code == course_code,
            )
        )
        if course is not None and not is_admin:
            # A verified NCKU account may still add a course the catalog is
            # missing, but editing one that already exists goes to the admin
            # queue rather than straight into the canonical row.
            return await queue_course_submission(
                db,
                course=course,
                extraction=extraction,
                submitted_by_user_id=submitted_by_user_id,
                upload_sha256=upload_sha256,
            )

        action = "updated" if course is not None else "created"
        if course is None:
            course = Course(
                department_id=department.id,
                course_code=course_code,
                title_zh=normalize_text(extraction.title_zh),
                title_en=(
                    normalize_text(extraction.title_en)
                    if extraction.title_en
                    else None
                ),
                instructor_name=(
                    normalize_text(extraction.instructor_name)
                    if extraction.instructor_name
                    else None
                ),
                academic_year=extraction.academic_year,
                semester=extraction.semester,
                credits=(
                    Decimal(str(extraction.credits))
                    if extraction.credits is not None
                    else None
                ),
                required_for_major=bool(
                    extraction.required_for_major
                ),
                tags=COURSE_TAGS.copy(),
                syllabus_url=extraction.syllabus_url,
                description=(
                    extraction.description.strip()
                    if extraction.description
                    else None
                ),
                difficulty=CourseDifficulty.UNKNOWN,
            )
            db.add(course)
        else:
            course.title_zh = normalize_text(extraction.title_zh)
            if extraction.title_en is not None:
                course.title_en = normalize_text(
                    extraction.title_en
                )
            if extraction.instructor_name is not None:
                course.instructor_name = normalize_text(
                    extraction.instructor_name
                )
            if extraction.academic_year is not None:
                course.academic_year = extraction.academic_year
            if extraction.semester is not None:
                course.semester = extraction.semester
            if extraction.credits is not None:
                course.credits = Decimal(
                    str(extraction.credits)
                )
            if extraction.required_for_major is not None:
                course.required_for_major = (
                    extraction.required_for_major
                )
            if extraction.syllabus_url is not None:
                course.syllabus_url = extraction.syllabus_url
            if extraction.description is not None:
                course.description = extraction.description.strip()
            course.tags = merged_tags(course.tags, COURSE_TAGS)
            course.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(course)
        return PersistedVisualResource(
            action=action,
            resource_id=course.id,
            title=course.title_zh,
            tags=course.tags,
            created_at=course.created_at,
            updated_at=course.updated_at,
        )
    except Exception:
        await db.rollback()
        raise
