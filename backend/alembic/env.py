from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from os import getenv
from typing import Any

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.models import Base


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Importing Base after all models have been declared gives Alembic the complete
# application schema for autogenerate.
target_metadata = Base.metadata


def get_database_url() -> str:
    """Use the deployment URL when set, otherwise the alembic.ini fallback."""

    return getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")


def render_item(
    item_type: str,
    item: Any,
    autogen_context: Any,
) -> str | bool:
    """
    Render pgvector columns with a stable public import.

    Without this hook, some pgvector/Alembic version combinations generate an
    internal module path or omit the import needed by the revision script.
    """

    if item_type == "type" and isinstance(item, Vector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector(dim={item.dim})"
    return False


def configure_context(connection: Connection | None = None) -> None:
    """Apply options shared by online and offline migration modes."""

    options: dict[str, Any] = {
        "target_metadata": target_metadata,
        "compare_type": True,
        "compare_server_default": True,
        "render_as_batch": False,
        "render_item": render_item,
    }
    if connection is not None:
        options["connection"] = connection
    else:
        options.update(
            {
                "url": get_database_url(),
                "literal_binds": True,
                "dialect_opts": {"paramstyle": "named"},
            }
        )
    context.configure(**options)


def run_migrations_offline() -> None:
    """Emit SQL without opening a database connection."""

    configure_context()
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run Alembic's synchronous operations through an async connection."""

    configure_context(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and bridge Alembic operations with run_sync."""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations against a live database."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
