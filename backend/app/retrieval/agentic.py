"""Agentic RAG router and tool orchestration for `/api/chat`."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from os import getenv
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import case, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.ai.providers import embed_query, get_chat_model
from app.models import (
    Course,
    CourseReview,
    Department,
    LifeResource,
    LifeResourceType,
    LifeReview,
    LifeReviewType,
    ReviewModerationStatus,
)
from app.retrieval.hybrid import (
    extract_exact_terms,
    reciprocal_rank_fusion,
    resolve_department_filter,
    retrieve_lexical_chunks,
    retrieve_vector_chunks,
)
from app.retrieval.reranker import rerank_chunks
from app.retrieval.types import RetrievedChunk


logger = logging.getLogger(__name__)

ToolName = Literal["course", "career", "life"]
IntentName = Literal["course", "career", "life", "composite"]

NO_CONTEXT_ANSWER = (
    "我不知道。依照目前可用的資料庫與工具，沒有找到足夠且符合"
    "科系/分類條件的真實資料；我不會用猜測補答案。"
)

ROUTER_SYSTEM_PROMPT = """你是 NCKUall 的 RAG 意圖路由器。

請只根據使用者問題判斷應呼叫哪些資料工具，不要回答問題本身。

可用工具：
- course: 選課、課程名稱、課號、授課教師、學分、成績分佈、課程評價。
- career: 實驗室、教授研究領域、專題、推甄、預研、交換、留學、轉系、職涯計畫。
- life: 租屋、生活避雷、美食、備餐、周邊地點、生活評價。

輸出必須是 JSON，格式：
{"intent":"course|career|life|composite","tools":["course"],"reason":"一句話"}

如果問題需要跨資料域比較或組合建議，intent 使用 composite，tools 放入所有需要的工具。
"""

COURSE_KEYWORDS = (
    "選課",
    "課程",
    "課號",
    "課綱",
    "學分",
    "必修",
    "選修",
    "成績",
    "分佈",
    "a+",
    "不及格",
    "老師",
    "授課",
)
CAREER_KEYWORDS = (
    "實驗室",
    "教授",
    "專題",
    "研究",
    "推甄",
    "預研",
    "交換",
    "留學",
    "雙聯",
    "轉系",
    "職涯",
    "計畫",
)
LIFE_KEYWORDS = (
    "租屋",
    "房東",
    "宿舍",
    "美食",
    "餐",
    "備餐",
    "高蛋白",
    "生活",
    "避雷",
    "地點",
    "周邊",
)

LIFE_CATEGORY_ALIASES = {
    "租屋": {"rental", "rental_warning", "rental_recommendation"},
    "租屋避雷": {"rental_warning"},
    "美食": {"food", "food_recommendation"},
    "高蛋白備餐": {"protein_meal_prep"},
    "備餐": {"protein_meal_prep"},
}


@dataclass(frozen=True)
class IntentDecision:
    intent: IntentName
    tools: tuple[ToolName, ...]
    reason: str = ""


@dataclass(frozen=True)
class ToolRunResult:
    tool_name: ToolName
    chunks: list[RetrievedChunk]
    error: str | None = None


@dataclass(frozen=True)
class AgenticRetrievalResult:
    intent: IntentName
    used_tools: list[str]
    chunks: list[RetrievedChunk]
    tool_results: list[ToolRunResult]
    router_reason: str

    @property
    def tool_trace(self) -> str:
        if not self.tool_results:
            return "沒有工具被呼叫。"
        lines = [
            f"- {result.tool_name}: {len(result.chunks)} 筆"
            + (f"，錯誤：{result.error}" if result.error else "")
            for result in self.tool_results
        ]
        return "\n".join(lines)


class AgenticRAGTool:
    name: ToolName
    description: str

    async def ainvoke(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        user_query: str,
        department_filter: str,
        category_filter: str | None,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        raise NotImplementedError


def _normalize_tools(raw_tools: list[Any], fallback: ToolName) -> tuple[ToolName, ...]:
    tools: list[ToolName] = []
    for raw_tool in raw_tools:
        if raw_tool in {"course", "career", "life"} and raw_tool not in tools:
            tools.append(raw_tool)
    if not tools:
        tools.append(fallback)
    return tuple(tools)


def _heuristic_route(user_query: str, category_filter: str | None) -> IntentDecision:
    normalized = user_query.lower()
    if category_filter:
        normalized = f"{normalized} {category_filter.lower()}"

    scores: dict[ToolName, int] = {
        "course": sum(keyword in normalized for keyword in COURSE_KEYWORDS),
        "career": sum(keyword in normalized for keyword in CAREER_KEYWORDS),
        "life": sum(keyword in normalized for keyword in LIFE_KEYWORDS),
    }
    selected = [tool for tool, score in scores.items() if score > 0]
    if len(selected) > 1:
        return IntentDecision(
            intent="composite",
            tools=tuple(selected),
            reason="keyword_multi_domain",
        )
    if selected:
        tool = selected[0]
        return IntentDecision(
            intent=tool,
            tools=(tool,),
            reason="keyword_single_domain",
        )
    return IntentDecision(
        intent="composite",
        tools=("course", "career", "life"),
        reason="low_confidence_fallback_all_tools",
    )


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if not match:
        raise ValueError("router response did not contain a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("router response JSON was not an object")
    return parsed


async def route_intent(
    *,
    user_query: str,
    category_filter: str | None,
) -> IntentDecision:
    mode = getenv("RAG_AGENT_ROUTER_MODE", "llm").lower()
    fallback = _heuristic_route(user_query, category_filter)
    if mode in {"keyword", "heuristic", "rules"}:
        return fallback

    try:
        llm = get_chat_model()
        message = (
            f"使用者問題：{user_query}\n"
            f"前端 category_filter：{category_filter or '無'}"
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=message),
            ]
        )
        parsed = _extract_json_object(str(response.content))
        intent = parsed.get("intent")
        if intent not in {"course", "career", "life", "composite"}:
            return fallback
        tools = _normalize_tools(
            list(parsed.get("tools") or []),
            fallback=fallback.tools[0],
        )
        if intent != "composite" and len(tools) > 1:
            intent = "composite"
        return IntentDecision(
            intent=intent,
            tools=tools,
            reason=str(parsed.get("reason") or "llm_router"),
        )
    except Exception:
        logger.warning(
            "agentic_router_fell_back_to_heuristics",
            exc_info=True,
        )
        return fallback


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _query_terms(user_query: str) -> list[str]:
    terms = extract_exact_terms(user_query)
    stripped = user_query.strip()
    if 2 <= len(stripped) <= 80:
        terms.append(stripped)
    terms.extend(
        token
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", user_query)
        if token not in terms
    )

    normalized: list[str] = []
    for term in terms:
        value = term.strip()
        if 2 <= len(value) <= 80 and value not in normalized:
            normalized.append(value)
    return normalized[:8]


def _department_clause(department_filter: str):
    department_label, department_code = resolve_department_filter(
        department_filter,
    )
    return or_(
        Department.code == department_code,
        Department.name_zh.ilike(f"%{_escape_like(department_label)}%"),
    )


def _course_category_clause(category_filter: str | None):
    if not category_filter:
        return None
    normalized = category_filter.strip().lower()
    if normalized in {"必修", "required", "required_for_major"}:
        return Course.required_for_major.is_(True)
    if normalized in {"選修", "elective"}:
        return Course.required_for_major.is_(False)
    return None


def _weighted_like_score(weighted_conditions: list[tuple[Any, float]]):
    score = literal(0.0)
    for condition, weight in weighted_conditions:
        score = score + case((condition, weight), else_=0.0)
    return score


def _safe_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _metadata(**values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if hasattr(value, "value"):
            result[key] = value.value
        elif isinstance(value, list):
            result[key] = [str(item) for item in value]
        else:
            result[key] = str(value)
    return result


def _grade_summary(course: Course) -> str:
    distributions = sorted(
        course.grade_distributions,
        key=lambda item: (item.academic_year, item.semester),
        reverse=True,
    )[:3]
    if not distributions:
        return "目前沒有歷年成績分佈。"

    lines = []
    for distribution in distributions:
        lines.append(
            (
                f"{distribution.academic_year}-{distribution.semester}: "
                f"修課 {distribution.enrollment_count or 'N/A'} 人，"
                f"平均 {distribution.avg_score or 'N/A'}，"
                f"通過率 {distribution.pass_rate or 'N/A'}，"
                f"級距 {distribution.grade_buckets}"
            )
        )
    return "\n".join(lines)


def _course_chunk(
    course: Course,
    department: Department,
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    content = "\n".join(
        part
        for part in (
            f"課程名稱：{course.title_zh}",
            f"課號：{course.course_code}",
            f"授課教師：{course.instructor_name or 'N/A'}",
            f"學分：{course.credits or 'N/A'}",
            f"屬性：{'必修' if course.required_for_major else '選修'}",
            f"難度：{course.difficulty.value}",
            f"課程描述：{course.description or 'N/A'}",
            f"歷年成績：\n{_grade_summary(course)}",
        )
        if part
    )
    return RetrievedChunk(
        id=course.id,
        content=content,
        source_type="course",
        source_url=course.syllabus_url,
        source_title=f"{course.title_zh} ({course.course_code})",
        category="course_required" if course.required_for_major else "course_elective",
        chunk_index=0,
        metadata_json=_metadata(
            tool="course",
            course_id=course.id,
            course_code=course.course_code,
            instructor_name=course.instructor_name,
            credits=course.credits,
            difficulty=course.difficulty,
            tags=course.tags,
        ),
        department_code=department.code,
        department_name=department.name_zh,
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


def _course_review_chunk(
    review: CourseReview,
    course: Course,
    department: Department,
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    content = "\n".join(
        part
        for part in (
            f"課程：{course.title_zh} ({course.course_code})",
            f"授課教師：{course.instructor_name or 'N/A'}",
            f"評價內容：{review.content}",
            f"整體評分：{review.overall_rating or 'N/A'}",
            f"負荷：{review.workload_rating or 'N/A'}",
            f"難度：{review.difficulty_rating or 'N/A'}",
            f"評分公平性：{review.grading_fairness_rating or 'N/A'}",
            f"標籤：{', '.join(review.tags)}",
        )
        if part
    )
    return RetrievedChunk(
        id=review.id,
        content=content,
        source_type="course_review",
        source_url=course.syllabus_url,
        source_title=f"{course.title_zh} 課程評價",
        category="course_review",
        chunk_index=0,
        metadata_json=_metadata(
            tool="course",
            course_id=course.id,
            course_code=course.course_code,
            instructor_name=course.instructor_name,
            overall_rating=review.overall_rating,
            workload_rating=review.workload_rating,
            difficulty_rating=review.difficulty_rating,
            tags=review.tags,
        ),
        department_code=department.code,
        department_name=department.name_zh,
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


class CourseTool(AgenticRAGTool):
    name: ToolName = "course"
    description = "查詢課程、課號、授課教師、學分、成績分佈與課程評價。"

    async def ainvoke(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        user_query: str,
        department_filter: str,
        category_filter: str | None,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        async with session_factory() as db:
            vector_chunks = (
                await self._retrieve_vector(
                    db,
                    query_embedding=query_embedding,
                    department_filter=department_filter,
                    category_filter=category_filter,
                    limit=limit,
                )
                if query_embedding
                else []
            )
            lexical_chunks = await self._retrieve_lexical(
                db,
                user_query=user_query,
                department_filter=department_filter,
                category_filter=category_filter,
                limit=limit,
            )
        return reciprocal_rank_fusion(
            vector_chunks,
            lexical_chunks,
            limit=limit,
        )

    async def _retrieve_vector(
        self,
        db: AsyncSession,
        *,
        query_embedding: list[float],
        department_filter: str,
        category_filter: str | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        category_clause = _course_category_clause(category_filter)
        course_distance = Course.embedding.cosine_distance(
            query_embedding,
        ).label("vector_distance")
        course_stmt = (
            select(Course, Department, course_distance)
            .join(Department, Course.department_id == Department.id)
            .options(selectinload(Course.grade_distributions))
            .where(Course.embedding.is_not(None))
            .where(_department_clause(department_filter))
            .order_by(course_distance)
            .limit(limit)
        )
        if category_clause is not None:
            course_stmt = course_stmt.where(category_clause)

        review_distance = CourseReview.embedding.cosine_distance(
            query_embedding,
        ).label("vector_distance")
        review_stmt = (
            select(CourseReview, Course, Department, review_distance)
            .join(Course, CourseReview.course_id == Course.id)
            .join(Department, Course.department_id == Department.id)
            .where(CourseReview.embedding.is_not(None))
            .where(_department_clause(department_filter))
            .order_by(review_distance)
            .limit(limit)
        )
        if category_clause is not None:
            review_stmt = review_stmt.where(category_clause)

        course_rows = (await db.execute(course_stmt)).all()
        review_rows = (await db.execute(review_stmt)).all()
        chunks = [
            _course_chunk(
                course,
                department,
                vector_distance=float(distance),
            )
            for course, department, distance in course_rows
        ]
        chunks.extend(
            _course_review_chunk(
                review,
                course,
                department,
                vector_distance=float(distance),
            )
            for review, course, department, distance in review_rows
        )
        return sorted(
            chunks,
            key=lambda chunk: chunk.vector_distance or 999,
        )[:limit]

    async def _retrieve_lexical(
        self,
        db: AsyncSession,
        *,
        user_query: str,
        department_filter: str,
        category_filter: str | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        terms = _query_terms(user_query)
        if not terms:
            return []
        weighted_course_conditions = []
        weighted_review_conditions = []
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            weighted_course_conditions.extend(
                [
                    (Course.title_zh.ilike(pattern, escape="\\"), 1.4),
                    (Course.course_code.ilike(pattern, escape="\\"), 1.3),
                    (Course.instructor_name.ilike(pattern, escape="\\"), 1.0),
                    (Course.description.ilike(pattern, escape="\\"), 0.5),
                ]
            )
            weighted_review_conditions.extend(
                [
                    (Course.title_zh.ilike(pattern, escape="\\"), 1.1),
                    (Course.instructor_name.ilike(pattern, escape="\\"), 0.8),
                    (CourseReview.content.ilike(pattern, escape="\\"), 0.9),
                ]
            )

        category_clause = _course_category_clause(category_filter)
        course_score = _weighted_like_score(
            weighted_course_conditions,
        ).label("lexical_score")
        course_stmt = (
            select(Course, Department, course_score)
            .join(Department, Course.department_id == Department.id)
            .options(selectinload(Course.grade_distributions))
            .where(or_(*(condition for condition, _ in weighted_course_conditions)))
            .where(_department_clause(department_filter))
            .order_by(course_score.desc())
            .limit(limit)
        )
        if category_clause is not None:
            course_stmt = course_stmt.where(category_clause)

        review_score = _weighted_like_score(
            weighted_review_conditions,
        ).label("lexical_score")
        review_stmt = (
            select(CourseReview, Course, Department, review_score)
            .join(Course, CourseReview.course_id == Course.id)
            .join(Department, Course.department_id == Department.id)
            .where(or_(*(condition for condition, _ in weighted_review_conditions)))
            .where(_department_clause(department_filter))
            .order_by(review_score.desc())
            .limit(limit)
        )
        if category_clause is not None:
            review_stmt = review_stmt.where(category_clause)

        course_rows = (await db.execute(course_stmt)).all()
        review_rows = (await db.execute(review_stmt)).all()
        chunks = [
            _course_chunk(
                course,
                department,
                lexical_score=float(score),
            )
            for course, department, score in course_rows
        ]
        chunks.extend(
            _course_review_chunk(
                review,
                course,
                department,
                lexical_score=float(score),
            )
            for review, course, department, score in review_rows
        )
        return sorted(
            chunks,
            key=lambda chunk: chunk.lexical_score or 0,
            reverse=True,
        )[:limit]


class CareerTool(AgenticRAGTool):
    name: ToolName = "career"
    description = "查詢實驗室、教授、專題、交換、留學、預研、推甄與職涯資源。"

    async def ainvoke(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        user_query: str,
        department_filter: str,
        category_filter: str | None,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        async with session_factory() as db:
            vector_chunks = (
                await retrieve_vector_chunks(
                    db,
                    query_embedding=query_embedding,
                    department_filter=department_filter,
                    category_filter=category_filter,
                    limit=limit,
                )
                if query_embedding
                else []
            )
            lexical_chunks = await retrieve_lexical_chunks(
                db,
                user_query=user_query,
                department_filter=department_filter,
                category_filter=category_filter,
                limit=limit,
            )
        return reciprocal_rank_fusion(
            vector_chunks,
            lexical_chunks,
            limit=limit,
        )


LIFE_RESOURCE_VALUES = {item.value for item in LifeResourceType}
LIFE_REVIEW_VALUES = {item.value for item in LifeReviewType}


def _life_category_values(category_filter: str | None) -> tuple[set[str], set[str]]:
    if not category_filter:
        return set(), set()
    normalized = category_filter.strip()
    values = LIFE_CATEGORY_ALIASES.get(normalized, {normalized})
    return (
        {value for value in values if value in LIFE_RESOURCE_VALUES},
        {value for value in values if value in LIFE_REVIEW_VALUES},
    )


def _life_resource_chunk(
    resource: LifeResource,
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    content = "\n".join(
        part
        for part in (
            f"生活資源：{resource.name}",
            f"類型：{resource.resource_type.value}",
            f"區域：{resource.area or 'N/A'}",
            f"地址：{resource.address or 'N/A'}",
            f"描述：{resource.description or 'N/A'}",
            f"價格：{resource.price_min or 'N/A'} - {resource.price_max or 'N/A'}",
            f"評分：{resource.rating or 'N/A'}",
            f"標籤：{', '.join(resource.tags)}",
        )
        if part
    )
    return RetrievedChunk(
        id=resource.id,
        content=content,
        source_type="life_resource",
        source_url=resource.external_url,
        source_title=resource.name,
        category=resource.resource_type.value,
        chunk_index=0,
        metadata_json=_metadata(
            tool="life",
            resource_type=resource.resource_type,
            area=resource.area,
            rating=resource.rating,
            tags=resource.tags,
        ),
        department_code=None,
        department_name="全校通用",
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


def _life_review_chunk(
    review: LifeReview,
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    content = "\n".join(
        part
        for part in (
            f"生活評價：{review.title}",
            f"類型：{review.review_type.value}",
            f"地點：{review.location_name or 'N/A'}",
            f"區域：{review.area or 'N/A'}",
            f"地址：{review.address or 'N/A'}",
            f"內容：{review.content}",
            f"評分：{review.rating or 'N/A'}",
            f"價格等級：{review.price_level or 'N/A'}",
            f"標籤：{', '.join(review.tags)}",
        )
        if part
    )
    return RetrievedChunk(
        id=review.id,
        content=content,
        source_type="life_review",
        source_url=None,
        source_title=review.title,
        category=review.review_type.value,
        chunk_index=0,
        metadata_json=_metadata(
            tool="life",
            review_type=review.review_type,
            location_name=review.location_name,
            area=review.area,
            rating=review.rating,
            price_level=review.price_level,
            tags=review.tags,
        ),
        department_code=None,
        department_name="全校通用",
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


class LifeTool(AgenticRAGTool):
    name: ToolName = "life"
    description = "查詢租屋避雷、美食、高蛋白備餐與校園生活評價。"

    async def ainvoke(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        user_query: str,
        department_filter: str,
        category_filter: str | None,
        query_embedding: list[float] | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        async with session_factory() as db:
            vector_chunks = (
                await self._retrieve_vector(
                    db,
                    query_embedding=query_embedding,
                    category_filter=category_filter,
                    limit=limit,
                )
                if query_embedding
                else []
            )
            lexical_chunks = await self._retrieve_lexical(
                db,
                user_query=user_query,
                category_filter=category_filter,
                limit=limit,
            )
        return reciprocal_rank_fusion(
            vector_chunks,
            lexical_chunks,
            limit=limit,
        )

    async def _retrieve_vector(
        self,
        db: AsyncSession,
        *,
        query_embedding: list[float],
        category_filter: str | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        resource_values, review_values = _life_category_values(category_filter)
        resource_distance = LifeResource.embedding.cosine_distance(
            query_embedding,
        ).label("vector_distance")
        resource_stmt = (
            select(LifeResource, resource_distance)
            .where(LifeResource.embedding.is_not(None))
            .order_by(resource_distance)
            .limit(limit)
        )
        if resource_values:
            resource_stmt = resource_stmt.where(
                LifeResource.resource_type.in_(resource_values),
            )

        review_distance = LifeReview.embedding.cosine_distance(
            query_embedding,
        ).label("vector_distance")
        review_stmt = (
            select(LifeReview, review_distance)
            .where(LifeReview.embedding.is_not(None))
            .where(LifeReview.moderation_status != ReviewModerationStatus.HIDDEN)
            .order_by(review_distance)
            .limit(limit)
        )
        if review_values:
            review_stmt = review_stmt.where(
                LifeReview.review_type.in_(review_values),
            )

        resource_rows = (await db.execute(resource_stmt)).all()
        review_rows = (await db.execute(review_stmt)).all()
        chunks = [
            _life_resource_chunk(
                resource,
                vector_distance=float(distance),
            )
            for resource, distance in resource_rows
        ]
        chunks.extend(
            _life_review_chunk(
                review,
                vector_distance=float(distance),
            )
            for review, distance in review_rows
        )
        return sorted(
            chunks,
            key=lambda chunk: chunk.vector_distance or 999,
        )[:limit]

    async def _retrieve_lexical(
        self,
        db: AsyncSession,
        *,
        user_query: str,
        category_filter: str | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        terms = _query_terms(user_query)
        if not terms:
            return []
        resource_conditions = []
        review_conditions = []
        for term in terms:
            pattern = f"%{_escape_like(term)}%"
            resource_conditions.extend(
                [
                    (LifeResource.name.ilike(pattern, escape="\\"), 1.2),
                    (LifeResource.area.ilike(pattern, escape="\\"), 1.0),
                    (LifeResource.address.ilike(pattern, escape="\\"), 0.8),
                    (LifeResource.description.ilike(pattern, escape="\\"), 0.7),
                ]
            )
            review_conditions.extend(
                [
                    (LifeReview.title.ilike(pattern, escape="\\"), 1.2),
                    (LifeReview.location_name.ilike(pattern, escape="\\"), 1.0),
                    (LifeReview.area.ilike(pattern, escape="\\"), 0.9),
                    (LifeReview.content.ilike(pattern, escape="\\"), 0.8),
                ]
            )

        resource_values, review_values = _life_category_values(category_filter)
        resource_score = _weighted_like_score(
            resource_conditions,
        ).label("lexical_score")
        resource_stmt = (
            select(LifeResource, resource_score)
            .where(or_(*(condition for condition, _ in resource_conditions)))
            .order_by(resource_score.desc())
            .limit(limit)
        )
        if resource_values:
            resource_stmt = resource_stmt.where(
                LifeResource.resource_type.in_(resource_values),
            )

        review_score = _weighted_like_score(
            review_conditions,
        ).label("lexical_score")
        review_stmt = (
            select(LifeReview, review_score)
            .where(or_(*(condition for condition, _ in review_conditions)))
            .where(LifeReview.moderation_status != ReviewModerationStatus.HIDDEN)
            .order_by(review_score.desc())
            .limit(limit)
        )
        if review_values:
            review_stmt = review_stmt.where(
                LifeReview.review_type.in_(review_values),
            )

        resource_rows = (await db.execute(resource_stmt)).all()
        review_rows = (await db.execute(review_stmt)).all()
        chunks = [
            _life_resource_chunk(
                resource,
                lexical_score=float(score),
            )
            for resource, score in resource_rows
        ]
        chunks.extend(
            _life_review_chunk(
                review,
                lexical_score=float(score),
            )
            for review, score in review_rows
        )
        return sorted(
            chunks,
            key=lambda chunk: chunk.lexical_score or 0,
            reverse=True,
        )[:limit]


TOOLS: dict[ToolName, AgenticRAGTool] = {
    "course": CourseTool(),
    "career": CareerTool(),
    "life": LifeTool(),
}


def _deduplicate_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    seen: set[tuple[str, str, int]] = set()
    unique: list[RetrievedChunk] = []
    for chunk in chunks:
        key = (chunk.source_type, str(chunk.id), chunk.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


async def _safe_tool_call(
    tool: AgenticRAGTool,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    user_query: str,
    department_filter: str,
    category_filter: str | None,
    query_embedding: list[float] | None,
    limit: int,
) -> ToolRunResult:
    try:
        chunks = await tool.ainvoke(
            session_factory=session_factory,
            user_query=user_query,
            department_filter=department_filter,
            category_filter=category_filter,
            query_embedding=query_embedding,
            limit=limit,
        )
        return ToolRunResult(tool_name=tool.name, chunks=chunks)
    except Exception as exc:
        logger.exception(
            "agentic_rag_tool_failed",
            extra={"tool": tool.name},
        )
        return ToolRunResult(
            tool_name=tool.name,
            chunks=[],
            error=exc.__class__.__name__,
        )


async def retrieve_agentic_context(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    user_query: str,
    department_filter: str,
    category_filter: str | None,
) -> AgenticRetrievalResult:
    router_task = asyncio.create_task(
        route_intent(
            user_query=user_query,
            category_filter=category_filter,
        )
    )
    embedding_task = asyncio.create_task(embed_query(user_query))

    decision = await router_task
    query_embedding: list[float] | None = None
    try:
        query_embedding = await embedding_task
    except Exception:
        logger.warning(
            "agentic_rag_embedding_failed_falling_back_to_lexical",
            exc_info=True,
        )

    lane_limit = int(getenv("RAG_RETRIEVAL_LANE_LIMIT", "30"))
    candidate_limit = int(getenv("RAG_RRF_CANDIDATE_LIMIT", "20"))
    context_limit = int(getenv("RAG_CONTEXT_LIMIT", "4"))
    selected_tools = [TOOLS[name] for name in decision.tools if name in TOOLS]
    if not selected_tools:
        selected_tools = [TOOLS["course"], TOOLS["career"], TOOLS["life"]]

    tool_results = await asyncio.gather(
        *[
            _safe_tool_call(
                tool,
                session_factory=session_factory,
                user_query=user_query,
                department_filter=department_filter,
                category_filter=category_filter,
                query_embedding=query_embedding,
                limit=lane_limit,
            )
            for tool in selected_tools
        ]
    )
    if (
        not any(result.chunks for result in tool_results)
        and len(selected_tools) < len(TOOLS)
    ):
        rescue_tools = [
            tool
            for name, tool in TOOLS.items()
            if tool not in selected_tools
        ]
        logger.info(
            "agentic_rag_rescue_retrieval_started",
            extra={
                "intent": decision.intent,
                "initial_tools": [tool.name for tool in selected_tools],
                "rescue_tools": [tool.name for tool in rescue_tools],
            },
        )
        rescue_results = await asyncio.gather(
            *[
                _safe_tool_call(
                    tool,
                    session_factory=session_factory,
                    user_query=user_query,
                    department_filter=department_filter,
                    category_filter=category_filter,
                    query_embedding=query_embedding,
                    limit=lane_limit,
                )
                for tool in rescue_tools
            ]
        )
        selected_tools.extend(rescue_tools)
        tool_results = [*tool_results, *rescue_results]

    candidates = _deduplicate_chunks(
        [
            chunk
            for result in tool_results
            for chunk in result.chunks
        ]
    )[:candidate_limit]
    chunks = (
        await rerank_chunks(
            user_query,
            candidates,
            limit=context_limit,
        )
        if candidates
        else []
    )
    logger.info(
        "agentic_rag_retrieval_completed",
        extra={
            "intent": decision.intent,
            "tools": [tool.name for tool in selected_tools],
            "candidate_count": len(candidates),
            "context_count": len(chunks),
            "router_reason": decision.reason,
        },
    )
    return AgenticRetrievalResult(
        intent=decision.intent,
        used_tools=[tool.name for tool in selected_tools],
        chunks=chunks,
        tool_results=tool_results,
        router_reason=decision.reason,
    )
