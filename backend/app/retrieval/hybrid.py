from __future__ import annotations

from dataclasses import replace
from os import getenv
import re
from typing import Any

from sqlalchemy import (
    Select,
    case,
    cast,
    func,
    literal_column,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import String, Text

from app.models import CareerDocumentChunk, Department
from app.retrieval.types import RetrievedChunk


DEPARTMENT_ALIASES: dict[str, str] = {
    "光電系": "DPS",
    "光電科學與工程學系": "DPS",
    "光電": "DPS",
    "電機系": "EE",
    "電機工程學系": "EE",
    "電機": "EE",
    "資工系": "CSIE",
    "資訊工程學系": "CSIE",
    "資工": "CSIE",
    "全校": "GLOBAL",
    "全校通用": "GLOBAL",
}

COURSE_CODE_PATTERN = re.compile(
    r"\b[A-Za-z]{1,10}[-_]?\d{2,8}\b"
)
QUOTED_TERM_PATTERN = re.compile(
    r"""["「『](.{2,40}?)["」』]"""
)
PROFESSOR_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{1,6})(教授|老師)"
)


def extract_exact_terms(user_query: str) -> list[str]:
    """Extract identifiers that benefit from exact substring matching."""

    terms: list[str] = []
    terms.extend(COURSE_CODE_PATTERN.findall(user_query))
    terms.extend(QUOTED_TERM_PATTERN.findall(user_query))

    for raw_name, title in PROFESSOR_PATTERN.findall(user_query):
        name = raw_name.lstrip("請問想查找關於和與的")
        if name:
            # Keep at most four trailing CJK characters as a person name.
            name = name[-4:]
            terms.extend((name, f"{name}{title}"))

    normalized_terms: list[str] = []
    for term in terms:
        normalized = term.strip()
        if (
            len(normalized) >= 2
            and normalized not in normalized_terms
        ):
            normalized_terms.append(normalized)
    return normalized_terms[:8]


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def resolve_department_filter(
    department_filter: str,
) -> tuple[str, str]:
    normalized = department_filter.strip()
    code = DEPARTMENT_ALIASES.get(normalized, normalized.upper())
    return normalized, code


def normalize_category_filter(
    category_filter: str | None,
) -> str | None:
    if not category_filter:
        return None
    aliases = {
        "海外交換": "overseas_exchange",
        "交換": "overseas_exchange",
        "實驗室專題": "lab_project",
        "實驗室": "lab_project",
        "預研": "pre_master",
        "推甄": "grad_school",
        "轉系": "transfer_department",
        "計畫": "program",
    }
    value = category_filter.strip()
    return aliases.get(value, value)


def _selected_chunk_columns() -> tuple[Any, ...]:
    return (
        CareerDocumentChunk.id,
        CareerDocumentChunk.content,
        CareerDocumentChunk.source_type,
        CareerDocumentChunk.source_url,
        CareerDocumentChunk.source_title,
        CareerDocumentChunk.category,
        CareerDocumentChunk.chunk_index,
        CareerDocumentChunk.metadata_json,
        Department.code.label("department_code"),
        Department.name_zh.label("department_name"),
    )


def _apply_metadata_filters(
    stmt: Select,
    *,
    department_filter: str,
    category_filter: str | None,
) -> Select:
    department_label, department_code = resolve_department_filter(
        department_filter
    )
    stmt = stmt.where(
        or_(
            Department.code == department_code,
            Department.name_zh.ilike(f"%{department_label}%"),
            cast(
                CareerDocumentChunk.metadata_json[
                    "department_code"
                ].astext,
                String,
            )
            == department_code,
            cast(
                CareerDocumentChunk.metadata_json[
                    "department_name"
                ].astext,
                String,
            ).ilike(f"%{department_label}%"),
        )
    )

    normalized_category = normalize_category_filter(category_filter)
    if normalized_category:
        stmt = stmt.where(
            or_(
                CareerDocumentChunk.category == normalized_category,
                cast(
                    CareerDocumentChunk.metadata_json["category"].astext,
                    String,
                )
                == normalized_category,
            )
        )
    return stmt


def build_vector_retrieval_stmt(
    *,
    query_embedding: list[float],
    department_filter: str,
    category_filter: str | None,
    limit: int,
) -> Select:
    distance = CareerDocumentChunk.embedding.cosine_distance(
        query_embedding
    ).label("vector_distance")
    stmt = (
        select(*_selected_chunk_columns(), distance)
        .outerjoin(
            Department,
            CareerDocumentChunk.department_id == Department.id,
        )
        .where(CareerDocumentChunk.embedding.is_not(None))
    )
    stmt = _apply_metadata_filters(
        stmt,
        department_filter=department_filter,
        category_filter=category_filter,
    )
    return stmt.order_by(distance).limit(limit)


def _fts_document():
    config = literal_column("'simple'::regconfig")
    empty = literal_column("''")
    title_vector = func.setweight(
        func.to_tsvector(
            config,
            func.coalesce(CareerDocumentChunk.source_title, empty),
        ),
        literal_column("'A'"),
    )
    content_vector = func.setweight(
        func.to_tsvector(
            config,
            func.coalesce(CareerDocumentChunk.content, empty),
        ),
        literal_column("'B'"),
    )
    metadata_vector = func.setweight(
        func.to_tsvector(
            config,
            func.coalesce(
                cast(CareerDocumentChunk.metadata_json, Text),
                empty,
            ),
        ),
        literal_column("'C'"),
    )
    return title_vector.op("||")(content_vector).op("||")(
        metadata_vector
    )


def build_lexical_retrieval_stmt(
    *,
    user_query: str,
    department_filter: str,
    category_filter: str | None,
    limit: int,
) -> Select:
    config = literal_column("'simple'::regconfig")
    document = _fts_document()
    query = func.websearch_to_tsquery(config, user_query)
    fts_match = document.op("@@")(query)
    base_rank = func.ts_rank_cd(
        document,
        query,
        32,
    )

    exact_terms = extract_exact_terms(user_query)
    if exact_terms:
        title_match = or_(
            *[
                CareerDocumentChunk.source_title.ilike(
                    f"%{_escape_like(term)}%",
                    escape="\\",
                )
                for term in exact_terms
            ]
        )
        content_match = or_(
            *[
                CareerDocumentChunk.content.ilike(
                    f"%{_escape_like(term)}%",
                    escape="\\",
                )
                for term in exact_terms
            ]
        )
        lexical_match = or_(
            fts_match,
            title_match,
            content_match,
        )
        lexical_score = (
            base_rank
            + case((title_match, 1.0), else_=0.0)
            + case((content_match, 0.5), else_=0.0)
        ).label("lexical_score")
    else:
        lexical_match = fts_match
        lexical_score = base_rank.label("lexical_score")

    stmt = (
        select(*_selected_chunk_columns(), lexical_score)
        .outerjoin(
            Department,
            CareerDocumentChunk.department_id == Department.id,
        )
        .where(lexical_match)
    )
    stmt = _apply_metadata_filters(
        stmt,
        department_filter=department_filter,
        category_filter=category_filter,
    )
    return stmt.order_by(lexical_score.desc()).limit(limit)


def _chunk_from_row(
    row: dict[str, Any],
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=row["id"],
        content=row["content"],
        source_type=row["source_type"],
        source_url=row["source_url"],
        source_title=row["source_title"],
        category=row["category"],
        chunk_index=row["chunk_index"],
        metadata_json=row["metadata_json"] or {},
        department_code=row["department_code"],
        department_name=row["department_name"],
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


async def retrieve_vector_chunks(
    db: AsyncSession,
    *,
    query_embedding: list[float],
    department_filter: str,
    category_filter: str | None,
    limit: int,
) -> list[RetrievedChunk]:
    rows = (
        await db.execute(
            build_vector_retrieval_stmt(
                query_embedding=query_embedding,
                department_filter=department_filter,
                category_filter=category_filter,
                limit=limit,
            )
        )
    ).mappings().all()
    return [
        _chunk_from_row(
            dict(row),
            vector_distance=float(row["vector_distance"]),
        )
        for row in rows
    ]


async def retrieve_lexical_chunks(
    db: AsyncSession,
    *,
    user_query: str,
    department_filter: str,
    category_filter: str | None,
    limit: int,
) -> list[RetrievedChunk]:
    rows = (
        await db.execute(
            build_lexical_retrieval_stmt(
                user_query=user_query,
                department_filter=department_filter,
                category_filter=category_filter,
                limit=limit,
            )
        )
    ).mappings().all()
    return [
        _chunk_from_row(
            dict(row),
            lexical_score=float(row["lexical_score"]),
        )
        for row in rows
    ]


def reciprocal_rank_fusion(
    vector_results: list[RetrievedChunk],
    lexical_results: list[RetrievedChunk],
    *,
    limit: int = 20,
    rrf_k: int | None = None,
    vector_weight: float | None = None,
    lexical_weight: float | None = None,
) -> list[RetrievedChunk]:
    """Fuse heterogeneous rankings without comparing their raw scores."""

    k = rrf_k or int(getenv("RAG_RRF_K", "60"))
    vector_weight = (
        vector_weight
        if vector_weight is not None
        else float(getenv("RAG_VECTOR_WEIGHT", "1.0"))
    )
    lexical_weight = (
        lexical_weight
        if lexical_weight is not None
        else float(getenv("RAG_LEXICAL_WEIGHT", "1.15"))
    )

    scores: dict[Any, float] = {}
    chunks: dict[Any, RetrievedChunk] = {}

    for rank, chunk in enumerate(vector_results, start=1):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + (
            vector_weight / (k + rank)
        )
        chunks[chunk.id] = chunk

    for rank, chunk in enumerate(lexical_results, start=1):
        scores[chunk.id] = scores.get(chunk.id, 0.0) + (
            lexical_weight / (k + rank)
        )
        existing = chunks.get(chunk.id)
        chunks[chunk.id] = (
            replace(existing, lexical_score=chunk.lexical_score)
            if existing is not None
            else chunk
        )

    ranked_ids = sorted(
        scores,
        key=lambda chunk_id: scores[chunk_id],
        reverse=True,
    )[:limit]
    return [
        replace(chunks[chunk_id], rrf_score=scores[chunk_id])
        for chunk_id in ranked_ids
    ]
