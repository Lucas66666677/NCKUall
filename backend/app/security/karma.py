from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser
from app.models import (
    CourseReview,
    LifeReview,
    LifeReviewFlag,
    LifeReviewVote,
    ReviewModerationStatus,
    User,
)
from app.schemas import LifeReviewCreate


APPROVED_REVIEW_KARMA = 10
UPVOTE_KARMA = 2
CONFIRMED_FLAG_PENALTY = -20
MAX_REVIEWS_PER_HOUR = 3
DUPLICATE_SIMILARITY_THRESHOLD = 0.85
FLAG_HIDE_THRESHOLD = 3


@dataclass(frozen=True)
class ReviewModerationDecision:
    moderation_status: ReviewModerationStatus
    is_approved: bool
    ai_spam_confidence: Decimal
    recent_review_count: int
    max_similarity: float


def user_profile_id(user: AuthUser) -> str:
    return user.user_id or hashlib.sha256(user.email.encode("utf-8")).hexdigest()


def email_hash(user: AuthUser) -> str:
    return hashlib.sha256(user.email.encode("utf-8")).hexdigest()


async def ensure_user_profile(
    db: AsyncSession,
    user: AuthUser,
) -> User:
    profile_id = user_profile_id(user)
    profile = await db.get(User, profile_id)
    if profile is None:
        profile = User(
            id=profile_id,
            email_hash=email_hash(user),
            karma_points=0,
        )
        db.add(profile)
        await db.flush()
    elif profile.email_hash is None:
        profile.email_hash = email_hash(user)
        await db.flush()
    return profile


def normalize_review_text(value: str) -> str:
    return "".join(value.lower().split())


def levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) < len(right):
        left, right = right, left

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (
                0 if left_char == right_char else 1
            )
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def levenshtein_similarity(left: str, right: str) -> float:
    left_normalized = normalize_review_text(left)
    right_normalized = normalize_review_text(right)
    max_length = max(len(left_normalized), len(right_normalized))
    if max_length == 0:
        return 1.0
    distance = levenshtein_distance(left_normalized, right_normalized)
    return round(1 - distance / max_length, 4)


async def adjust_karma(
    db: AsyncSession,
    *,
    user_id: str | None,
    delta: int,
) -> None:
    if not user_id or delta == 0:
        return
    profile = await db.get(User, user_id)
    if profile is None:
        return
    profile.karma_points += delta
    await db.flush()


async def evaluate_life_review_submission(
    db: AsyncSession,
    *,
    payload: LifeReviewCreate,
    author_user_id: str,
) -> ReviewModerationDecision:
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    recent_count = await db.scalar(
        select(func.count(LifeReview.id)).where(
            LifeReview.author_user_id == author_user_id,
            LifeReview.created_at >= one_hour_ago,
        )
    )
    recent_review_count = int(recent_count or 0)

    submitted_text = f"{payload.title}\n{payload.content}"
    duplicate_scope = select(LifeReview.title, LifeReview.content).where(
        LifeReview.moderation_status != ReviewModerationStatus.HIDDEN,
    )
    if payload.life_resource_id is not None:
        duplicate_scope = duplicate_scope.where(
            LifeReview.life_resource_id == payload.life_resource_id,
        )
    else:
        duplicate_scope = duplicate_scope.where(
            LifeReview.review_type == payload.review_type,
            or_(
                LifeReview.area == payload.area,
                LifeReview.location_name == payload.location_name,
            ),
        )

    rows = (await db.execute(duplicate_scope.limit(80))).all()
    max_similarity = 0.0
    for title, content in rows:
        existing_text = f"{title}\n{content}"
        max_similarity = max(
            max_similarity,
            levenshtein_similarity(submitted_text, existing_text),
        )
        if max_similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="疑似重複或複製貼上的評論，請改寫為自己的真實經驗。",
            )

    if recent_review_count >= MAX_REVIEWS_PER_HOUR:
        return ReviewModerationDecision(
            moderation_status=ReviewModerationStatus.PENDING,
            is_approved=False,
            ai_spam_confidence=Decimal("0.7000"),
            recent_review_count=recent_review_count,
            max_similarity=max_similarity,
        )

    spam_confidence = Decimal(str(round(min(max_similarity, 0.84), 4)))
    return ReviewModerationDecision(
        moderation_status=ReviewModerationStatus.APPROVED,
        is_approved=True,
        ai_spam_confidence=spam_confidence,
        recent_review_count=recent_review_count,
        max_similarity=max_similarity,
    )


async def create_life_review_upvote(
    db: AsyncSession,
    *,
    review_id: UUID,
    voter: AuthUser,
) -> LifeReview | None:
    voter_profile = await ensure_user_profile(db, voter)
    review = await db.get(LifeReview, review_id)
    if review is None:
        return None
    if (
        review.moderation_status == ReviewModerationStatus.HIDDEN
        or not review.is_approved
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此評論目前不可投票",
        )
    if review.author_user_id == voter_profile.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能替自己的評論按讚",
        )

    existing_vote = await db.scalar(
        select(LifeReviewVote).where(
            LifeReviewVote.life_review_id == review_id,
            LifeReviewVote.voter_user_id == voter_profile.id,
        )
    )
    if existing_vote is not None:
        return review

    db.add(
        LifeReviewVote(
            life_review_id=review_id,
            voter_user_id=voter_profile.id,
            value=1,
        )
    )
    review.score += Decimal("1")
    await adjust_karma(
        db,
        user_id=review.author_user_id,
        delta=UPVOTE_KARMA,
    )
    await db.commit()
    await db.refresh(review)
    return review


async def flag_life_review_for_moderation(
    db: AsyncSession,
    *,
    review_id: UUID,
    reporter: AuthUser,
) -> LifeReview | None:
    reporter_profile = await ensure_user_profile(db, reporter)
    # Serialize reports for the same review so concurrent requests cannot both
    # pass the duplicate check or lose a report_count increment. PostgreSQL
    # enforces the row lock; SQLite safely treats it as a no-op in tests.
    review = await db.scalar(
        select(LifeReview)
        .where(LifeReview.id == review_id)
        .with_for_update()
    )
    if review is None:
        return None
    if review.moderation_status == ReviewModerationStatus.HIDDEN:
        return review

    existing_flag = await db.scalar(
        select(LifeReviewFlag).where(
            LifeReviewFlag.life_review_id == review_id,
            LifeReviewFlag.reporter_user_id == reporter_profile.id,
        )
    )
    if existing_flag is not None:
        return review

    db.add(
        LifeReviewFlag(
            life_review_id=review_id,
            reporter_user_id=reporter_profile.id,
        )
    )
    review.report_count += 1
    review.last_reported_at = datetime.now(UTC)
    # A single report enters the admin moderation queue for awareness, but only
    # a threshold of *distinct* reporters (enforced by the unique constraint on
    # LifeReviewFlag) takes the review off the public board automatically.
    # Preserves legitimate abuse-reporting while preventing one account from
    # unilaterally silencing another user's review.
    if review.report_count >= FLAG_HIDE_THRESHOLD:
        review.moderation_status = ReviewModerationStatus.PENDING
        review.is_approved = False
    await db.commit()
    await db.refresh(review)
    return review


async def evaluate_course_review_duplicate(
    db: AsyncSession,
    *,
    course_id: UUID,
    content: str,
) -> float:
    rows = (
        await db.execute(
            select(CourseReview.content)
            .where(CourseReview.course_id == course_id)
            .limit(80)
        )
    ).all()
    max_similarity = 0.0
    for (existing_content,) in rows:
        max_similarity = max(
            max_similarity,
            levenshtein_similarity(content, existing_content),
        )
        if max_similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="疑似重複或複製貼上的課程評價，請改寫為自己的真實經驗。",
            )
    return max_similarity
