from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from os import getenv
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_db
from app.models import DeveloperKey
from app.security.rate_limit import RedisRateLimiter, is_enabled


logger = logging.getLogger(__name__)

API_KEY_PATTERN = re.compile(
    r"^ncku_(?:live|test)_[A-Za-z0-9_-]{32,180}$"
)
API_KEY_PREFIX_LENGTH = 20
DEFAULT_RATE_LIMIT = 60

COURSES_READ_SCOPE = "courses:read"
EVENTS_READ_SCOPE = "events:read"
SUPPORTED_SCOPES = frozenset(
    {
        COURSES_READ_SCOPE,
        EVENTS_READ_SCOPE,
    }
)

api_key_header = APIKeyHeader(
    name="X-API-KEY",
    scheme_name="DeveloperApiKey",
    description=(
        "NCKUall developer key. The plaintext value is issued once and must "
        "be sent in the X-API-KEY request header."
    ),
    auto_error=False,
)


@dataclass(frozen=True)
class DeveloperPrincipal:
    key_id: UUID
    key_prefix: str
    owner_name: str
    scopes: frozenset[str]

    def permits(self, required_scope: str) -> bool:
        resource = required_scope.partition(":")[0]
        return (
            required_scope in self.scopes
            or "*" in self.scopes
            or f"{resource}:*" in self.scopes
        )


def get_api_key_hash_secret() -> str:
    secret = getenv("API_KEY_HASH_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError(
            "API_KEY_HASH_SECRET must contain at least 32 characters."
        )
    return secret


def hash_api_key(raw_api_key: str, *, secret: str | None = None) -> str:
    """Return a deterministic keyed digest without storing plaintext keys."""

    key_secret = secret or get_api_key_hash_secret()
    return hmac.new(
        key_secret.encode("utf-8"),
        raw_api_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_api_key(*, production: bool) -> tuple[str, str]:
    environment = "live" if production else "test"
    raw_api_key = f"ncku_{environment}_{secrets.token_urlsafe(32)}"
    return raw_api_key, raw_api_key[:API_KEY_PREFIX_LENGTH]


def get_developer_rate_limit() -> int:
    try:
        return max(
            1,
            min(
                int(
                    getenv(
                        "API_KEY_RATE_LIMIT_PER_MINUTE",
                        str(DEFAULT_RATE_LIMIT),
                    )
                ),
                10_000,
            ),
        )
    except ValueError:
        return DEFAULT_RATE_LIMIT


def invalid_api_key_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="無效或已失效的 API Key",
        headers={"WWW-Authenticate": "ApiKey"},
    )


async def validate_api_key(
    request: Request,
    response: Response,
    raw_api_key: Annotated[str | None, Security(api_key_header)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
) -> DeveloperPrincipal | None:
    """
    Validate and rate-limit an optional developer credential.

    Requests without X-API-KEY remain public to preserve the platform's guest
    read access. If the header is present, every validation check is mandatory.
    """

    if raw_api_key is None:
        return None
    raw_api_key = raw_api_key.strip()
    if not API_KEY_PATTERN.fullmatch(raw_api_key):
        raise invalid_api_key_error()

    try:
        hashed_key = hash_api_key(raw_api_key)
    except RuntimeError as exc:
        logger.critical("Developer API key hashing is not configured.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="開發者 API 驗證服務暫時無法使用",
        ) from exc

    developer_key = await db.scalar(
        select(DeveloperKey)
        .where(DeveloperKey.hashed_key == hashed_key)
        .limit(1)
    )
    now = datetime.now(UTC)
    if (
        developer_key is None
        or not hmac.compare_digest(
            developer_key.hashed_key,
            hashed_key,
        )
        or not developer_key.is_active
        or developer_key.revoked_at is not None
        or (
            developer_key.expires_at is not None
            and developer_key.expires_at <= now
        )
    ):
        raise invalid_api_key_error()

    principal = DeveloperPrincipal(
        key_id=developer_key.id,
        key_prefix=developer_key.key_prefix,
        owner_name=developer_key.owner_name,
        scopes=frozenset(developer_key.scopes),
    )

    if not is_enabled("API_KEY_RATE_LIMIT_ENABLED", default=True):
        return principal

    limiter: RedisRateLimiter | None = getattr(
        request.app.state,
        "developer_api_rate_limiter",
        None,
    )
    if limiter is None:
        if is_enabled("API_KEY_RATE_LIMIT_FAIL_OPEN", default=False):
            logger.error(
                "Developer API rate limiter unavailable; failing open.",
                extra={"developer_key_id": str(principal.key_id)},
            )
            return principal
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="開發者 API 限流服務暫時無法使用",
        )

    limit = get_developer_rate_limit()
    try:
        decision = await limiter.check(
            key=f"rate_limit:developer:{principal.key_id}",
            limit=limit,
        )
    except Exception as exc:
        logger.exception(
            "Developer API rate-limit check failed.",
            extra={"developer_key_id": str(principal.key_id)},
        )
        if is_enabled("API_KEY_RATE_LIMIT_FAIL_OPEN", default=False):
            return principal
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="開發者 API 限流服務暫時無法使用",
        ) from exc

    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Reset"] = str(decision.retry_after)
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API Key 請求過於頻繁，請稍後再試",
            headers={
                "Retry-After": str(decision.retry_after),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.retry_after),
            },
        )
    return principal


def require_developer_scope(required_scope: str):
    if required_scope not in SUPPORTED_SCOPES:
        raise ValueError(f"Unsupported developer API scope: {required_scope}")

    async def dependency(
        principal: Annotated[
            DeveloperPrincipal | None,
            Depends(validate_api_key),
        ],
    ) -> DeveloperPrincipal | None:
        if principal is not None and not principal.permits(required_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API Key 缺少必要權限：{required_scope}",
            )
        return principal

    return dependency
