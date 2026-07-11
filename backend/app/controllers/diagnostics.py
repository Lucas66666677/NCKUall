from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from os import getenv
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers import embed_query, get_chat_model
from app.auth import AuthUser
from app.models import (
    CareerDocumentChunk,
    CareerResource,
    CareerResourceType,
    Course,
    Department,
    DiagnosisResult,
    GraduationRequirement,
)
from app.retrieval.hybrid import (
    reciprocal_rank_fusion,
    retrieve_lexical_chunks,
    retrieve_vector_chunks,
)
from app.retrieval.types import RetrievedChunk
from app.schemas import (
    CompletedCourseInput,
    CreditBucketProgress,
    CreditDiagnosisStats,
    DiagnosisCareerResourceContext,
    DiagnosisRequest,
    DiagnosisResponse,
    GeneralEducationAreaProgress,
)


logger = logging.getLogger(__name__)

ZERO = Decimal("0")
ONE = Decimal("1")
PASSING_TEXT_GRADES = {"P", "PASS", "S", "通過", "及格"}
FAILING_TEXT_GRADES = {
    "F",
    "FAIL",
    "NP",
    "N",
    "W",
    "WF",
    "I",
    "X",
    "不通過",
    "未通過",
    "不及格",
    "棄選",
    "停修",
}
VALID_CATEGORIES = {
    "major_required",
    "major_elective",
    "general_education",
    "free_elective",
    "other",
}


class DiagnosisGenerationError(RuntimeError):
    """Raised when the LLM cannot produce the markdown diagnosis report."""


def diagnosis_owner_id(user: AuthUser) -> str:
    if user.user_id:
        return user.user_id
    return hashlib.sha256(user.email.encode("utf-8")).hexdigest()


def diagnosis_email_hash(user: AuthUser) -> str:
    return hashlib.sha256(user.email.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClassifiedCompletedCourse:
    course_code: str
    title: str | None
    credits: Decimal
    category: str
    general_education_area: str | None


def normalize_course_code(course_code: str) -> str:
    return course_code.strip().replace(" ", "").replace("_", "-").upper()


def to_decimal(value: Any, *, default: Decimal = ZERO) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def rate(earned: Decimal, required: Decimal) -> float:
    if required <= ZERO:
        return 1.0
    return round(float(min(earned / required, ONE)), 4)


def remaining(required: Decimal, earned: Decimal) -> Decimal:
    return max(required - earned, ZERO)


def is_passing_grade(grade: str | None) -> bool:
    if grade is None or not grade.strip():
        return True

    normalized = grade.strip().upper()
    if normalized in PASSING_TEXT_GRADES:
        return True
    if normalized in FAILING_TEXT_GRADES or normalized.startswith("F"):
        return False

    try:
        return Decimal(normalized) >= Decimal("60")
    except InvalidOperation:
        # Letter grades such as A+, B-, C are passing unless explicitly failed.
        return True


def normalize_string_set(values: Iterable[Any] | None) -> set[str]:
    if values is None:
        return set()
    return {
        normalize_course_code(str(value))
        for value in values
        if str(value).strip()
    }


def normalize_course_category(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    aliases = {
        "必修": "major_required",
        "專業必修": "major_required",
        "系必修": "major_required",
        "選修": "major_elective",
        "專業選修": "major_elective",
        "系選修": "major_elective",
        "通識": "general_education",
        "自由選修": "free_elective",
    }
    normalized = aliases.get(value.strip(), value.strip())
    return normalized if normalized in VALID_CATEGORIES else None


def normalize_general_area_requirements(raw_value: Any) -> dict[str, Decimal]:
    """
    Accept multiple department-authored JSON shapes for GE dimensions.

    Supported examples:
    - ["人文", "社會", "自然", "跨域"]
    - {"人文": 2, "社會": 2}
    - [{"area": "人文", "required_credits": 2}]
    """

    if isinstance(raw_value, dict):
        return {
            str(area): to_decimal(required, default=ONE)
            for area, required in raw_value.items()
            if str(area).strip()
        }

    if isinstance(raw_value, list):
        requirements: dict[str, Decimal] = {}
        for item in raw_value:
            if isinstance(item, dict):
                area = (
                    item.get("area")
                    or item.get("name")
                    or item.get("key")
                    or item.get("label")
                )
                if area:
                    requirements[str(area)] = to_decimal(
                        item.get("required_credits")
                        or item.get("credits")
                        or item.get("required"),
                        default=ONE,
                    )
            elif str(item).strip():
                requirements[str(item)] = ONE
        return requirements

    return {}


def general_education_course_map(rules_json: dict[str, Any]) -> dict[str, str]:
    raw_mapping = rules_json.get("general_education_courses") or {}
    mapping: dict[str, str] = {}
    if not isinstance(raw_mapping, dict):
        return mapping

    for area, course_codes in raw_mapping.items():
        for code in normalize_string_set(course_codes):
            mapping[code] = str(area)
    return mapping


def category_rule_map(rules_json: dict[str, Any]) -> dict[str, str]:
    raw_mapping = rules_json.get("course_categories") or {}
    if not isinstance(raw_mapping, dict):
        return {}

    mapping: dict[str, str] = {}
    for raw_code, raw_category in raw_mapping.items():
        category = normalize_course_category(raw_category)
        if category:
            mapping[normalize_course_code(str(raw_code))] = category
    return mapping


def build_course_catalog(courses: list[Course], department_id: UUID) -> dict[str, Course]:
    catalog: dict[str, Course] = {}
    for course in courses:
        code = normalize_course_code(course.course_code)
        existing = catalog.get(code)
        if existing is None or course.department_id == department_id:
            catalog[code] = course
    return catalog


def classify_completed_course(
    item: CompletedCourseInput,
    *,
    department_id: UUID,
    catalog: dict[str, Course],
    category_rules: dict[str, str],
    required_codes: set[str],
    major_elective_codes: set[str],
    ge_course_areas: dict[str, str],
) -> ClassifiedCompletedCourse:
    code = normalize_course_code(item.course_code)
    course = catalog.get(code)
    credits = to_decimal(item.credits)
    if credits <= ZERO and course is not None:
        credits = to_decimal(course.credits)

    explicit_category = normalize_course_category(item.category)
    category = (
        explicit_category
        or category_rules.get(code)
        or (
            "major_required"
            if code in required_codes
            else None
        )
        or (
            "major_elective"
            if code in major_elective_codes
            else None
        )
        or (
            "general_education"
            if code in ge_course_areas
            else None
        )
    )

    if category is None and course is not None and course.department_id == department_id:
        category = "major_required" if course.required_for_major else "major_elective"
    if category is None:
        category = "free_elective"

    general_area = item.general_education_area or ge_course_areas.get(code)
    return ClassifiedCompletedCourse(
        course_code=code,
        title=item.title or (course.title_zh if course else None),
        credits=credits,
        category=category,
        general_education_area=general_area,
    )


def calculate_credit_statistics(
    *,
    payload: DiagnosisRequest,
    department: Department,
    requirement: GraduationRequirement,
    department_courses: list[Course],
) -> CreditDiagnosisStats:
    rules_json = requirement.rules_json or {}
    catalog = build_course_catalog(department_courses, payload.department_id)
    category_rules = category_rule_map(rules_json)
    required_codes = normalize_string_set(rules_json.get("required_course_codes"))
    major_elective_codes = normalize_string_set(
        rules_json.get("major_elective_course_codes")
    )
    ge_course_areas = general_education_course_map(rules_json)

    required_codes.update(
        normalize_course_code(course.course_code)
        for course in department_courses
        if course.department_id == payload.department_id
        and course.required_for_major
    )

    earned_total = ZERO
    earned_required = ZERO
    earned_major_elective = ZERO
    earned_general = ZERO
    earned_free = ZERO
    earned_by_ge_area: dict[str, Decimal] = {}
    passed_codes: set[str] = set()
    ignored_count = 0

    for item in payload.completed_courses:
        code = normalize_course_code(item.course_code)
        if code in passed_codes or not is_passing_grade(item.grade):
            ignored_count += 1
            continue

        classified = classify_completed_course(
            item,
            department_id=payload.department_id,
            catalog=catalog,
            category_rules=category_rules,
            required_codes=required_codes,
            major_elective_codes=major_elective_codes,
            ge_course_areas=ge_course_areas,
        )
        if classified.credits <= ZERO:
            ignored_count += 1
            continue

        passed_codes.add(code)
        earned_total += classified.credits

        if classified.category == "major_required":
            earned_required += classified.credits
        elif classified.category == "major_elective":
            earned_major_elective += classified.credits
        elif classified.category == "general_education":
            earned_general += classified.credits
            area = classified.general_education_area or "未分類通識"
            earned_by_ge_area[area] = (
                earned_by_ge_area.get(area, ZERO) + classified.credits
            )
        else:
            earned_free += classified.credits

    total_required = to_decimal(requirement.total_required_credits)
    required_major_required = to_decimal(requirement.major_required_credits)
    required_major_elective = to_decimal(requirement.major_elective_credits)
    required_general = to_decimal(requirement.general_education_credits)
    missing_required_codes = sorted(required_codes - passed_codes)

    recommended_codes = missing_required_codes[:8]
    if len(recommended_codes) < 8:
        recommended_codes.extend(
            normalize_course_code(course.course_code)
            for course in department_courses
            if course.department_id == payload.department_id
            and not course.required_for_major
            and normalize_course_code(course.course_code) not in passed_codes
        )
    recommended_codes = list(dict.fromkeys(recommended_codes))[:8]

    buckets = [
        CreditBucketProgress(
            bucket="total",
            label="畢業總學分",
            required_credits=total_required,
            earned_credits=earned_total,
            remaining_credits=remaining(total_required, earned_total),
            completion_rate=rate(earned_total, total_required),
        ),
        CreditBucketProgress(
            bucket="major_required",
            label="專業必修",
            required_credits=required_major_required,
            earned_credits=earned_required,
            remaining_credits=remaining(required_major_required, earned_required),
            completion_rate=rate(earned_required, required_major_required),
        ),
        CreditBucketProgress(
            bucket="major_elective",
            label="系選修",
            required_credits=required_major_elective,
            earned_credits=earned_major_elective,
            remaining_credits=remaining(
                required_major_elective,
                earned_major_elective,
            ),
            completion_rate=rate(earned_major_elective, required_major_elective),
        ),
        CreditBucketProgress(
            bucket="general_education",
            label="通識學分",
            required_credits=required_general,
            earned_credits=earned_general,
            remaining_credits=remaining(required_general, earned_general),
            completion_rate=rate(earned_general, required_general),
        ),
        CreditBucketProgress(
            bucket="free_elective",
            label="自由選修與其他",
            required_credits=ZERO,
            earned_credits=earned_free,
            remaining_credits=ZERO,
            completion_rate=1.0,
        ),
    ]

    area_requirements = normalize_general_area_requirements(
        requirement.general_education_areas
    )
    general_area_progress = [
        GeneralEducationAreaProgress(
            area=area,
            required_credits=required,
            earned_credits=earned_by_ge_area.get(area, ZERO),
            is_satisfied=earned_by_ge_area.get(area, ZERO) >= required,
        )
        for area, required in area_requirements.items()
    ]

    return CreditDiagnosisStats(
        department_id=payload.department_id,
        department_name=department.name_zh,
        current_semester=payload.current_semester,
        curriculum_year=requirement.curriculum_year,
        total_required_credits=total_required,
        total_earned_credits=earned_total,
        overall_completion_rate=rate(earned_total, total_required),
        passed_course_count=len(passed_codes),
        ignored_course_count=ignored_count,
        buckets=buckets,
        general_education_areas=general_area_progress,
        missing_required_course_codes=missing_required_codes,
        recommended_course_codes=recommended_codes,
    )


async def load_diagnosis_inputs(
    db: AsyncSession,
    payload: DiagnosisRequest,
) -> tuple[Department, GraduationRequirement, list[Course]]:
    department = await db.get(Department, payload.department_id)
    if department is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到指定科系",
        )

    requirement = await db.scalar(
        select(GraduationRequirement)
        .where(
            GraduationRequirement.department_id == payload.department_id,
            GraduationRequirement.is_active.is_(True),
        )
        .order_by(GraduationRequirement.curriculum_year.desc().nullslast())
        .limit(1)
    )
    if requirement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="尚未設定該科系畢業學分規定",
        )

    completed_codes = [
        normalize_course_code(item.course_code)
        for item in payload.completed_courses
    ]
    courses = list(
        (
            await db.scalars(
                select(Course)
                .where(
                    or_(
                        Course.department_id == payload.department_id,
                        Course.course_code.in_(completed_codes),
                    )
                )
                .order_by(
                    Course.required_for_major.desc(),
                    Course.course_code,
                )
            )
        ).all()
    )
    return department, requirement, courses


def career_resource_to_context(
    resource: CareerResource,
) -> DiagnosisCareerResourceContext:
    return DiagnosisCareerResourceContext(
        title=resource.title,
        resource_type=resource.resource_type.value,
        professor_name=resource.professor_name,
        organization_name=resource.organization_name,
        summary=resource.summary,
        requirements=resource.requirements,
        official_url=resource.official_url or resource.source_url,
        tags=resource.tags or [],
    )


def chunk_to_context(chunk: RetrievedChunk | CareerDocumentChunk) -> DiagnosisCareerResourceContext:
    metadata = chunk.metadata_json or {}
    if isinstance(chunk, RetrievedChunk):
        return DiagnosisCareerResourceContext(
            title=chunk.source_title or metadata.get("title") or chunk.category,
            resource_type=chunk.category,
            professor_name=metadata.get("professor_name"),
            organization_name=metadata.get("organization_name"),
            summary=chunk.content[:700],
            requirements=metadata.get("requirements"),
            official_url=chunk.source_url,
            tags=metadata.get("tags") or [],
        )

    return DiagnosisCareerResourceContext(
        title=chunk.source_title or chunk.category,
        resource_type=chunk.category,
        professor_name=metadata.get("professor_name"),
        organization_name=metadata.get("organization_name"),
        summary=chunk.content[:700],
        requirements=metadata.get("requirements"),
        official_url=chunk.source_url,
        tags=metadata.get("tags") or [],
    )


async def load_structured_career_context(
    db: AsyncSession,
    *,
    department_id: UUID,
    limit: int = 8,
) -> list[DiagnosisCareerResourceContext]:
    rows = list(
        (
            await db.scalars(
                select(CareerResource)
                .where(
                    CareerResource.department_id == department_id,
                    CareerResource.resource_type.in_(
                        [
                            CareerResourceType.LAB_REVIEW,
                            CareerResourceType.EXCHANGE,
                            CareerResourceType.STUDY_ABROAD,
                        ]
                    ),
                )
                .order_by(
                    CareerResource.resource_type,
                    CareerResource.updated_at.desc(),
                )
                .limit(limit)
            )
        ).all()
    )
    return [career_resource_to_context(resource) for resource in rows]


async def retrieve_career_rag_context(
    db: AsyncSession,
    *,
    department_name: str,
    limit: int = 6,
) -> list[DiagnosisCareerResourceContext]:
    query = (
        f"{department_name} 熱門專題 實驗室 研究方向 海外交換 "
        "交換計畫 申請門檻 GPA 語言成績"
    )
    lexical_task = asyncio.create_task(
        retrieve_lexical_chunks(
            db,
            user_query=query,
            department_filter=department_name,
            category_filter=None,
            limit=limit * 2,
        )
    )

    vector_chunks: list[RetrievedChunk] = []
    lexical_chunks: list[RetrievedChunk] = []
    if getenv("DIAGNOSIS_VECTOR_RAG_ENABLED", "true").lower() == "true":
        embedding_task = asyncio.create_task(embed_query(query))
        try:
            query_embedding, lexical_chunks = await asyncio.gather(
                embedding_task,
                lexical_task,
            )
            vector_chunks = await retrieve_vector_chunks(
                db,
                query_embedding=query_embedding,
                department_filter=department_name,
                category_filter=None,
                limit=limit * 2,
            )
        except Exception:
            logger.warning("diagnosis_vector_retrieval_failed", exc_info=True)
            if not embedding_task.done():
                embedding_task.cancel()
                await asyncio.gather(
                    embedding_task,
                    return_exceptions=True,
                )
            lexical_chunks = await _await_lexical_fallback(lexical_task)
    else:
        lexical_chunks = await _await_lexical_fallback(lexical_task)

    fused_chunks = reciprocal_rank_fusion(
        vector_chunks,
        lexical_chunks,
        limit=limit,
    )
    return [chunk_to_context(chunk) for chunk in fused_chunks]


async def _await_lexical_fallback(
    lexical_task: asyncio.Task[list[RetrievedChunk]],
) -> list[RetrievedChunk]:
    try:
        return await lexical_task
    except asyncio.CancelledError:
        return []
    except Exception:
        logger.warning("diagnosis_lexical_retrieval_failed", exc_info=True)
        return []


def dedupe_career_context(
    values: list[DiagnosisCareerResourceContext],
) -> list[DiagnosisCareerResourceContext]:
    seen: set[tuple[str, str | None]] = set()
    deduped: list[DiagnosisCareerResourceContext] = []
    for item in values:
        key = (item.title, item.professor_name or item.official_url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def build_llm_context(
    *,
    stats: CreditDiagnosisStats,
    requirement: GraduationRequirement,
    career_context: list[DiagnosisCareerResourceContext],
) -> str:
    payload = {
        "credit_statistics": stats.model_dump(mode="json"),
        "graduation_requirement": {
            "curriculum_year": requirement.curriculum_year,
            "source_url": requirement.source_url,
            "general_education_areas": requirement.general_education_areas,
            "rules_json": requirement.rules_json,
        },
        "career_context": [
            item.model_dump(mode="json") for item in career_context
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


async def generate_diagnosis_report(
    *,
    context: str,
    current_semester: str,
) -> str:
    system_prompt = """你是 NCKUall 的成大生涯診斷顧問。

請只根據提供的 JSON Context 生成 Markdown 報告，不可捏造不存在的課程、教授、實驗室或交換學校。
如果 Context 沒有足夠資料，請明確寫「目前資料庫尚未收錄」並提出下一步查證建議。
語氣要專業、鼓勵、具體，避免製造焦慮。"""
    user_prompt = f"""請為目前學期為「{current_semester}」的學生生成「成大生涯全方位診斷報告」。

報告必須包含：
1. 一段 3 句內總結。
2. 選課缺口提醒，包含尚缺學分與缺口優先級。
3. 推薦補齊的通識領域。
4. 適合探索的教授實驗室或研究方向。
5. 建議申請或關注的海外交換學校/計畫；若資料不足請誠實說明。
6. 下一學期的可執行行動清單。

JSON Context:
{context}"""

    try:
        response = await get_chat_model().ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
    except Exception as exc:
        raise DiagnosisGenerationError from exc

    return str(response.content)


async def diagnose_graduation_and_career(
    db: AsyncSession,
    payload: DiagnosisRequest,
) -> DiagnosisResponse:
    department, requirement, courses = await load_diagnosis_inputs(db, payload)
    stats = calculate_credit_statistics(
        payload=payload,
        department=department,
        requirement=requirement,
        department_courses=courses,
    )
    structured_context, rag_context = await asyncio.gather(
        load_structured_career_context(
            db,
            department_id=payload.department_id,
        ),
        retrieve_career_rag_context(
            db,
            department_name=department.name_zh,
        ),
    )
    career_context = dedupe_career_context(
        [*structured_context, *rag_context]
    )[:12]
    report = await generate_diagnosis_report(
        context=build_llm_context(
            stats=stats,
            requirement=requirement,
            career_context=career_context,
        ),
        current_semester=payload.current_semester,
    )
    return DiagnosisResponse(
        session_id=payload.session_id,
        credit_statistics=stats,
        career_context=career_context,
        report_markdown=report,
    )


async def persist_diagnosis_result(
    db: AsyncSession,
    *,
    payload: DiagnosisRequest,
    response: DiagnosisResponse,
    user: AuthUser,
) -> DiagnosisResult:
    """Persist one private report snapshot for later verified PDF export."""

    result = DiagnosisResult(
        session_id=payload.session_id,
        owner_user_id=diagnosis_owner_id(user),
        owner_email_hash=diagnosis_email_hash(user),
        department_id=payload.department_id,
        current_semester=payload.current_semester,
        result_json=response.model_dump(mode="json"),
    )
    db.add(result)
    await db.commit()
    await db.refresh(result)
    return result


async def load_diagnosis_result_for_export(
    db: AsyncSession,
    *,
    user: AuthUser,
    session_id: str | None = None,
) -> DiagnosisResult | None:
    """Load the caller-owned diagnosis snapshot by session id or newest first."""

    stmt = select(DiagnosisResult).where(
        DiagnosisResult.owner_user_id == diagnosis_owner_id(user)
    )
    if session_id:
        stmt = stmt.where(DiagnosisResult.session_id == session_id)
    stmt = stmt.order_by(DiagnosisResult.created_at.desc()).limit(1)
    return await db.scalar(stmt)
