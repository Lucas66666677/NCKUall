from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.retrieval import reranker
from app.retrieval.hybrid import (
    build_lexical_retrieval_stmt,
    extract_exact_terms,
    reciprocal_rank_fusion,
)
from app.retrieval.types import RetrievedChunk


def make_chunk(
    title: str,
    *,
    vector_distance: float | None = None,
    lexical_score: float | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid4(),
        content=f"{title} 的完整資料內容",
        source_type="department_html",
        source_url=None,
        source_title=title,
        category="lab_project",
        chunk_index=0,
        metadata_json={},
        department_code="DPS",
        department_name="光電科學與工程學系",
        vector_distance=vector_distance,
        lexical_score=lexical_score,
    )


def test_rrf_fuses_rankings_deduplicates_and_keeps_scores() -> None:
    semantic_first = make_chunk(
        "語意第一",
        vector_distance=0.08,
    )
    shared = make_chunk(
        "雙路命中",
        vector_distance=0.12,
    )
    lexical_only = make_chunk(
        "課程代碼 DPS5001",
        lexical_score=0.9,
    )

    fused = reciprocal_rank_fusion(
        [semantic_first, shared],
        [
            replace(shared, lexical_score=0.95),
            lexical_only,
        ],
        limit=20,
        rrf_k=60,
        vector_weight=1.0,
        lexical_weight=1.15,
    )

    assert len(fused) == 3
    assert fused[0].id == shared.id
    assert fused[0].vector_distance == 0.12
    assert fused[0].lexical_score == 0.95
    assert fused[0].rrf_score > fused[1].rrf_score


def test_exact_term_extraction_covers_course_professor_and_plan() -> None:
    terms = extract_exact_terms(
        "請問 DPS5001 王大明教授與「海外交換菁英計畫」的規定"
    )

    assert "DPS5001" in terms
    assert "王大明" in terms
    assert "王大明教授" in terms
    assert "海外交換菁英計畫" in terms


def test_lexical_statement_uses_weighted_postgres_fts_and_filters() -> None:
    stmt = build_lexical_retrieval_stmt(
        user_query="DPS5001 王大明教授",
        department_filter="光電科學與工程學系",
        category_filter="實驗室",
        limit=30,
    )
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "to_tsvector('simple'::regconfig" in sql
    assert "websearch_to_tsquery('simple'::regconfig" in sql
    assert "career_document_chunks.source_title ILIKE" in sql
    assert "departments.code = 'DPS'" in sql
    assert "career_document_chunks.category = 'lab_project'" in sql
    assert "LIMIT 30" in sql


@pytest.mark.asyncio
async def test_reranker_runs_sync_inference_and_returns_top_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        make_chunk(f"候選 {index}", vector_distance=index / 100)
        for index in range(6)
    ]

    def fake_rerank(
        _query: str,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        return [
            replace(chunk, rerank_score=1.0 - index / 10)
            for index, chunk in enumerate(reversed(chunks))
        ]

    monkeypatch.setattr(reranker, "_rerank_sync", fake_rerank)
    results = await reranker.rerank_chunks(
        "哪一個最相關？",
        candidates,
        limit=4,
    )

    assert [chunk.id for chunk in results] == [
        candidates[5].id,
        candidates[4].id,
        candidates[3].id,
        candidates[2].id,
    ]
    assert [chunk.rerank_score for chunk in results] == [
        1.0,
        0.9,
        0.8,
        0.7,
    ]
