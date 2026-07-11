from uuid import UUID
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.models import Course, CourseReview
from app.privacy import anonymize_review_text, sanitize_nested_text
from app.schemas import CourseReviewCreate
from app.auth import AuthUser
from app.security.karma import (
    APPROVED_REVIEW_KARMA,
    adjust_karma,
    ensure_user_profile,
    evaluate_course_review_duplicate,
)


def list_courses(
    db: Session,
    *,
    department_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Course]:
    """Return courses, optionally constrained to a specific department."""

    stmt = (
        select(Course)
        .options(selectinload(Course.department), selectinload(Course.grade_distributions))
        .order_by(Course.academic_year.desc().nullslast(), Course.semester.desc().nullslast(), Course.course_code)
        .limit(limit)
        .offset(offset)
    )

    if department_id is not None:
        stmt = stmt.where(Course.department_id == department_id)

    return list(db.scalars(stmt).all())


def get_course_by_id(db: Session, *, course_id: UUID) -> Course | None:
    """Return one course with metadata needed by detail and SEO pages."""

    statement = (
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.department),
            selectinload(Course.grade_distributions),
        )
    )
    return db.scalar(statement)


async def get_course_by_id_async(
    db: AsyncSession,
    *,
    course_id: UUID,
) -> Course | None:
    """Return one course with grade distributions using async SQLAlchemy."""

    statement = (
        select(Course)
        .where(Course.id == course_id)
        .options(
            selectinload(Course.department),
            selectinload(Course.grade_distributions),
        )
    )
    return await db.scalar(statement)


async def create_course_review(
    db: AsyncSession,
    *,
    course_id: UUID,
    payload: CourseReviewCreate,
    user: AuthUser,
) -> CourseReview | None:
    """Persist a verified-user review without storing account identifiers."""

    course = await db.get(Course, course_id)
    if course is None:
        return None

    profile = await ensure_user_profile(db, user)
    max_similarity = await evaluate_course_review_duplicate(
        db,
        course_id=course_id,
        content=payload.content,
    )
    review = CourseReview(
        course_id=course_id,
        author_user_id=profile.id,
        reviewer_department_id=payload.reviewer_department_id,
        overall_rating=payload.overall_rating,
        workload_rating=payload.workload_rating,
        difficulty_rating=payload.difficulty_rating,
        grading_fairness_rating=payload.grading_fairness_rating,
        content=await anonymize_review_text(payload.content),
        tags=sanitize_nested_text(payload.tags),
        is_verified=True,
        is_approved=True,
        score=0,
        ai_spam_confidence=Decimal(str(max_similarity)),
    )
    db.add(review)
    await adjust_karma(
        db,
        user_id=profile.id,
        delta=APPROVED_REVIEW_KARMA,
    )
    await db.commit()
    await db.refresh(review)
    return review
