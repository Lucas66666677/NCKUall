"""
Seed the NCKU department master table using an idempotent async upsert.

Run from any directory:
    python backend/scripts/seed_departments.py

Required environment:
    DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres

Apply Alembic migrations before running this script:
    cd backend
    alembic upgrade head
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict, dataclass
from os import getenv
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Department


@dataclass(frozen=True)
class DepartmentSeed:
    code: str
    name_zh: str
    name_en: str
    college: str
    is_active: bool = True


DEPARTMENTS: tuple[DepartmentSeed, ...] = (
    DepartmentSeed("CHIN", "中國文學系", "Department of Chinese Literature", "文學院"),
    DepartmentSeed("FLLD", "外國語文學系", "Department of Foreign Languages and Literature", "文學院"),
    DepartmentSeed("MATH", "數學系", "Department of Mathematics", "理學院"),
    DepartmentSeed("PHYS", "物理學系", "Department of Physics", "理學院"),
    DepartmentSeed("CHEM", "化學系", "Department of Chemistry", "理學院"),
    DepartmentSeed(
        "DPS",
        "光電科學與工程學系",
        "Department of Photonics",
        "理學院",
    ),
    DepartmentSeed(
        "EE",
        "電機工程學系",
        "Department of Electrical Engineering",
        "電機資訊學院",
    ),
    DepartmentSeed(
        "CSIE",
        "資訊工程學系",
        "Department of Computer Science and Information Engineering",
        "電機資訊學院",
    ),
    DepartmentSeed(
        "ME",
        "機械工程學系",
        "Department of Mechanical Engineering",
        "工學院",
    ),
    DepartmentSeed(
        "CHEN",
        "化學工程學系",
        "Department of Chemical Engineering",
        "工學院",
    ),
    DepartmentSeed("CE", "土木工程學系", "Department of Civil Engineering", "工學院"),
    DepartmentSeed(
        "ES",
        "工程科學系",
        "Department of Engineering Science",
        "工學院",
    ),
    DepartmentSeed(
        "ARE",
        "航空太空工程學系",
        "Department of Aeronautics and Astronautics",
        "工學院",
    ),
    DepartmentSeed(
        "IIM",
        "工業與資訊管理學系",
        "Department of Industrial and Information Management",
        "管理學院",
    ),
    DepartmentSeed(
        "BA",
        "企業管理學系",
        "Department of Business Administration",
        "管理學院",
    ),
    DepartmentSeed("ACC", "會計學系", "Department of Accountancy", "管理學院"),
    DepartmentSeed("STAT", "統計學系", "Department of Statistics", "管理學院"),
    DepartmentSeed("MED", "醫學系", "Department of Medicine", "醫學院"),
    DepartmentSeed("NURS", "護理學系", "Department of Nursing", "醫學院"),
    DepartmentSeed("ARCH", "建築學系", "Department of Architecture", "規劃與設計學院"),
)


def normalize_database_url(url: str) -> str:
    """Normalize common provider URLs to SQLAlchemy's psycopg 3 dialect."""

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql+psycopg2://"):
        return url.replace(
            "postgresql+psycopg2://",
            "postgresql+psycopg://",
            1,
        )
    return url


async def seed_departments(
    session: AsyncSession,
    *,
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Insert missing departments and return (inserted_codes, skipped_codes).

    Existing rows are matched by either code or Chinese name. PostgreSQL's
    conflict fallback also protects against concurrent inserts on unique keys.
    """

    codes = [department.code for department in DEPARTMENTS]
    names = [department.name_zh for department in DEPARTMENTS]
    existing_rows = (
        await session.execute(
            select(Department.code, Department.name_zh).where(
                or_(
                    Department.code.in_(codes),
                    Department.name_zh.in_(names),
                )
            )
        )
    ).all()
    existing_codes = {row.code for row in existing_rows}
    existing_names = {row.name_zh for row in existing_rows}

    pending = [
        department
        for department in DEPARTMENTS
        if department.code not in existing_codes
        and department.name_zh not in existing_names
    ]
    skipped_codes = [
        department.code
        for department in DEPARTMENTS
        if department not in pending
    ]

    if dry_run or not pending:
        return [department.code for department in pending], skipped_codes

    statement = (
        insert(Department)
        .values([asdict(department) for department in pending])
        .on_conflict_do_nothing()
        .returning(Department.code)
    )
    inserted_codes = list((await session.scalars(statement)).all())
    await session.commit()

    skipped_codes.extend(
        department.code
        for department in pending
        if department.code not in inserted_codes
    )
    return inserted_codes, skipped_codes


async def main(*, dry_run: bool = False) -> None:
    raw_database_url = getenv("DATABASE_URL")
    if not raw_database_url:
        raise SystemExit(
            "DATABASE_URL is required. Refusing to use a local fallback for a production seed."
        )

    engine = create_async_engine(
        normalize_database_url(raw_database_url),
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    try:
        async with session_factory() as session:
            inserted, skipped = await seed_departments(
                session,
                dry_run=dry_run,
            )
    finally:
        await engine.dispose()

    action = "Would insert" if dry_run else "Inserted"
    print(f"{action} {len(inserted)} department(s): {', '.join(inserted) or 'none'}")
    print(f"Skipped {len(skipped)} existing/conflicting department(s): {', '.join(skipped) or 'none'}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Idempotently seed the NCKU department master table.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show which rows would be inserted without writing to PostgreSQL.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(main(dry_run=arguments.dry_run))
