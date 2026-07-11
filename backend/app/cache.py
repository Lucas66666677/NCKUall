from __future__ import annotations

import functools
import json
import logging
from os import getenv
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, Protocol, TypeVar

from fastapi import Request
from pydantic import BaseModel, TypeAdapter, ValidationError


logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")
ModelT = TypeVar("ModelT", bound=BaseModel)

DEFAULT_LOW_CHURN_CACHE_TTL_SECONDS = 60 * 60 * 12
COURSE_DETAIL_CACHE_PREFIX = "nckuall:course"
DEPARTMENTS_CACHE_KEY = "nckuall:departments:active"


class AsyncCacheBackend(Protocol):
    async def get(self, name: str) -> str | bytes | None: ...

    async def set(
        self,
        name: str,
        value: str | bytes,
        ex: int | None = None,
    ) -> Any: ...

    async def delete(self, *names: str) -> Any: ...


@dataclass(frozen=True)
class CacheReadResult:
    hit: bool
    value: Any | None = None


class AsyncCacheManager:
    """
    Small fail-open Redis cache facade for read-heavy API responses.

    The manager stores JSON strings only. Route handlers should cache response
    schemas rather than ORM instances so cached payloads stay portable and
    stable across SQLAlchemy session lifecycles.
    """

    def __init__(
        self,
        redis: AsyncCacheBackend | None,
        *,
        namespace: str = "nckuall",
        enabled: bool = True,
    ) -> None:
        self.redis = redis
        self.namespace = namespace.strip(":")
        self.enabled = enabled and redis is not None

    def key(self, *parts: object) -> str:
        clean_parts = [
            str(part).strip(":")
            for part in parts
            if part is not None and str(part) != ""
        ]
        return ":".join([self.namespace, *clean_parts])

    async def get_model(
        self,
        key: str,
        model_type: type[ModelT],
    ) -> CacheReadResult:
        if not self.enabled or self.redis is None:
            return CacheReadResult(hit=False)

        try:
            raw_value = await self.redis.get(key)
        except Exception:
            logger.warning("cache_read_failed", extra={"cache_key": key}, exc_info=True)
            return CacheReadResult(hit=False)

        if raw_value is None:
            return CacheReadResult(hit=False)

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        try:
            return CacheReadResult(
                hit=True,
                value=model_type.model_validate_json(raw_value),
            )
        except (ValidationError, ValueError, TypeError):
            logger.warning(
                "cache_payload_invalid",
                extra={"cache_key": key, "model": model_type.__name__},
                exc_info=True,
            )
            await self.delete(key)
            return CacheReadResult(hit=False)

    async def set_model(
        self,
        key: str,
        value: ModelT | object,
        *,
        model_type: type[ModelT],
        ttl_seconds: int,
    ) -> None:
        if not self.enabled or self.redis is None:
            return

        try:
            model_value = (
                value
                if isinstance(value, model_type)
                else model_type.model_validate(value)
            )
            await self.redis.set(
                key,
                model_value.model_dump_json(),
                ex=ttl_seconds,
            )
        except Exception:
            logger.warning("cache_write_failed", extra={"cache_key": key}, exc_info=True)

    async def get_models(
        self,
        key: str,
        model_type: type[ModelT],
    ) -> CacheReadResult:
        if not self.enabled or self.redis is None:
            return CacheReadResult(hit=False)

        try:
            raw_value = await self.redis.get(key)
        except Exception:
            logger.warning("cache_read_failed", extra={"cache_key": key}, exc_info=True)
            return CacheReadResult(hit=False)

        if raw_value is None:
            return CacheReadResult(hit=False)

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        try:
            adapter = TypeAdapter(list[model_type])
            return CacheReadResult(
                hit=True,
                value=adapter.validate_json(raw_value),
            )
        except (ValidationError, ValueError, TypeError):
            logger.warning(
                "cache_payload_invalid",
                extra={"cache_key": key, "model": f"list[{model_type.__name__}]"},
                exc_info=True,
            )
            await self.delete(key)
            return CacheReadResult(hit=False)

    async def set_models(
        self,
        key: str,
        values: list[ModelT] | list[object],
        *,
        model_type: type[ModelT],
        ttl_seconds: int,
    ) -> None:
        if not self.enabled or self.redis is None:
            return

        try:
            models = [
                value
                if isinstance(value, model_type)
                else model_type.model_validate(value)
                for value in values
            ]
            adapter = TypeAdapter(list[model_type])
            await self.redis.set(
                key,
                adapter.dump_json(models),
                ex=ttl_seconds,
            )
        except Exception:
            logger.warning("cache_write_failed", extra={"cache_key": key}, exc_info=True)

    async def set_json(
        self,
        key: str,
        value: BaseModel | dict[str, Any] | list[Any],
        *,
        ttl_seconds: int,
    ) -> None:
        if not self.enabled or self.redis is None:
            return

        if isinstance(value, BaseModel):
            payload = value.model_dump_json()
        else:
            payload = json.dumps(value, ensure_ascii=False, default=str)

        try:
            await self.redis.set(key, payload, ex=ttl_seconds)
        except Exception:
            logger.warning("cache_write_failed", extra={"cache_key": key}, exc_info=True)

    async def delete(self, *keys: str) -> None:
        if not self.enabled or self.redis is None or not keys:
            return

        try:
            await self.redis.delete(*keys)
        except Exception:
            logger.warning(
                "cache_delete_failed",
                extra={"cache_keys": list(keys)},
                exc_info=True,
            )


def course_detail_cache_key(course_id: object) -> str:
    return f"{COURSE_DETAIL_CACHE_PREFIX}:{course_id}"


def low_churn_cache_ttl_seconds() -> int:
    raw_value = getenv("LOW_CHURN_CACHE_TTL_SECONDS")
    if raw_value is None:
        return DEFAULT_LOW_CHURN_CACHE_TTL_SECONDS

    try:
        parsed = int(raw_value)
    except ValueError:
        logger.warning(
            "invalid_cache_ttl",
            extra={
                "env_var": "LOW_CHURN_CACHE_TTL_SECONDS",
                "value": raw_value,
            },
        )
        return DEFAULT_LOW_CHURN_CACHE_TTL_SECONDS

    return max(60, min(parsed, 60 * 60 * 24 * 7))


def get_cache_manager(request: Request) -> AsyncCacheManager:
    manager: AsyncCacheManager | None = getattr(
        request.app.state,
        "cache_manager",
        None,
    )
    if manager is None:
        return AsyncCacheManager(None, enabled=False)
    return manager


def cache_result(
    *,
    key_builder: Callable[P, str],
    model_type: type[ModelT],
    ttl_seconds: int,
) -> Callable[
    [Callable[P, Awaitable[ModelT]]],
    Callable[P, Awaitable[ModelT]],
]:
    """
    Decorate pure async functions that receive ``cache_manager`` as a kwarg.

    Route handlers often need dependency-injected cache managers, so this
    decorator is intentionally small and optional. It is useful for service
    functions where the key can be derived from function arguments.
    """

    def decorator(
        func: Callable[P, Awaitable[ModelT]],
    ) -> Callable[P, Awaitable[ModelT]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> ModelT:
            cache_manager = kwargs.get("cache_manager")
            if not isinstance(cache_manager, AsyncCacheManager):
                return await func(*args, **kwargs)

            key = key_builder(*args, **kwargs)
            cached = await cache_manager.get_model(key, model_type)
            if cached.hit:
                return cached.value

            value = await func(*args, **kwargs)
            await cache_manager.set_model(
                key,
                value,
                model_type=model_type,
                ttl_seconds=ttl_seconds,
            )
            return value

        return wrapper

    return decorator
