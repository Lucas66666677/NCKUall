from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import AsyncSessionLocal
from app.models import (
    CareerDocumentChunk,
    CareerResource,
    Course,
    Department,
    UserViewLog,
)
from app.schemas import RecommendationItemResponse, RecommendationResponse


logger = logging.getLogger(__name__)
RECENT_VIEW_LIMIT = 10
DEFAULT_RECOMMENDATION_LIMIT = 6
LANE_MULTIPLIER = 5
DEPARTMENT_BONUS = 0.12


@dataclass(frozen=True)
class RecommendationCandidate:
    resource_type: str
    resource_id: UUID
    title: str
    subtitle: str | None
    department_id: UUID | None
    department_name: str | None
    href: str
    similarity_score: float
    tags: list[str]
    adjusted_score: float


def recommendation_user_id(user_id: str | None, email: str) -> str:
    """Use Supabase subject as the stable key, falling back to email."""

    return user_id or email


def normalize_vector(vector: object) -> list[float]:
    if vector is None:
        return []
    if isinstance(vector, list):
        return [float(value) for value in vector]
    try:
        return [float(value) for value in vector]  # type: ignore[operator]
    except TypeError:
        return []


def average_vectors(vectors: list[list[float]]) -> list[float]:
    """Average same-dimensional vectors into one user profile vector."""

    target_dimension = next((len(vector) for vector in vectors if vector), 0)
    if target_dimension == 0:
        return []
    clean_vectors = [
        vector
        for vector in vectors
        if len(vector) == target_dimension
    ]
    if not clean_vectors:
        return []

    dimension = len(clean_vectors[0])
    sums = [0.0] * dimension
    for vector in clean_vectors:
        for index, value in enumerate(vector):
            sums[index] += value
    count = float(len(clean_vectors))
    return [value / count for value in sums]


def cosine_distance_to_similarity(distance: object) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    # pgvector cosine distance is usually in [0, 2]. Clamp for UI stability.
    return max(0.0, min(1.0, 1.0 - value))


def score_candidate(
    *,
    similarity_score: float,
    department_id: UUID | None,
    preferred_department_id: UUID | None,
) -> float:
    bonus = (
        DEPARTMENT_BONUS
        if preferred_department_id is not None
        and department_id == preferred_department_id
        else 0.0
    )
    return round(similarity_score + bonus, 6)


def recommendation_reason(
    candidate: RecommendationCandidate,
    *,
    preferred_department_id: UUID | None,
) -> str:
    if (
        preferred_department_id is not None
        and candidate.department_id == preferred_department_id
    ):
        return "和你最近查看的資源語意相近，且優先符合目前選取科系。"
    if candidate.resource_type == "course":
        return "和你最近查看的課程或研究方向高度相關。"
    return "和你最近關注的實驗室、計畫或職涯方向相近。"


def merge_candidates(
    candidates: list[RecommendationCandidate],
    *,
    viewed_keys: set[tuple[str, UUID]],
    preferred_department_id: UUID | None,
    limit: int,
) -> list[RecommendationItemResponse]:
    deduped: dict[tuple[str, UUID], RecommendationCandidate] = {}
    for candidate in candidates:
        key = (candidate.resource_type, candidate.resource_id)
        if key in viewed_keys:
            continue
        current = deduped.get(key)
        if current is None or candidate.adjusted_score > current.adjusted_score:
            deduped[key] = candidate

    ranked = sorted(
        deduped.values(),
        key=lambda item: item.adjusted_score,
        reverse=True,
    )[:limit]
    return [
        RecommendationItemResponse(
            resource_type=candidate.resource_type,  # type: ignore[arg-type]
            resource_id=candidate.resource_id,
            title=candidate.title,
            subtitle=candidate.subtitle,
            department_id=candidate.department_id,
            department_name=candidate.department_name,
            href=candidate.href,
            reason=recommendation_reason(
                candidate,
                preferred_department_id=preferred_department_id,
            ),
            similarity_score=round(candidate.similarity_score, 4),
            adjusted_score=round(candidate.adjusted_score, 4),
            tags=candidate.tags,
        )
        for candidate in ranked
    ]


async def record_user_view_log(
    user_id: str,
    resource_type: str,
    resource_id: UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> None:
    try:
        async with session_factory() as db:
            db.add(
                UserViewLog(
                    user_id=user_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
            )
            await db.commit()
    except SQLAlchemyError:
        logger.warning(
            "user_view_log_write_failed",
            extra={
                "user_id": user_id,
                "resource_type": resource_type,
                "resource_id": str(resource_id),
            },
            exc_info=True,
        )


async def create_user_view_log(
    db: AsyncSession,
    *,
    user_id: str,
    resource_type: str,
    resource_id: UUID,
) -> None:
    db.add(
        UserViewLog(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )
    await db.commit()


async def load_recent_views(
    db: AsyncSession,
    *,
    user_id: str,
    limit: int = RECENT_VIEW_LIMIT,
) -> list[UserViewLog]:
    rows = list(
        (
            await db.scalars(
                select(UserViewLog)
                .where(UserViewLog.user_id == user_id)
                .order_by(UserViewLog.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return rows


async def load_profile_vectors(
    db: AsyncSession,
    views: list[UserViewLog],
) -> list[list[float]]:
    ids_by_type: dict[str, list[UUID]] = defaultdict(list)
    for view in views:
        ids_by_type[view.resource_type].append(view.resource_id)

    vectors: list[list[float]] = []
    course_ids = ids_by_type.get("course", [])
    if course_ids:
        rows = (
            await db.execute(
                select(Course.embedding)
                .where(Course.id.in_(course_ids))
                .where(Course.embedding.is_not(None))
            )
        ).all()
        vectors.extend(normalize_vector(row.embedding) for row in rows)

    career_ids = ids_by_type.get("career", [])
    if career_ids:
        resource_rows = (
            await db.execute(
                select(CareerResource.embedding)
                .where(CareerResource.id.in_(career_ids))
                .where(CareerResource.embedding.is_not(None))
            )
        ).all()
        chunk_rows = (
            await db.execute(
                select(CareerDocumentChunk.embedding)
                .where(CareerDocumentChunk.id.in_(career_ids))
                .where(CareerDocumentChunk.embedding.is_not(None))
            )
        ).all()
        vectors.extend(normalize_vector(row.embedding) for row in resource_rows)
        vectors.extend(normalize_vector(row.embedding) for row in chunk_rows)

    return [vector for vector in vectors if vector]


async def recommend_courses(
    db: AsyncSession,
    *,
    profile_vector: list[float],
    viewed_course_ids: set[UUID],
    preferred_department_id: UUID | None,
    limit: int,
) -> list[RecommendationCandidate]:
    distance = Course.embedding.cosine_distance(profile_vector).label("distance")
    statement = (
        select(
            Course.id,
            Course.title_zh,
            Course.course_code,
            Course.instructor_name,
            Course.department_id,
            Course.tags,
            Department.name_zh.label("department_name"),
            distance,
        )
        .join(Department, Course.department_id == Department.id)
        .where(Course.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    if viewed_course_ids:
        statement = statement.where(Course.id.notin_(viewed_course_ids))

    rows = (await db.execute(statement)).mappings().all()
    candidates: list[RecommendationCandidate] = []
    for row in rows:
        similarity = cosine_distance_to_similarity(row["distance"])
        department_id = row["department_id"]
        candidates.append(
            RecommendationCandidate(
                resource_type="course",
                resource_id=row["id"],
                title=row["title_zh"],
                subtitle=row["instructor_name"] or row["course_code"],
                department_id=department_id,
                department_name=row["department_name"],
                href=f"/courses/{row['id']}",
                similarity_score=similarity,
                adjusted_score=score_candidate(
                    similarity_score=similarity,
                    department_id=department_id,
                    preferred_department_id=preferred_department_id,
                ),
                tags=row["tags"] or [],
            )
        )
    return candidates


async def recommend_career_chunks(
    db: AsyncSession,
    *,
    profile_vector: list[float],
    viewed_career_ids: set[UUID],
    preferred_department_id: UUID | None,
    limit: int,
) -> list[RecommendationCandidate]:
    distance = CareerDocumentChunk.embedding.cosine_distance(profile_vector).label(
        "distance"
    )
    statement = (
        select(
            CareerDocumentChunk.id,
            CareerDocumentChunk.source_title,
            CareerDocumentChunk.category,
            CareerDocumentChunk.source_url,
            CareerDocumentChunk.metadata_json,
            CareerDocumentChunk.department_id,
            Department.name_zh.label("department_name"),
            distance,
        )
        .outerjoin(Department, CareerDocumentChunk.department_id == Department.id)
        .where(CareerDocumentChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    )
    if viewed_career_ids:
        statement = statement.where(
            CareerDocumentChunk.id.notin_(viewed_career_ids)
        )

    rows = (await db.execute(statement)).mappings().all()
    candidates: list[RecommendationCandidate] = []
    for row in rows:
        metadata = row["metadata_json"] or {}
        title = (
            row["source_title"]
            or metadata.get("title")
            or metadata.get("professor_name")
            or row["category"]
        )
        similarity = cosine_distance_to_similarity(row["distance"])
        department_id = row["department_id"]
        category = str(row["category"])
        candidates.append(
            RecommendationCandidate(
                resource_type="career",
                resource_id=row["id"],
                title=str(title),
                subtitle=metadata.get("professor_name")
                or metadata.get("organization_name")
                or row["department_name"],
                department_id=department_id,
                department_name=row["department_name"],
                href=f"/careers?category={category}",
                similarity_score=similarity,
                adjusted_score=score_candidate(
                    similarity_score=similarity,
                    department_id=department_id,
                    preferred_department_id=preferred_department_id,
                ),
                tags=[str(tag) for tag in metadata.get("tags") or [category]][:6],
            )
        )
    return candidates


async def get_personalized_recommendations(
    db: AsyncSession,
    *,
    user_id: str,
    department_id: UUID | None = None,
    limit: int = DEFAULT_RECOMMENDATION_LIMIT,
) -> RecommendationResponse:
    recent_views = await load_recent_views(db, user_id=user_id)
    profile_vectors = await load_profile_vectors(db, recent_views)
    profile_vector = average_vectors(profile_vectors)
    viewed_keys = {
        (view.resource_type, view.resource_id)
        for view in recent_views
        if view.resource_type in {"course", "career"}
    }

    if not profile_vector:
        return RecommendationResponse(
            items=[],
            based_on_count=0,
            viewed_resource_count=len(recent_views),
            profile_ready=False,
        )

    lane_limit = max(limit * LANE_MULTIPLIER, 12)
    course_candidates = await recommend_courses(
        db,
        profile_vector=profile_vector,
        viewed_course_ids={
            resource_id
            for resource_type, resource_id in viewed_keys
            if resource_type == "course"
        },
        preferred_department_id=department_id,
        limit=lane_limit,
    )
    career_candidates = await recommend_career_chunks(
        db,
        profile_vector=profile_vector,
        viewed_career_ids={
            resource_id
            for resource_type, resource_id in viewed_keys
            if resource_type == "career"
        },
        preferred_department_id=department_id,
        limit=lane_limit,
    )
    items = merge_candidates(
        [*course_candidates, *career_candidates],
        viewed_keys=viewed_keys,
        preferred_department_id=department_id,
        limit=limit,
    )
    return RecommendationResponse(
        items=items,
        based_on_count=len(profile_vectors),
        viewed_resource_count=len(recent_views),
        profile_ready=True,
    )
