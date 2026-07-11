from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, get_current_user
from app.controllers.recommendations import (
    create_user_view_log,
    get_personalized_recommendations,
    recommendation_user_id,
)
from app.database import get_async_db
from app.schemas import (
    RecommendationResponse,
    UserViewLogCreate,
    UserViewLogResponse,
)


router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get(
    "",
    response_model=RecommendationResponse,
    summary="Get personalized resource recommendations",
    description=(
        "Build a per-user profile vector from the user's latest resource views, "
        "then retrieve similar courses and career resources using pgvector."
    ),
)
async def get_recommendations(
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    department_id: Annotated[
        UUID | None,
        Query(description="Prefer recommendations from this department."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=12)] = 6,
) -> RecommendationResponse:
    """Return mixed course and career recommendations for a signed-in user."""

    try:
        return await get_personalized_recommendations(
            db,
            user_id=recommendation_user_id(
                current_user.user_id,
                current_user.email,
            ),
            department_id=department_id,
            limit=limit,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="推薦資料庫查詢暫時失敗",
        ) from exc


@router.post(
    "/views",
    response_model=UserViewLogResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an authenticated resource view",
)
async def post_user_view_log(
    payload: UserViewLogCreate,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> UserViewLogResponse:
    """Record a lightweight personalized recommendation signal."""

    try:
        await create_user_view_log(
            db,
            user_id=recommendation_user_id(
                current_user.user_id,
                current_user.email,
            ),
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
        )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="目前無法記錄瀏覽行為",
        ) from exc

    return UserViewLogResponse(
        resource_type=payload.resource_type,
        resource_id=payload.resource_id,
    )
