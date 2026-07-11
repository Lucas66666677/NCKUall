from __future__ import annotations

import logging
from os import getenv
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from openai import AsyncOpenAI, OpenAIError

from app.schemas import ChatRequest
from app.security.rate_limit import enforce_chat_rate_limit, is_enabled


logger = logging.getLogger(__name__)


class ModerationUnavailableError(RuntimeError):
    """Raised when content safety cannot be evaluated."""


class OpenAIModerationGuardrail:
    """Asynchronous input moderation using OpenAI's moderation endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "omni-moderation-latest",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=1,
        )
        self.model = model

    async def is_blocked(self, text: str) -> bool:
        try:
            response = await self.client.moderations.create(
                model=self.model,
                input=text,
            )
        except OpenAIError as exc:
            raise ModerationUnavailableError from exc

        if not response.results:
            raise ModerationUnavailableError("Moderation returned no results.")

        result = response.results[0]
        if result.flagged:
            categories = result.categories.model_dump()
            flagged_categories = sorted(
                category
                for category, flagged in categories.items()
                if flagged
            )
            logger.warning(
                "Chat input blocked by moderation categories: %s",
                ", ".join(flagged_categories),
            )
        return bool(result.flagged)

    async def close(self) -> None:
        await self.client.close()


async def enforce_chat_guardrails(
    payload: ChatRequest,
    request: Request,
    _rate_limit: Annotated[None, Depends(enforce_chat_rate_limit)],
) -> None:
    """Rate-limit first, then reject unsafe input before RAG/LLM spending."""

    if not is_enabled("CHAT_MODERATION_ENABLED", default=True):
        return

    guardrail: OpenAIModerationGuardrail | None = getattr(
        request.app.state,
        "chat_moderation_guardrail",
        None,
    )
    if guardrail is None:
        if is_enabled("MODERATION_FAIL_OPEN", default=False):
            logger.error("Chat moderation unavailable; failing open by configuration.")
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="內容安全檢查暫時無法使用",
        )

    try:
        blocked = await guardrail.is_blocked(payload.user_query)
    except ModerationUnavailableError as exc:
        logger.exception("Chat moderation provider request failed.")
        if is_enabled("MODERATION_FAIL_OPEN", default=False):
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="內容安全檢查暫時無法使用",
        ) from exc

    if blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="輸入內容違反社群安全規範",
        )
