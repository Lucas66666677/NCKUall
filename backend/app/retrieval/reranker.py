from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from os import getenv
from threading import Lock
from typing import Any

from app.retrieval.types import RetrievedChunk


logger = logging.getLogger(__name__)
_ranker: Any | None = None
_ranker_lock = Lock()
_rerank_semaphore = asyncio.Semaphore(
    int(getenv("RAG_RERANK_MAX_CONCURRENCY", "2"))
)


def get_ranker():
    """Lazily load one ONNX reranker per Gunicorn worker."""

    global _ranker
    if _ranker is not None:
        return _ranker

    with _ranker_lock:
        if _ranker is None:
            from flashrank import Ranker

            _ranker = Ranker(
                model_name=getenv(
                    "RAG_RERANK_MODEL",
                    "ms-marco-MultiBERT-L-12",
                ),
                cache_dir=getenv(
                    "RAG_RERANK_CACHE_DIR",
                    "/tmp/flashrank",
                ),
                max_length=int(
                    getenv("RAG_RERANK_MAX_LENGTH", "256")
                ),
            )
    return _ranker


def _rerank_sync(
    user_query: str,
    candidates: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    from flashrank import RerankRequest

    ranker = get_ranker()
    passages = [
        {
            "id": str(chunk.id),
            "text": "\n".join(
                part
                for part in (chunk.source_title, chunk.content)
                if part
            ),
        }
        for chunk in candidates
    ]
    results = ranker.rerank(
        RerankRequest(query=user_query, passages=passages)
    )
    candidate_by_id = {
        str(candidate.id): candidate for candidate in candidates
    }

    reranked: list[RetrievedChunk] = []
    for result in results:
        candidate = candidate_by_id.get(str(result["id"]))
        if candidate is None:
            continue
        reranked.append(
            replace(
                candidate,
                rerank_score=float(result["score"]),
            )
        )
    return reranked


async def rerank_chunks(
    user_query: str,
    candidates: list[RetrievedChunk],
    *,
    limit: int = 4,
) -> list[RetrievedChunk]:
    if not candidates:
        return []

    timeout_seconds = float(
        getenv("RAG_RERANK_TIMEOUT_SECONDS", "8")
    )
    await _rerank_semaphore.acquire()
    inference_task = asyncio.create_task(
        asyncio.to_thread(
            _rerank_sync,
            user_query,
            candidates,
        )
    )
    inference_task.add_done_callback(
        lambda _task: _rerank_semaphore.release()
    )
    try:
        # Shield keeps the semaphore occupied until a timed-out thread exits.
        reranked = await asyncio.wait_for(
            asyncio.shield(inference_task),
            timeout=timeout_seconds,
        )
    except Exception:
        logger.exception(
            "rag_reranking_failed",
            extra={"candidate_count": len(candidates)},
        )
        if getenv("RAG_RERANK_FAIL_OPEN", "false").lower() in {
            "1",
            "true",
            "yes",
        }:
            return candidates[:limit]
        raise

    return reranked[:limit]
