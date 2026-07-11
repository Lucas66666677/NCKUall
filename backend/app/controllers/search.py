from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import Activity, Course, Department
from app.schemas import SearchSuggestionResponse


SUGGESTION_LIMIT = 8
QUERY_LIMIT_PER_RESOURCE = 8


@dataclass(frozen=True)
class RankedSuggestion:
    resource_type: str
    resource_id: UUID
    label: str
    secondary_text: str | None
    href: str
    score: float


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _match_score(column, keyword: str):
    escaped = _escape_like(keyword)
    return (
        func.similarity(column, keyword)
        + case(
            (func.lower(column) == keyword.lower(), 2.0),
            (column.ilike(f"{escaped}%", escape="\\"), 1.0),
            else_=0.2,
        )
    )


def _course_title_suggestions(
    db: Session,
    *,
    keyword: str,
    department_id: UUID | None,
) -> list[RankedSuggestion]:
    escaped = _escape_like(keyword)
    score = _match_score(Course.title_zh, keyword).label("score")
    statement = (
        select(
            Course.id,
            Course.title_zh,
            Course.course_code,
            Course.instructor_name,
            Department.name_zh.label("department_name"),
            score,
        )
        .join(Department, Course.department_id == Department.id)
        .where(
            or_(
                Course.title_zh.ilike(f"%{escaped}%", escape="\\"),
                Course.course_code.ilike(f"{escaped}%", escape="\\"),
            )
        )
        .order_by(score.desc(), Course.title_zh)
        .limit(QUERY_LIMIT_PER_RESOURCE)
    )
    if department_id is not None:
        statement = statement.where(Course.department_id == department_id)

    return [
        RankedSuggestion(
            resource_type="course",
            resource_id=row.id,
            label=row.title_zh,
            secondary_text=" · ".join(
                part
                for part in (
                    row.course_code,
                    row.instructor_name,
                    row.department_name,
                )
                if part
            ),
            href=f"/courses/{row.id}",
            score=float(row.score),
        )
        for row in db.execute(statement).all()
    ]


def _instructor_suggestions(
    db: Session,
    *,
    keyword: str,
    department_id: UUID | None,
) -> list[RankedSuggestion]:
    escaped = _escape_like(keyword)
    score = _match_score(Course.instructor_name, keyword).label("score")
    statement = (
        select(
            Course.id,
            Course.instructor_name,
            Course.title_zh,
            Department.name_zh.label("department_name"),
            score,
        )
        .join(Department, Course.department_id == Department.id)
        .where(
            Course.instructor_name.is_not(None),
            Course.instructor_name.ilike(
                f"%{escaped}%",
                escape="\\",
            ),
        )
        .order_by(score.desc(), Course.instructor_name, Course.title_zh)
        .limit(QUERY_LIMIT_PER_RESOURCE)
    )
    if department_id is not None:
        statement = statement.where(Course.department_id == department_id)

    return [
        RankedSuggestion(
            resource_type="instructor",
            resource_id=row.id,
            label=row.instructor_name,
            secondary_text=f"{row.title_zh} · {row.department_name}",
            href=(
                "/courses?search="
                f"{quote(row.instructor_name)}"
            ),
            score=float(row.score),
        )
        for row in db.execute(statement).all()
        if row.instructor_name
    ]


def _event_suggestions(
    db: Session,
    *,
    keyword: str,
) -> list[RankedSuggestion]:
    escaped = _escape_like(keyword)
    score = _match_score(Activity.title, keyword).label("score")
    statement = (
        select(
            Activity.id,
            Activity.title,
            Activity.location,
            Activity.start_at,
            score,
        )
        .where(
            Activity.title.ilike(
                f"%{escaped}%",
                escape="\\",
            )
        )
        .order_by(score.desc(), Activity.start_at.desc().nullslast())
        .limit(QUERY_LIMIT_PER_RESOURCE)
    )
    return [
        RankedSuggestion(
            resource_type="event",
            resource_id=row.id,
            label=row.title,
            secondary_text=row.location,
            href=f"/events#event-{row.id}",
            score=float(row.score),
        )
        for row in db.execute(statement).all()
    ]


def get_search_suggestions(
    db: Session,
    *,
    keyword: str,
    department_id: UUID | None,
    limit: int = SUGGESTION_LIMIT,
) -> list[SearchSuggestionResponse]:
    """Return a small, ranked, deduplicated typeahead payload."""

    normalized_keyword = keyword.strip()
    if len(normalized_keyword) < 2:
        return []

    candidates = [
        *_course_title_suggestions(
            db,
            keyword=normalized_keyword,
            department_id=department_id,
        ),
        *_instructor_suggestions(
            db,
            keyword=normalized_keyword,
            department_id=department_id,
        ),
        *_event_suggestions(db, keyword=normalized_keyword),
    ]
    priority = {"course": 0, "instructor": 1, "event": 2}
    candidates.sort(
        key=lambda item: (
            -item.score,
            priority.get(item.resource_type, 99),
            item.label,
        )
    )

    suggestions: list[SearchSuggestionResponse] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        dedupe_key = (
            candidate.resource_type,
            candidate.label.casefold(),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        suggestions.append(
            SearchSuggestionResponse(
                resource_type=candidate.resource_type,
                resource_id=candidate.resource_id,
                label=candidate.label,
                secondary_text=candidate.secondary_text,
                href=candidate.href,
            )
        )
        if len(suggestions) >= limit:
            break
    return suggestions
