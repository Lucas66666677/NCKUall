from typing import Annotated
from functools import lru_cache
from os import getenv
from pathlib import Path
import re
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
from pydantic import BaseModel, Field
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


class CourseSearchItem(BaseModel):
    """Lightweight course payload for typeahead/autocomplete UI."""

    id: UUID
    course_code: str
    title_zh: str
    title_en: str | None = None
    instructor_name: str | None = None
    department_id: UUID
    credits: float | None = None
    required_for_major: bool = False
    tags: list[str] = Field(default_factory=list)
    href: str


class CourseFilterItem(CourseSearchItem):
    """Course result enriched with AI review signals."""

    sweetness: float | None = None
    chillness: float | None = None
    hardness: float | None = None
    ai_summary: str | None = None
    review_tags: list[str] = Field(default_factory=list)
    review_count: int = 0


class CourseSearchResponse(BaseModel):
    query: str
    count: int
    results: list[CourseSearchItem]


class CourseFilterResponse(BaseModel):
    count: int
    results: list[CourseFilterItem]


def _load_dotenv_for_local_dev() -> None:
    """Load local env files lazily without overriding production variables."""

    try:
        from dotenv import load_dotenv

        backend_root = Path(__file__).resolve().parents[3]
        project_root = backend_root.parent
        load_dotenv(project_root / ".env", override=False)
        load_dotenv(backend_root / ".env", override=False)
    except Exception:
        return


@lru_cache(maxsize=1)
def _supabase_client():
    _load_dotenv_for_local_dev()
    supabase_url = getenv("SUPABASE_URL", "").strip()
    supabase_key = (
        getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or getenv("SUPABASE_ANON_KEY", "").strip()
    )
    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/SUPABASE_ANON_KEY are required."
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise RuntimeError("supabase Python SDK is not installed.") from exc
    return create_client(supabase_url, supabase_key)


def _get_supabase_or_503():
    try:
        return _supabase_client()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Supabase client unavailable: {exc}",
        ) from exc


def _safe_search_text(value: str) -> str:
    text = re.sub(r"[\x00-\x1f,(){}]", " ", value.strip())
    text = re.sub(r"\s+", " ", text)
    return text[:80]


def _as_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _course_item_from_row(row: dict) -> CourseSearchItem:
    return CourseSearchItem(
        id=row["id"],
        course_code=row.get("course_code") or "",
        title_zh=row.get("title_zh") or "",
        title_en=row.get("title_en"),
        instructor_name=row.get("instructor_name"),
        department_id=row["department_id"],
        credits=_as_float(row.get("credits")),
        required_for_major=bool(row.get("required_for_major")),
        tags=row.get("tags") or [],
        href=f"/courses/{row['id']}",
    )


def _weekday_terms(weekday: str) -> list[str]:
    normalized = weekday.strip().lower()
    mapping = {
        "1": ["週一", "星期一", "禮拜一", "一", "mon", "monday"],
        "monday": ["週一", "星期一", "禮拜一", "mon", "monday"],
        "mon": ["週一", "星期一", "禮拜一", "mon", "monday"],
        "2": ["週二", "星期二", "禮拜二", "二", "tue", "tuesday"],
        "tuesday": ["週二", "星期二", "禮拜二", "tue", "tuesday"],
        "tue": ["週二", "星期二", "禮拜二", "tue", "tuesday"],
        "3": ["週三", "星期三", "禮拜三", "三", "wed", "wednesday"],
        "wednesday": ["週三", "星期三", "禮拜三", "wed", "wednesday"],
        "wed": ["週三", "星期三", "禮拜三", "wed", "wednesday"],
        "4": ["週四", "星期四", "禮拜四", "四", "thu", "thursday"],
        "thursday": ["週四", "星期四", "禮拜四", "thu", "thursday"],
        "thu": ["週四", "星期四", "禮拜四", "thu", "thursday"],
        "5": ["週五", "星期五", "禮拜五", "五", "fri", "friday"],
        "friday": ["週五", "星期五", "禮拜五", "fri", "friday"],
        "fri": ["週五", "星期五", "禮拜五", "fri", "friday"],
        "6": ["週六", "星期六", "禮拜六", "六", "sat", "saturday"],
        "saturday": ["週六", "星期六", "禮拜六", "sat", "saturday"],
        "sat": ["週六", "星期六", "禮拜六", "sat", "saturday"],
        "7": ["週日", "週天", "星期日", "星期天", "禮拜日", "日", "sun", "sunday"],
        "sunday": ["週日", "週天", "星期日", "星期天", "禮拜日", "sun", "sunday"],
        "sun": ["週日", "週天", "星期日", "星期天", "禮拜日", "sun", "sunday"],
    }
    return mapping.get(normalized, [weekday.strip()])


def _course_query_base(client):
    return client.table("courses").select(
        "id,course_code,title_zh,title_en,instructor_name,department_id,"
        "credits,required_for_major,tags,description"
    )


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


@router.get(
    "/search",
    response_model=CourseSearchResponse,
    summary="Fast fuzzy course autocomplete",
    description=(
        "Search course title, instructor name, and course code with a "
        "small response payload designed for autocomplete dropdowns."
    ),
)
def search_courses(
    query: Annotated[
        str,
        Query(min_length=1, max_length=80, description="Course title, instructor, or course code."),
    ],
    department_id: Annotated[
        UUID | None,
        Query(description="Optionally restrict suggestions to one department."),
    ] = None,
) -> CourseSearchResponse:
    """Return the top 10 lightweight fuzzy matches from Supabase."""

    keyword = _safe_search_text(query)
    if not keyword:
        return CourseSearchResponse(query=query, count=0, results=[])

    client = _get_supabase_or_503()
    pattern = f"%{keyword}%"
    supabase_query = (
        _course_query_base(client)
        .or_(
            ",".join(
                [
                    f"title_zh.ilike.{pattern}",
                    f"title_en.ilike.{pattern}",
                    f"instructor_name.ilike.{pattern}",
                    f"course_code.ilike.{pattern}",
                ]
            )
        )
        .limit(10)
    )
    if department_id is not None:
        supabase_query = supabase_query.eq("department_id", str(department_id))

    response = supabase_query.execute()
    results = [_course_item_from_row(row) for row in response.data or []]
    return CourseSearchResponse(query=keyword, count=len(results), results=results)


@router.get(
    "/filter",
    response_model=CourseFilterResponse,
    summary="Advanced AI-powered course filter",
    description=(
        "Filter courses by AI-enriched review scores and tags, then attach "
        "a representative AI summary for each course."
    ),
)
def filter_courses(
    min_sweetness: Annotated[
        float | None,
        Query(ge=1, le=5, description="Minimum AI sweetness score."),
    ] = None,
    max_hardness: Annotated[
        float | None,
        Query(ge=1, le=5, description="Maximum AI hardness score."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Query(description="Require all review tags, e.g. tags=點名&tags=期中超難."),
    ] = None,
    department_id: Annotated[
        UUID | None,
        Query(description="Restrict courses to one department."),
    ] = None,
    weekday: Annotated[
        str | None,
        Query(max_length=20, description="Optional weekday text/number, e.g. 1, mon, 週一."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CourseFilterResponse:
    """Filter by course_reviews AI columns and enrich matching courses."""

    client = _get_supabase_or_503()
    review_query = (
        client.table("course_reviews")
        .select("course_id,sweetness,hardness,chillness,tags,ai_summary,created_at")
        .eq("is_approved", True)
    )
    if min_sweetness is not None:
        review_query = review_query.gte("sweetness", min_sweetness)
    if max_hardness is not None:
        review_query = review_query.lte("hardness", max_hardness)
    clean_tags = [tag.strip() for tag in tags or [] if tag.strip()]
    if clean_tags:
        review_query = review_query.contains("tags", clean_tags)

    review_response = review_query.limit(500).execute()
    review_rows = [row for row in review_response.data or [] if row.get("course_id")]
    if not review_rows:
        return CourseFilterResponse(count=0, results=[])

    summaries_by_course: dict[str, dict] = {}
    for row in review_rows:
        course_id = str(row["course_id"])
        bucket = summaries_by_course.setdefault(
            course_id,
            {
                "review_count": 0,
                "sweetness_values": [],
                "hardness_values": [],
                "chillness_values": [],
                "review_tags": [],
                "ai_summary": None,
                "latest_created_at": "",
            },
        )
        bucket["review_count"] += 1
        for key, values_key in (
            ("sweetness", "sweetness_values"),
            ("hardness", "hardness_values"),
            ("chillness", "chillness_values"),
        ):
            value = _as_float(row.get(key))
            if value is not None:
                bucket[values_key].append(value)
        for tag in row.get("tags") or []:
            if tag not in bucket["review_tags"]:
                bucket["review_tags"].append(tag)
        created_at = str(row.get("created_at") or "")
        if row.get("ai_summary") and created_at >= bucket["latest_created_at"]:
            bucket["ai_summary"] = row.get("ai_summary")
            bucket["latest_created_at"] = created_at

    course_ids = list(summaries_by_course.keys())
    course_query = _course_query_base(client).in_("id", course_ids)
    if department_id is not None:
        course_query = course_query.eq("department_id", str(department_id))
    if weekday:
        weekday_filters = [
            f"description.ilike.%{_safe_search_text(term)}%"
            for term in _weekday_terms(weekday)
            if _safe_search_text(term)
        ]
        if weekday_filters:
            course_query = course_query.or_(",".join(weekday_filters))

    course_response = course_query.range(offset, offset + limit - 1).execute()
    results: list[CourseFilterItem] = []
    for row in course_response.data or []:
        course_item = _course_item_from_row(row)
        stats = summaries_by_course.get(str(row["id"]), {})

        def average(values_key: str) -> float | None:
            values = stats.get(values_key) or []
            return round(sum(values) / len(values), 2) if values else None

        results.append(
            CourseFilterItem(
                **course_item.model_dump(),
                sweetness=average("sweetness_values"),
                hardness=average("hardness_values"),
                chillness=average("chillness_values"),
                ai_summary=stats.get("ai_summary"),
                review_tags=stats.get("review_tags") or [],
                review_count=int(stats.get("review_count") or 0),
            )
        )

    results.sort(
        key=lambda item: (
            -(item.sweetness or 0),
            item.hardness if item.hardness is not None else 99,
            -item.review_count,
        )
    )
    return CourseFilterResponse(count=len(results), results=results)


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
