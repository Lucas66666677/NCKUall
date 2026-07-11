from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.auth import AuthUser, verify_ncku_user
from app.controllers.life import create_life_review, list_life_resources, list_life_reviews
from app.database import get_async_db, get_db
from app.integrations.nextjs import revalidate_life_page
from app.models import LifeResourceType, LifeReviewType
from app.schemas import LifeResourceResponse, LifeReviewCreate, LifeReviewResponse
from app.security.karma import create_life_review_upvote, flag_life_review_for_moderation


router = APIRouter(prefix="/life", tags=["life"])


@router.get(
    "",
    response_model=list[LifeResourceResponse],
    summary="List life assistant resources",
    description="Return rentals, food places, study spaces, transportation notes, and other campus-life resources.",
    response_description="Life resources sorted by verification status and rating.",
)
def get_life_resources(
    db: Annotated[Session, Depends(get_db)],
    resource_type: Annotated[LifeResourceType | None, Query(description="Filter by rental, food, service, etc.")] = None,
    area: Annotated[str | None, Query(description="Filter by area or neighborhood text.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LifeResourceResponse]:
    """Get life assistant resources such as rentals and food places."""

    return list_life_resources(
        db,
        resource_type=resource_type,
        area=area,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/reviews",
    response_model=LifeReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a student life review",
    description=(
        "Create a student-generated review for rental warnings, rental recommendations, "
        "food recommendations, or high-protein meal-prep ingredient sources around NCKU."
    ),
    response_description="The created review card data.",
)
async def post_life_review(
    payload: LifeReviewCreate,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(verify_ncku_user)],
) -> LifeReviewResponse:
    """Create a life assistant review shared by a student."""

    review = await create_life_review(db, payload=payload, user=current_user)
    background_tasks.add_task(revalidate_life_page, review.id)
    return review


@router.post(
    "/reviews/{review_id}/upvote",
    response_model=LifeReviewResponse,
    summary="Upvote a verified life review",
    description="Give one NCKU-verified upvote to a public review. Repeated upvotes from the same user are idempotent.",
)
async def upvote_life_review(
    review_id: UUID,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(verify_ncku_user)],
) -> LifeReviewResponse:
    review = await create_life_review_upvote(
        db,
        review_id=review_id,
        voter=current_user,
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定評論",
        )
    background_tasks.add_task(revalidate_life_page, review.id)
    return review


@router.post(
    "/reviews/{review_id}/flag",
    response_model=LifeReviewResponse,
    summary="Flag a suspicious or harmful life review",
    description="Move a review into the moderation queue. Karma is only penalized after an administrator confirms the report.",
)
async def flag_life_review(
    review_id: UUID,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(verify_ncku_user)],
) -> LifeReviewResponse:
    review = await flag_life_review_for_moderation(
        db,
        review_id=review_id,
        reporter=current_user,
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定評論",
        )
    background_tasks.add_task(revalidate_life_page, review.id)
    return review


@router.get(
    "/reviews",
    response_model=list[LifeReviewResponse],
    summary="List student life reviews",
    description=(
        "List student-shared life reviews. Supports filtering by review type, area, "
        "related life resource, and keyword for frontend review boards."
    ),
    response_description="Review cards sorted from newest to oldest.",
)
def get_life_reviews(
    db: Annotated[Session, Depends(get_db)],
    review_type: Annotated[
        LifeReviewType | None,
        Query(description="Filter by rental_warning, food_recommendation, protein_meal_prep, etc."),
    ] = None,
    area: Annotated[str | None, Query(description="Filter by area text, e.g. 東寧路 or 勝利校區.")] = None,
    life_resource_id: Annotated[UUID | None, Query(description="Filter reviews linked to a specific life resource.")] = None,
    keyword: Annotated[str | None, Query(description="Search title, content, and location name.")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum number of reviews returned.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Pagination offset.")] = 0,
) -> list[LifeReviewResponse]:
    """Get student life reviews for the life assistant board."""

    return list_life_reviews(
        db,
        review_type=review_type,
        area=area,
        life_resource_id=life_resource_id,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
