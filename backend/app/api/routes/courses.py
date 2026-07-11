from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_optional_user, verify_ncku_user
from app.cache import (
    AsyncCacheManager,
    course_detail_cache_key,
    get_cache_manager,
    low_churn_cache_ttl_seconds,
)
from app.controllers.courses import (
    create_course_review,
    get_course_by_id_async,
    list_courses,
)
from app.controllers.analytics import record_search_log
from app.controllers.recommendations import (
    recommendation_user_id,
    record_user_view_log,
)
from app.database import (
    get_analytics_session_factory,
    get_async_db,
    get_db,
)
from app.schemas import (
    CourseResponse,
    CourseReviewCreate,
    CourseReviewResponse,
)
from app.security.developer_api import (
    COURSES_READ_SCOPE,
    DeveloperPrincipal,
    require_developer_scope,
)


router = APIRouter(prefix="/courses", tags=["courses"])


@router.get(
    "",
    response_model=list[CourseResponse],
    openapi_extra={"security": [{}]},
)
def get_courses(
    db: Annotated[Session, Depends(get_db)],
    _developer: Annotated[
        DeveloperPrincipal | None,
        Depends(require_developer_scope(COURSES_READ_SCOPE)),
    ],
    department_id: Annotated[UUID | None, Query(description="Filter courses by department UUID.")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CourseResponse]:
    """Get course list, optionally filtered by department."""

    return list_courses(
        db,
        department_id=department_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{course_id}/reviews",
    response_model=CourseReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an anonymous course review",
)
async def post_course_review(
    course_id: UUID,
    payload: CourseReviewCreate,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    cache_manager: Annotated[AsyncCacheManager, Depends(get_cache_manager)],
    current_user: Annotated[AuthUser, Depends(verify_ncku_user)],
) -> CourseReviewResponse:
    """Accept verified NCKU feedback after mandatory anonymization."""

    review = await create_course_review(
        db,
        course_id=course_id,
        payload=payload,
        user=current_user,
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定課程",
        )
    await cache_manager.delete(course_detail_cache_key(course_id))
    return CourseReviewResponse.model_validate(review)


@router.get(
    "/{course_id}",
    response_model=CourseResponse,
    summary="Get one course",
)
async def get_course(
    course_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_async_db)],
    analytics_session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_analytics_session_factory),
    ],
    current_user: Annotated[
        AuthUser | None,
        Depends(get_optional_user),
    ] = None,
) -> CourseResponse:
    """Get one course for detail pages and dynamic social metadata."""

    def schedule_course_view(course_response: CourseResponse) -> None:
        background_tasks.add_task(
            record_search_log,
            "course",
            course_response.id,
            session_factory=analytics_session_factory,
        )
        if current_user is not None:
            background_tasks.add_task(
                record_user_view_log,
                recommendation_user_id(
                    current_user.user_id,
                    current_user.email,
                ),
                "course",
                course_response.id,
                session_factory=analytics_session_factory,
            )

    cache_manager = get_cache_manager(request)
    cache_key = course_detail_cache_key(course_id)
    cached = await cache_manager.get_model(cache_key, CourseResponse)
    if cached.hit:
        response.headers["X-Cache"] = "HIT"
        course_response = cached.value
        schedule_course_view(course_response)
        return course_response

    course = await get_course_by_id_async(db, course_id=course_id)
    if course is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定課程",
        )
    course_response = CourseResponse.model_validate(course)
    await cache_manager.set_model(
        cache_key,
        course_response,
        model_type=CourseResponse,
        ttl_seconds=low_churn_cache_ttl_seconds(),
    )
    response.headers["X-Cache"] = "MISS" if cache_manager.enabled else "BYPASS"
    schedule_course_view(course_response)
    return course_response
