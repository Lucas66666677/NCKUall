from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from os import getenv
from typing import Any
from uuid import uuid4

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.database import (
    get_analytics_session_factory,
    get_async_db,
    get_async_session_factory,
    get_db,
)
from app.main import app
from app.models import Base
from app.security.guardrails import enforce_chat_guardrails


TEST_JWT_SECRET = "test-only-supabase-jwt-secret"
TEST_JWT_AUDIENCE = "authenticated"


def normalize_test_database_url(url: str) -> str:
    """Force SQLAlchemy's psycopg 3 driver for sync and async test engines."""

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


@dataclass(frozen=True)
class TestDatabase:
    schema: str
    async_engine: AsyncEngine
    sync_engine: Engine
    async_session_factory: async_sessionmaker[AsyncSession]
    sync_session_factory: sessionmaker[Session]


@pytest_asyncio.fixture
async def test_database() -> AsyncGenerator[TestDatabase, None]:
    """
    Create a fresh PostgreSQL schema for each test.

    PostgreSQL is intentional: SQLite cannot faithfully exercise pgvector,
    JSONB, ARRAY, PostgreSQL enum, or the RAG metadata filtering expression.
    """

    raw_url = getenv("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests.")

    database_url = normalize_test_database_url(raw_url)
    schema = f"test_{uuid4().hex}"
    search_path = f"-csearch_path={schema},public"

    admin_engine = create_async_engine(
        database_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    async_engine: AsyncEngine | None = None
    sync_engine: Engine | None = None

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        async_engine = create_async_engine(
            database_url,
            connect_args={"options": search_path},
            pool_pre_ping=True,
        )
        sync_engine = create_engine(
            database_url,
            connect_args={"options": search_path},
            pool_pre_ping=True,
        )

        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        yield TestDatabase(
            schema=schema,
            async_engine=async_engine,
            sync_engine=sync_engine,
            async_session_factory=async_sessionmaker(
                async_engine,
                expire_on_commit=False,
            ),
            sync_session_factory=sessionmaker(
                sync_engine,
                expire_on_commit=False,
            ),
        )
    finally:
        if async_engine is not None:
            await async_engine.dispose()
        if sync_engine is not None:
            sync_engine.dispose()

        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    test_database: TestDatabase,
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an async SQLAlchemy 2.0 session for arranging and asserting data."""

    async with test_database.async_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    test_database: TestDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[AsyncClient, None]:
    """Run HTTP requests through FastAPI with both database dependencies isolated."""

    async def override_get_async_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_database.async_session_factory() as session:
            yield session

    def override_get_db() -> Generator[Session, None, None]:
        with test_database.sync_session_factory() as session:
            yield session

    async def bypass_chat_guardrails() -> None:
        return None

    app.dependency_overrides[get_async_db] = override_get_async_db
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_analytics_session_factory] = (
        lambda: test_database.async_session_factory
    )
    app.dependency_overrides[get_async_session_factory] = (
        lambda: test_database.async_session_factory
    )
    app.dependency_overrides[enforce_chat_guardrails] = bypass_chat_guardrails
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("SUPABASE_JWT_AUDIENCE", TEST_JWT_AUDIENCE)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture
def make_access_token():
    """Create signed Supabase-like JWTs without bypassing the auth dependency."""

    def _make_access_token(
        email: str,
        *,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        now = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "sub": str(uuid4()),
            "email": email,
            "aud": TEST_JWT_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "role": "authenticated",
        }
        claims.update(extra_claims or {})
        return jwt.encode(
            claims,
            TEST_JWT_SECRET,
            algorithm="HS256",
        )

    return _make_access_token
