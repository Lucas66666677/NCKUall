from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import ChatHistory, LifeReview, ReviewModerationStatus, User
from app.security.karma import APPROVED_REVIEW_KARMA, CONFIRMED_FLAG_PENALTY


logger = logging.getLogger(__name__)
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")


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
