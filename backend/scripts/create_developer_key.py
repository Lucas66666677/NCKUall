"""Provision a one-time plaintext API key for an external developer."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from os import getenv
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import DeveloperKey  # noqa: E402
from app.security.developer_api import (  # noqa: E402
    SUPPORTED_SCOPES,
    generate_api_key,
    hash_api_key,
)


async def create_key(args: argparse.Namespace) -> None:
    database_url = getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required.")

    production = args.environment == "live"
    raw_api_key, key_prefix = generate_api_key(
        production=production,
    )
    expires_at = datetime.now(UTC) + timedelta(
        days=args.expires_in_days
    )
    scopes = sorted(set(args.scopes))

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    try:
        async with session_factory() as session:
            developer_key = DeveloperKey(
                hashed_key=hash_api_key(raw_api_key),
                key_prefix=key_prefix,
                owner_name=args.owner,
                owner_email=args.owner_email,
                scopes=scopes,
                expires_at=expires_at,
                is_active=True,
            )
            session.add(developer_key)
            await session.commit()
            await session.refresh(developer_key)
    finally:
        await engine.dispose()

    print("Developer API key created.")
    print(f"ID: {developer_key.id}")
    print(f"Owner: {developer_key.owner_name}")
    print(f"Scopes: {', '.join(developer_key.scopes)}")
    print(f"Expires at: {developer_key.expires_at.isoformat()}")
    print("")
    print("Store this value now; it cannot be recovered later:")
    print(raw_api_key)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Issue a hashed NCKUall developer API key.",
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--owner-email")
    parser.add_argument(
        "--scope",
        dest="scopes",
        action="append",
        choices=sorted(SUPPORTED_SCOPES),
        required=True,
        help="Repeat this option to grant multiple scopes.",
    )
    parser.add_argument(
        "--expires-in-days",
        type=int,
        default=365,
        choices=range(1, 3651),
    )
    parser.add_argument(
        "--environment",
        choices=("test", "live"),
        default=(
            "live"
            if getenv("APP_ENV", "").lower() == "production"
            else "test"
        ),
    )
    return parser


def main() -> None:
    asyncio.run(create_key(build_parser().parse_args()))


if __name__ == "__main__":
    main()
