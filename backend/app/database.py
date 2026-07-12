from collections.abc import AsyncGenerator, Generator
from os import getenv
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_database_url(database_url: str) -> str:
    """Force SQLAlchemy to use psycopg 3 for both sync and async engines."""

    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    return database_url


DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/nckuall"
DATABASE_URL = normalize_database_url(
    getenv(
        "DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )
)
DATABASE_READ_URL = normalize_database_url(getenv("DATABASE_READ_URL", DATABASE_URL))
READ_METHODS = {"GET", "HEAD", "OPTIONS"}

POOL_SIZE = int(getenv("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(getenv("DB_MAX_OVERFLOW", "50"))
POOL_TIMEOUT = int(getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(getenv("DB_POOL_RECYCLE", "1800"))
POOL_PRE_PING = getenv("DB_POOL_PRE_PING", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
POOL_USE_LIFO = getenv("DB_POOL_USE_LIFO", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}


def is_transaction_pooler_url(database_url: str) -> bool:
    """
    Detect Supabase transaction pooler URLs.

    Supabase's transaction-mode pooler is typically exposed on port 6543 and
    pooler hosts often contain "pooler.supabase.com". Keep the explicit
    DB_POOLER_MODE override for custom PgBouncer deployments.
    """

    configured_mode = getenv("DB_POOLER_MODE", "").strip().lower()
    if configured_mode in {"transaction", "pgbouncer_transaction"}:
        return True
    if configured_mode in {"direct", "session", "none"}:
        return False

    url = make_url(database_url)
    host = (url.host or "").lower()
    return url.port == 6543 or "pooler.supabase.com" in host


def should_disable_prepared_statements(database_url: str) -> bool:
    """Return whether psycopg server-side prepared statements must be disabled."""

    configured = getenv("DB_DISABLE_PREPARED_STATEMENTS")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    return is_transaction_pooler_url(database_url)


def psycopg_connect_args(database_url: str) -> dict[str, Any]:
    """
    Build DBAPI args shared by sync and async psycopg engines.

    In PgBouncer/Supavisor transaction mode, a backend session is not stable
    across transactions. psycopg prepared statements are session-scoped, so
    they must be disabled by setting prepare_threshold to None.
    """

    connect_args: dict[str, Any] = {
        "application_name": getenv("DB_APPLICATION_NAME", "nckuall-api"),
    }
    if should_disable_prepared_statements(database_url):
        connect_args["prepare_threshold"] = None
    return connect_args


def engine_options(database_url: str) -> dict[str, Any]:
    """Centralize production pool settings for every SQLAlchemy engine."""

    return {
        "pool_size": POOL_SIZE,
        "max_overflow": MAX_OVERFLOW,
        "pool_timeout": POOL_TIMEOUT,
        "pool_recycle": POOL_RECYCLE,
        "pool_pre_ping": POOL_PRE_PING,
        "pool_use_lifo": POOL_USE_LIFO,
        "connect_args": psycopg_connect_args(database_url),
    }


write_engine = create_engine(
    DATABASE_URL,
    **engine_options(DATABASE_URL),
)
read_engine = create_engine(
    DATABASE_READ_URL,
    **engine_options(DATABASE_READ_URL),
)

async_write_engine = create_async_engine(
    DATABASE_URL,
    **engine_options(DATABASE_URL),
)
async_read_engine = create_async_engine(
    DATABASE_READ_URL,
    **engine_options(DATABASE_READ_URL),
)

# Backwards-compatible aliases for migration utilities and legacy imports.
engine = write_engine
async_engine = async_write_engine

WriteSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=write_engine,
)
ReadSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=read_engine,
)

AsyncWriteSessionLocal = async_sessionmaker(
    async_write_engine,
    expire_on_commit=False,
)
AsyncReadSessionLocal = async_sessionmaker(
    async_read_engine,
    expire_on_commit=False,
)

# Writes are the safest default for background jobs and legacy imports.
SessionLocal = WriteSessionLocal
AsyncSessionLocal = AsyncWriteSessionLocal


def should_use_read_session(request: Request | None) -> bool:
    if request is None:
        return False
    return request.method.upper() in READ_METHODS


def get_analytics_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide the isolated session factory used by background analytics."""

    return AsyncWriteSessionLocal


def get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide independent read sessions for concurrent agent tool calls."""

    return AsyncReadSessionLocal


def get_write_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide the primary write session factory for explicit write jobs."""

    return AsyncWriteSessionLocal


def get_read_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide the replica read session factory for explicit read jobs."""

    return AsyncReadSessionLocal


def get_db(request: Request) -> Generator[Session, None, None]:
    """Provide one sync SQLAlchemy session per request.

    GET/HEAD/OPTIONS are routed to the read replica. Unsafe methods use the
    primary writer.
    """

    factory = ReadSessionLocal if should_use_read_session(request) else WriteSessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()


async def get_async_db(
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide one async SQLAlchemy session per request.

    GET/HEAD/OPTIONS are routed to the read replica. Unsafe methods use the
    primary writer.
    """

    factory = AsyncReadSessionLocal if should_use_read_session(request) else AsyncWriteSessionLocal
    async with factory() as db:
        yield db
