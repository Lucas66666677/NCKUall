from contextlib import asynccontextmanager
import asyncio
import logging
from os import getenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api.router import api_router
from app.availability import ReadOnlyModeMiddleware, check_database_health
from app.cache import AsyncCacheManager
from app.database import async_write_engine
from app.observability.logging import configure_logging
from app.observability.middleware import ObservabilityMiddleware
from app.observability.sentry import initialize_sentry
from app.security.audit import SecurityAlertMonitor
from app.security.cors import (
    cors_allow_credentials,
    get_cors_expose_headers,
    get_cors_headers,
    get_cors_methods,
    get_cors_origin_regex,
    get_cors_origins,
)
from app.security.exceptions import install_exception_handlers
from app.security.guardrails import OpenAIModerationGuardrail
from app.security.rate_limit import RedisRateLimiter
from app.retrieval.reranker import get_ranker
from app.realtime.notifications import notification_broker
from app.realtime.routes import router as realtime_router
from app.vector_index_health import check_vector_indexes
from app.visual_ingestion.service import OpenAIVisualParser


configure_logging()
initialize_sentry()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    redis_client: Redis | None = None
    moderation_guardrail: OpenAIModerationGuardrail | None = None
    application.state.chat_rate_limiter = None
    application.state.developer_api_rate_limiter = None
    application.state.visual_ingestion_rate_limiter = None
    application.state.cache_manager = AsyncCacheManager(None, enabled=False)
    application.state.security_alert_monitor = SecurityAlertMonitor()
    application.state.chat_moderation_guardrail = None
    application.state.notification_broker = notification_broker
    application.state.visual_ingestion_parser = None
    visual_ingestion_parser: OpenAIVisualParser | None = None

    redis_url = getenv("REDIS_URL")
    if redis_url:
        try:
            redis_client = Redis.from_url(
                redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            await redis_client.ping()
            application.state.chat_rate_limiter = RedisRateLimiter(redis_client)
            application.state.developer_api_rate_limiter = RedisRateLimiter(
                redis_client
            )
            application.state.visual_ingestion_rate_limiter = (
                RedisRateLimiter(
                    redis_client,
                    window_seconds=60 * 60,
                )
            )
            application.state.cache_manager = AsyncCacheManager(redis_client)
            application.state.security_alert_monitor = SecurityAlertMonitor(
                redis_client
            )
        except Exception:
            logger.exception("Could not initialize Redis-backed services.")
            if redis_client is not None:
                await redis_client.aclose()
                redis_client = None
    else:
        logger.warning(
            "REDIS_URL is not configured; API caching is disabled and "
            "protected Redis-backed rate limiting will fail closed."
        )

    visual_ingestion_api_key = getenv(
        "VISUAL_INGEST_OPENAI_API_KEY"
    ) or getenv("OPENAI_API_KEY")
    if visual_ingestion_api_key:
        visual_ingestion_parser = OpenAIVisualParser(
            api_key=visual_ingestion_api_key,
            model=getenv(
                "VISUAL_INGEST_OPENAI_MODEL",
                "gpt-4o-mini",
            ),
            timeout_seconds=float(
                getenv("VISUAL_INGEST_TIMEOUT_SECONDS", "45")
            ),
        )
        application.state.visual_ingestion_parser = (
            visual_ingestion_parser
        )
    else:
        logger.warning(
            "No OpenAI API key is configured for visual ingestion."
        )

    moderation_api_key = getenv("MODERATION_OPENAI_API_KEY") or getenv(
        "OPENAI_API_KEY"
    )
    if moderation_api_key:
        moderation_guardrail = OpenAIModerationGuardrail(
            api_key=moderation_api_key,
            model=getenv("OPENAI_MODERATION_MODEL", "omni-moderation-latest"),
            timeout_seconds=float(getenv("MODERATION_TIMEOUT_SECONDS", "5")),
        )
        application.state.chat_moderation_guardrail = moderation_guardrail
    else:
        logger.warning(
            "No moderation API key is configured; protected chat will fail closed."
        )

    await check_database_health(application)
    await notification_broker.start(redis_client)

    if getenv("CHECK_VECTOR_INDEXES_ON_STARTUP", "false").lower() == "true":
        try:
            await check_vector_indexes(async_write_engine)
        except Exception:
            logger.exception("Could not check vector index health during startup.")

    if getenv("RAG_RERANK_PRELOAD", "true").lower() == "true":
        try:
            await asyncio.to_thread(get_ranker)
        except Exception:
            logger.exception("Could not preload the RAG reranker.")
            if (
                getenv("RAG_RERANK_FAIL_OPEN", "false").lower()
                != "true"
            ):
                raise

    try:
        yield
    finally:
        await notification_broker.stop()
        if visual_ingestion_parser is not None:
            await visual_ingestion_parser.close()
        if moderation_guardrail is not None:
            await moderation_guardrail.close()
        if redis_client is not None:
            await redis_client.aclose()


app = FastAPI(
    title="NCKU Student Resource API",
    description="Backend API for course planning, career planning, campus events, and life resources.",
    version="0.1.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_origin_regex=get_cors_origin_regex(),
    allow_credentials=cors_allow_credentials(),
    allow_methods=get_cors_methods(),
    allow_headers=get_cors_headers(),
    expose_headers=get_cors_expose_headers(),
)
app.add_middleware(ReadOnlyModeMiddleware)
app.add_middleware(ObservabilityMiddleware)

install_exception_handlers(app)
app.include_router(api_router)
app.include_router(realtime_router)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str | bool]:
    database = await check_database_health(app)
    status_text = (
        "ok"
        if database.write_ok and database.read_ok
        else "read_only"
        if database.read_only_mode
        else "degraded"
    )
    return {
        "status": status_text,
        "database_write_ok": database.write_ok,
        "database_read_ok": database.read_ok,
        "read_only": database.read_only_mode,
        "checked_at": database.checked_at.isoformat(),
    }
