from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.controllers.chat import answer_chat
from app.controllers.analytics import record_search_log
from app.database import (
    get_analytics_session_factory,
    get_async_db,
    get_async_session_factory,
)
from app.schemas import ChatRequest, ChatResponse
from app.security.guardrails import enforce_chat_guardrails


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def post_chat(
    payload: ChatRequest,
    background_tasks: BackgroundTasks,
    _guardrails: Annotated[None, Depends(enforce_chat_guardrails)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
    app_session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_async_session_factory),
    ],
    analytics_session_factory: Annotated[
        async_sessionmaker[AsyncSession],
        Depends(get_analytics_session_factory),
    ],
) -> ChatResponse:
    """Department-filtered hybrid RAG with RRF and cross-encoder reranking."""

    try:
        response = await answer_chat(
            db,
            session_factory=app_session_factory,
            session_id=payload.session_id,
            user_query=payload.user_query,
            department_filter=payload.department_filter,
            category_filter=payload.category_filter,
        )
        lab_citation = next(
            (
                citation
                for citation in response.citations
                if citation.category == "lab_project"
                and citation.resource_id is not None
            ),
            None,
        )
        background_tasks.add_task(
            record_search_log,
            "lab" if lab_citation else "search",
            lab_citation.resource_id if lab_citation else None,
            session_factory=analytics_session_factory,
        )
        return response
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Retrieval database query failed.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LLM or embedding provider request failed.",
        ) from exc
