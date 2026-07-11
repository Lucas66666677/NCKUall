from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth import AuthUser
from app.models import (
    LifeResource,
    LifeResourceType,
    LifeReview,
    LifeReviewType,
    ReviewModerationStatus,
    User,
)
from app.schemas import LifeReviewCreate
from app.privacy import anonymize_review_text, sanitize_nested_text
from app.security.karma import (
    APPROVED_REVIEW_KARMA,
    adjust_karma,
    ensure_user_profile,
    evaluate_life_review_submission,
)


def list_life_resources(
    db: Session,
    *,
    resource_type: LifeResourceType | None = None,
    area: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LifeResource]:
    """Return life assistant resources such as rentals, food, and services."""

    stmt = (
        select(LifeResource)
        .order_by(LifeResource.is_verified.desc(), LifeResource.rating.desc().nullslast(), LifeResource.name)
        .limit(limit)
        .offset(offset)
    )

    if resource_type is not None:
        stmt = stmt.where(LifeResource.resource_type == resource_type)

    if area:
        stmt = stmt.where(LifeResource.area.ilike(f"%{area.strip()}%"))

    return list(db.scalars(stmt).all())


async def create_life_review(
    db: AsyncSession,
    *,
    payload: LifeReviewCreate,
    user: AuthUser,
) -> LifeReview:
    """Create an anonymous student review after sanitizing every text field."""

    profile = await ensure_user_profile(db, user)
    decision = await evaluate_life_review_submission(
        db,
        payload=payload,
        author_user_id=profile.id,
    )
    review = LifeReview(
        life_resource_id=payload.life_resource_id,
        author_user_id=profile.id,
        review_type=payload.review_type,
        title=await anonymize_review_text(payload.title),
        content=await anonymize_review_text(payload.content),
        location_name=sanitize_nested_text(payload.location_name),
        area=sanitize_nested_text(payload.area),
        address=sanitize_nested_text(payload.address),
        rating=payload.rating,
        price_level=payload.price_level,
        author_alias="匿名同學",
        tags=sanitize_nested_text(payload.tags),
        metadata_json=sanitize_nested_text(payload.metadata),
        is_verified=False,
        is_approved=decision.is_approved,
        score=0,
        ai_spam_confidence=decision.ai_spam_confidence,
        moderation_status=decision.moderation_status,
    )
    db.add(review)
    if decision.is_approved:
        await adjust_karma(
            db,
            user_id=profile.id,
            delta=APPROVED_REVIEW_KARMA,
        )
    await db.commit()
    await db.refresh(review)
    return review


def list_life_reviews(
    db: Session,
    *,
    review_type: LifeReviewType | None = None,
    area: str | None = None,
    life_resource_id: UUID | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[LifeReview]:
    """Return student life reviews for frontend cards and lists."""

    stmt = (
        select(LifeReview)
        .outerjoin(User, LifeReview.author_user_id == User.id)
        .where(
            LifeReview.moderation_status == ReviewModerationStatus.APPROVED,
            LifeReview.is_approved.is_(True),
        )
        .order_by(
            (
                LifeReview.score
                + (func.coalesce(User.karma_points, 0) * 0.01)
            ).desc(),
            LifeReview.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    if review_type is not None:
        stmt = stmt.where(LifeReview.review_type == review_type)

    if area:
        stmt = stmt.where(LifeReview.area.ilike(f"%{area.strip()}%"))

    if life_resource_id is not None:
        stmt = stmt.where(LifeReview.life_resource_id == life_resource_id)

    if keyword:
        pattern = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                LifeReview.title.ilike(pattern),
                LifeReview.content.ilike(pattern),
                LifeReview.location_name.ilike(pattern),
            )
        )

    return list(db.scalars(stmt).all())
