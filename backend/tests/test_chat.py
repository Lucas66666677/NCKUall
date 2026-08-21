from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers import chat as chat_controller
from app.models import CareerDocumentChunk, ChatHistory, Department
from app.retrieval import agentic as agentic_retrieval


pytestmark = pytest.mark.integration

EMBEDDING = [0.01] * 1536


async def test_chat_retrieves_department_chunk_and_returns_citations(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    department = Department(
        code="DPS",
        name_zh="光電科學與工程學系",
        name_en="Department of Photonics",
    )
    db_session.add(department)
    await db_session.flush()
    career_chunk = CareerDocumentChunk(
        department_id=department.id,
        source_type="department_html",
        source_url="https://dps.ncku.edu.tw/lab/example",
        source_title="光電系王教授實驗室",
        category="lab_project",
        chunk_index=0,
        content="王教授實驗室專題生每週需參加一次研究會議，並完成期末成果報告。",
        metadata_json={
            "department_code": "DPS",
            "department_name": "光電科學與工程學系",
            "category": "lab_project",
        },
        embedding=EMBEDDING,
    )
    db_session.add(career_chunk)
    await db_session.commit()

    embed_query = AsyncMock(return_value=EMBEDDING)
    chat_model = Mock()
    chat_model.ainvoke = AsyncMock(
        return_value=SimpleNamespace(
            content="根據來源 1，專題生每週需參加研究會議並完成期末成果報告。"
        )
    )
    monkeypatch.setattr(
        agentic_retrieval,
        "embed_query",
        embed_query,
    )
    monkeypatch.setattr(
        agentic_retrieval,
        "route_intent",
        AsyncMock(
            return_value=agentic_retrieval.IntentDecision(
                intent="career",
                tools=("career",),
                reason="integration_test",
            )
        ),
    )
    monkeypatch.setattr(
        chat_controller,
        "get_chat_model",
        lambda: chat_model,
    )
    monkeypatch.setattr(
        agentic_retrieval,
        "rerank_chunks",
        AsyncMock(
            side_effect=lambda _query, chunks, *, limit: chunks[:limit]
        ),
    )

    response = await client.post(
        "/api/chat",
        json={
            "session_id": "chat-integration-session",
            "user_query": "王教授實驗室的專題規定是什麼？",
            "department_filter": "光電科學與工程學系",
            "category_filter": "實驗室",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["retrieved_count"] == 1
    assert "每週需參加研究會議" in body["answer"]
    assert len(body["citations"]) == 1
    assert body["citations"][0] == {
        "resource_id": str(career_chunk.id),
        "source_title": "光電系王教授實驗室",
        "source_url": "https://dps.ncku.edu.tw/lab/example",
        "source_type": "department_html",
        "category": "lab_project",
        "department": "光電科學與工程學系",
        "chunk_index": 0,
        "similarity": 1.0,
        "excerpt": "王教授實驗室專題生每週需參加一次研究會議，並完成期末成果報告。",
        "metadata": {
            "department_code": "DPS",
            "department_name": "光電科學與工程學系",
            "category": "lab_project",
        },
    }
    embed_query.assert_awaited_once_with(
        "王教授實驗室的專題規定是什麼？"
    )
    chat_model.ainvoke.assert_awaited_once()

    history = list(
        (
            await db_session.scalars(
                select(ChatHistory)
                .where(ChatHistory.session_id == "chat-integration-session")
                .order_by(ChatHistory.created_at)
            )
        ).all()
    )
    assert [message.role for message in history] == ["human", "ai"]
    assert history[0].content == "王教授實驗室的專題規定是什麼？"
    assert history[1].metadata_json["citations"][0]["source_title"] == "光電系王教授實驗室"
