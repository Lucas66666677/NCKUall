from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status as status_codes
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth import AuthUser
from app.models import (
    ChatHistory,
    Course,
    CourseSubmissionStatus,
    CourseVisualSubmission,
    LifeReview,
    ReviewModerationStatus,
    User,
)
from app.security.karma import (
    APPROVED_REVIEW_KARMA,
    CONFIRMED_FLAG_PENALTY,
    email_hash,
    user_profile_id,
)


logger = logging.getLogger(__name__)
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")


def ensure_user_profile_sync(db: Session, user: AuthUser) -> User:
    """Materialise the users row for `user` on a synchronous Session.

    The async `ensure_user_profile` cannot be awaited from the admin routes,
    which run on a sync Session. `course_visual_submissions.reviewed_by_user_id`
    is a real foreign key into users, so an administrator whose profile row has
    never been created -- entirely possible, since admin rights come from the
    JWT rather than from any local row -- would otherwise make every approve or
    reject fail with a foreign key violation.
    """

    profile_id = user_profile_id(user)
    profile = db.get(User, profile_id)
    if profile is None:
        profile = User(id=profile_id, email_hash=email_hash(user), karma_points=0)
        db.add(profile)
        db.flush()
    elif profile.email_hash is None:
        profile.email_hash = email_hash(user)
        db.flush()
    return profile


def unresolved_flagged_reviews_filter() -> ColumnElement[bool]:
    """Build the shared predicate used by the queue and dashboard count."""

    return or_(
        (
            (LifeReview.report_count > 0)
            & LifeReview.moderated_at.is_(None)
        ),
        LifeReview.moderation_status == ReviewModerationStatus.PENDING,
    )


def list_flagged_reviews(
    db: Session,
    *,
    limit: int,
    offset: int,
) -> tuple[list[LifeReview], int]:
    """Return reported or pending reviews with deterministic pagination."""

    flagged_filter = unresolved_flagged_reviews_filter()
    total = db.scalar(
        select(func.count(LifeReview.id)).where(flagged_filter)
    ) or 0
    statement = (
        select(LifeReview)
        .where(flagged_filter)
        .order_by(
            case(
                (
                    LifeReview.moderation_status
                    == ReviewModerationStatus.PENDING,
                    0,
                ),
                else_=1,
            ),
            LifeReview.last_reported_at.desc().nullslast(),
            LifeReview.created_at.desc(),
            LifeReview.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all()), int(total)


def update_review_status(
    db: Session,
    *,
    review_id: UUID,
    moderation_status: ReviewModerationStatus,
    admin_user_id: str,
) -> LifeReview | None:
    """Update moderation status and preserve an administrator audit trail."""

    review = db.get(LifeReview, review_id)
    if review is None:
        return None

    previous_status = review.moderation_status
    review.moderation_status = moderation_status
    review.is_approved = moderation_status == ReviewModerationStatus.APPROVED
    review.moderated_at = datetime.now(UTC)
    review.moderated_by = admin_user_id
    author = db.get(User, review.author_user_id) if review.author_user_id else None
    if (
        author is not None
        and moderation_status == ReviewModerationStatus.APPROVED
        and previous_status != ReviewModerationStatus.APPROVED
    ):
        author.karma_points += APPROVED_REVIEW_KARMA
    if (
        author is not None
        and moderation_status == ReviewModerationStatus.HIDDEN
        and previous_status != ReviewModerationStatus.HIDDEN
        and review.report_count > 0
    ):
        author.karma_points += CONFIRMED_FLAG_PENALTY
    db.commit()
    db.refresh(review)

    logger.info(
        "admin_review_status_updated",
        extra={
            "review_id": str(review_id),
            "admin_user_id": admin_user_id,
            "previous_status": previous_status.value,
            "new_status": moderation_status.value,
        },
    )
    return review


COURSE_SUBMISSION_APPLIED_FIELDS = (
    "title_zh",
    "title_en",
    "instructor_name",
    "academic_year",
    "semester",
    "credits",
    "required_for_major",
    "syllabus_url",
    "description",
)


def list_course_submissions(
    db: Session,
    *,
    status: CourseSubmissionStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[CourseVisualSubmission], int]:
    """Return queued course edits, oldest first so nothing starves."""

    conditions = []
    if status is not None:
        conditions.append(CourseVisualSubmission.status == status)
    total = db.scalar(
        select(func.count(CourseVisualSubmission.id)).where(*conditions)
    ) or 0
    statement = (
        select(CourseVisualSubmission)
        .where(*conditions)
        .order_by(
            CourseVisualSubmission.created_at,
            CourseVisualSubmission.id,
        )
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(statement).all()), int(total)


def review_course_submission(
    db: Session,
    *,
    submission_id: UUID,
    approve: bool,
    admin_user: AuthUser,
) -> CourseVisualSubmission | None:
    """Approve a queued course edit onto the live row, or reject it.

    Approving copies only the fields the visual extraction actually owns, and
    only where the submission carried a value -- a null in the proposal means
    "not read from the document", not "clear this field".
    """

    # Lock the row before reading its status: without this, two administrators
    # clicking approve at the same moment both see PENDING and both write the
    # proposal onto the live course. PostgreSQL enforces the lock; SQLite
    # treats it as a no-op.
    reviewer = ensure_user_profile_sync(db, admin_user)
    submission = db.scalar(
        select(CourseVisualSubmission)
        .where(CourseVisualSubmission.id == submission_id)
        .with_for_update()
    )
    if submission is None:
        return None
    if submission.status is not CourseSubmissionStatus.PENDING:
        raise HTTPException(
            status_code=status_codes.HTTP_409_CONFLICT,
            detail="此課程提交已審核過",
        )

    if approve:
        course = db.get(Course, submission.course_id)
        if course is None:
            raise HTTPException(
                status_code=status_codes.HTTP_404_NOT_FOUND,
                detail="找不到對應課程",
            )
        proposed = submission.proposed or {}
        for field in COURSE_SUBMISSION_APPLIED_FIELDS:
            value = proposed.get(field)
            if value is None:
                continue
            if field == "credits":
                value = Decimal(str(value))
            setattr(course, field, value)
        course.updated_at = datetime.now(UTC)

    submission.status = (
        CourseSubmissionStatus.APPROVED
        if approve
        else CourseSubmissionStatus.REJECTED
    )
    submission.reviewed_by_user_id = reviewer.id
    submission.reviewed_at = datetime.now(UTC)
    db.commit()
    db.refresh(submission)

    logger.info(
        "admin_course_submission_reviewed",
        extra={
            "submission_id": str(submission_id),
            "course_id": str(submission.course_id),
            "admin_user_id": reviewer.id,
            "decision": submission.status.value,
        },
    )
    return submission


def get_admin_dashboard_stats(db: Session) -> tuple[int, int, list[str]]:
    """Return review counts and frequently repeated recent chat searches."""

    now_taipei = datetime.now(TAIPEI_TIMEZONE)
    start_of_today_utc = now_taipei.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ).astimezone(UTC)
    today_new_reviews = db.scalar(
        select(func.count(LifeReview.id)).where(
            LifeReview.created_at >= start_of_today_utc
        )
    ) or 0
    pending_flagged_reviews = db.scalar(
        select(func.count(LifeReview.id)).where(
            unresolved_flagged_reviews_filter()
        )
    ) or 0

    popular_rows = db.execute(
        select(
            ChatHistory.content,
            func.count(ChatHistory.id).label("query_count"),
        )
        .where(ChatHistory.role == "human")
        .group_by(ChatHistory.content)
        .order_by(func.count(ChatHistory.id).desc(), ChatHistory.content)
        .limit(5)
    ).all()
    popular_search_terms = [
        str(row.content)[:80]
        for row in popular_rows
    ]
    return (
        int(today_new_reviews),
        int(pending_flagged_reviews),
        popular_search_terms,
    )
