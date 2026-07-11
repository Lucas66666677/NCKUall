from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.providers import get_chat_model
from app.models import ChatHistory
from app.retrieval.agentic import (
    NO_CONTEXT_ANSWER,
    retrieve_agentic_context,
)
from app.retrieval.types import RetrievedChunk
from app.schemas import ChatCitation, ChatResponse


logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一個成大校園資訊助理。

你只能根據提供的 Agent Tools Context 回答問題。如果 Context 中沒有相關資訊，請誠實回答不知道，絕不能捏造事實。
回答時請遵守：
1. 優先使用真實資料來源中的資訊。
2. 不要把推測、常識或模型既有知識當成資料庫事實。
3. 如果資料不足，請明確說明缺少哪些資訊。
4. 回答要使用繁體中文，語氣清楚、務實、適合成大學生。
5. 可以在文字中用「根據來源 1 / 來源 2」指涉引用資料，但不要編造不存在的來源。
6. 若工具追蹤顯示沒有任何資料，必須回答不知道，並停止推理。
"""


async def load_recent_chat_history(
    db: AsyncSession,
    *,
    session_id: str,
    max_messages: int = 10,
) -> list[ChatHistory]:
    """Load the last five conversation rounds for the same chat session."""

    stmt = (
        select(ChatHistory)
        .where(ChatHistory.session_id == session_id)
        .order_by(ChatHistory.created_at.desc())
        .limit(max_messages)
    )
    messages = list((await db.scalars(stmt)).all())
    messages.reverse()
    return messages


def format_chat_history(messages: list[ChatHistory]) -> str:
    if not messages:
        return "目前沒有先前對話。"

    role_labels = {
        "human": "使用者",
        "ai": "AI",
    }
    return "\n".join(
        f"{role_labels.get(message.role, message.role)}：{message.content}"
        for message in messages
    )


async def save_chat_turn(
    db: AsyncSession,
    *,
    session_id: str,
    user_query: str,
    answer: str,
    department_filter: str,
    category_filter: str | None,
    citations: list[ChatCitation],
    intent: str,
    used_tools: list[str],
) -> None:
    shared_metadata = {
        "department_filter": department_filter,
        "category_filter": category_filter,
        "agent_intent": intent,
        "agent_used_tools": used_tools,
    }
    db.add_all(
        [
            ChatHistory(
                session_id=session_id,
                role="human",
                content=user_query,
                metadata_json=shared_metadata,
            ),
            ChatHistory(
                session_id=session_id,
                role="ai",
                content=answer,
                metadata_json={
                    **shared_metadata,
                    "citations": [citation.model_dump(mode="json") for citation in citations],
                },
            ),
        ]
    )
    await db.commit()


def format_context(chunks: list[RetrievedChunk]) -> str:
    if not chunks:
        return "目前沒有檢索到符合科系與分類條件的資料。"

    context_blocks = []
    for index, chunk in enumerate(chunks, start=1):
        source = chunk.source_title or chunk.source_url or chunk.source_type
        department = chunk.department_name or chunk.metadata_json.get("department_name") or chunk.department_code
        context_blocks.append(
            f"[來源 {index}]\n"
            f"source_title: {source}\n"
            f"source_url: {chunk.source_url or 'N/A'}\n"
            f"department: {department or 'N/A'}\n"
            f"category: {chunk.category}\n"
            f"content: {chunk.content}"
        )
    return "\n\n".join(context_blocks)


def build_citations(chunks: list[RetrievedChunk]) -> list[ChatCitation]:
    citations: list[ChatCitation] = []
    for chunk in chunks:
        citations.append(
            ChatCitation(
                resource_id=chunk.id,
                source_title=chunk.source_title,
                source_url=chunk.source_url,
                source_type=chunk.source_type,
                category=chunk.category,
                department=chunk.department_name or chunk.metadata_json.get("department_name"),
                chunk_index=chunk.chunk_index,
                similarity=round(chunk.relevance_score, 4),
                excerpt=chunk.content[:240],
                metadata=chunk.metadata_json,
            )
        )
    return citations


async def generate_answer(
    *,
    user_query: str,
    context: str,
    chat_history: str,
    tool_trace: str,
) -> str:
    llm = get_chat_model()
    message = (
        "請根據以下 Agent Tools Context、工具追蹤與最近對話紀錄回答使用者問題。\n\n"
        f"最近對話紀錄：\n{chat_history}\n\n"
        f"工具追蹤：\n{tool_trace}\n\n"
        f"Context:\n{context}\n\n"
        f"使用者問題：{user_query}"
    )
    response = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=message)])
    return str(response.content)


async def answer_chat(
    db: AsyncSession,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    user_query: str,
    department_filter: str,
    category_filter: str | None = None,
) -> ChatResponse:
    history_messages = await load_recent_chat_history(db, session_id=session_id)
    retrieval_result = await retrieve_agentic_context(
        session_factory=session_factory,
        user_query=user_query,
        department_filter=department_filter,
        category_filter=category_filter,
    )
    chunks = retrieval_result.chunks
    context = format_context(chunks)
    citations = build_citations(chunks)

    if not chunks:
        answer = NO_CONTEXT_ANSWER
    else:
        answer = await generate_answer(
            user_query=user_query,
            context=context,
            chat_history=format_chat_history(history_messages),
            tool_trace=retrieval_result.tool_trace,
        )

    await save_chat_turn(
        db,
        session_id=session_id,
        user_query=user_query,
        answer=answer,
        department_filter=department_filter,
        category_filter=category_filter,
        citations=citations,
        intent=retrieval_result.intent,
        used_tools=retrieval_result.used_tools,
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        retrieved_count=len(chunks),
        intent=retrieval_result.intent,
        used_tools=retrieval_result.used_tools,
    )
