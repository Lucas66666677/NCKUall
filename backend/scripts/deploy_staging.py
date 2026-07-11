"""
Defensive Supabase Staging deployment helper for NCKUall.

Run from the repository root or backend directory:
    python backend/scripts/deploy_staging.py

Required environment:
    DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:6543/postgres

What this script does:
    1. Normalizes Supabase URLs and enforces sslmode=require.
    2. Checks/creates required PostgreSQL extensions before migrations.
    3. Detects create_all-contaminated databases and stamps Alembic head.
    4. Runs Alembic upgrade head for clean or versioned databases.
    5. Seeds the NCKU department master table idempotently.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote_plus, unquote

# --- 加入這段 Windows 專屬的 Async 修復代碼 ---
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
# ---------------------------------------------

SQLALCHEMY_IMPORT_ERROR: ModuleNotFoundError | None = None
try:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
except ModuleNotFoundError as exc:
    SQLALCHEMY_IMPORT_ERROR = exc


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_EXTENSIONS = ("vector", "pg_trgm", "fuzzystrmatch")
CORE_TABLES = ("departments", "courses")

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"


def colorize(message: str, color: str) -> str:
    return f"{color}{message}{Color.RESET}"


def log_info(message: str) -> None:
    print(colorize(f"INFO  {message}", Color.CYAN))


def log_success(message: str) -> None:
    print(colorize(f"OK    {message}", Color.GREEN))


def log_warn(message: str) -> None:
    print(colorize(f"WARN  {message}", Color.YELLOW))


def log_error(message: str) -> None:
    print(colorize(f"ERROR {message}", Color.RED), file=sys.stderr)


def abort(message: str, *, code: int = 1) -> NoReturn:
    log_error(message)
    raise SystemExit(code)


def ensure_runtime_dependencies() -> None:
    if SQLALCHEMY_IMPORT_ERROR is None:
        return

    missing_package = SQLALCHEMY_IMPORT_ERROR.name or "required package"
    abort(
        f"缺少 Python 套件：{missing_package}\n"
        "請先在 backend 虛擬環境安裝依賴：\n"
        "    cd backend\n"
        "    python -m venv .venv\n"
        "    .venv\\Scripts\\python -m pip install -r requirements.txt\n"
        "或在 Mac/Linux 使用：\n"
        "    .venv/bin/python -m pip install -r requirements.txt",
        code=2,
    )


def load_env_file(path: Path, *, override: bool = False) -> None:
    """Load a small KEY=VALUE env file without adding python-dotenv."""

    if not path.exists():
        abort(f"指定的 env 檔不存在：{path}")

    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")
    loaded = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(raw_line)
        if not match:
            log_warn(f"略過無法解析的 env 行：{raw_line}")
            continue
        key, value = match.groups()
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
            loaded += 1

    log_success(f"已載入 {loaded} 個環境變數：{path}")


def rewrite_postgres_driver_prefix(database_url: str) -> str:
    """Force SQLAlchemy's psycopg 3 dialect while accepting copied Supabase URLs."""

    if database_url.startswith("postgres://"):
        log_warn("已將 postgres:// 自動修正為 postgresql+psycopg://。")
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        log_warn("已將 postgresql:// 自動修正為 postgresql+psycopg://。")
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql+psycopg2://"):
        log_warn("已將 postgresql+psycopg2:// 自動修正為 postgresql+psycopg://。")
        return database_url.replace(
            "postgresql+psycopg2://",
            "postgresql+psycopg://",
            1,
        )
    return database_url


def quote_url_userinfo(database_url: str) -> str:
    """
    Encode username/password before SQLAlchemy parses the URL.

    Supabase copied URLs are often manually edited, and passwords containing
    characters such as !, @, #, /, +, or % can break URL parsing when left raw.
    This function isolates the authority section, splits on the last @, and
    quote_plus-encodes the userinfo fields idempotently enough for deployment
    use: existing percent-encoded bytes are decoded once then encoded again.
    """

    scheme_separator = "://"
    if scheme_separator not in database_url:
        return database_url

    scheme, remainder = database_url.split(scheme_separator, 1)
    at_index = remainder.rfind("@")
    if at_index == -1:
        return database_url

    authority_end = len(remainder)
    for delimiter in ("/", "?", "#"):
        index = remainder.find(delimiter, at_index + 1)
        if index != -1:
            authority_end = min(authority_end, index)

    authority = remainder[:authority_end]
    suffix = remainder[authority_end:]
    userinfo, hostinfo = authority.rsplit("@", 1)
    if ":" in userinfo:
        username, password = userinfo.split(":", 1)
        encoded_userinfo = (
            f"{quote_plus(unquote(username), safe='')}:"
            f"{quote_plus(unquote(password), safe='')}"
        )
    else:
        encoded_userinfo = quote_plus(unquote(userinfo), safe="")

    rebuilt_url = f"{scheme}{scheme_separator}{encoded_userinfo}@{hostinfo}{suffix}"
    if rebuilt_url != database_url:
        log_warn("已自動 URL Encode 資料庫帳號/密碼段落，避免特殊字元造成解析錯誤。")
    return rebuilt_url


def normalize_database_url(raw_url: str) -> str:
    """Normalize provider URLs to SQLAlchemy psycopg and enforce Supabase SSL."""

    database_url = raw_url.strip().strip('"').strip("'")
    if not database_url:
        abort("DATABASE_URL 為空，拒絕部署。")

    database_url = rewrite_postgres_driver_prefix(database_url)
    database_url = quote_url_userinfo(database_url)

    try:
        url = make_url(database_url)
    except Exception as exc:  # noqa: BLE001 - keep deploy error explicit.
        abort(f"DATABASE_URL 格式無法解析：{exc}")

    raw_lower = raw_url.lower()
    host = (url.host or "").lower()
    is_supabase = (
        "supabase.com" in raw_lower
        or "supabase.co" in raw_lower
        or "supabase.com" in host
        or "supabase.co" in host
    )

    if url.query.get("sslmode") != "require":
        url = url.update_query_dict({"sslmode": "require"})
        if is_supabase:
            log_warn("偵測到 Supabase 連線但缺少 sslmode=require，已自動補上。")
        else:
            log_warn("Staging 部署要求 SSL；已自動補上 sslmode=require。")

    return url.render_as_string(hide_password=False)


def masked_database_url(database_url: str) -> str:
    return make_url(database_url).render_as_string(hide_password=True)


def is_transaction_pooler_url(database_url: str) -> bool:
    configured_mode = os.getenv("DB_POOLER_MODE", "").strip().lower()
    if configured_mode in {"transaction", "pgbouncer_transaction"}:
        return True
    if configured_mode in {"session", "direct", "none"}:
        return False

    url = make_url(database_url)
    return url.port == 6543


def async_engine_for_deploy(database_url: str):
    connect_args: dict[str, object] = {
        "application_name": "nckuall-staging-deploy",
    }
    if is_transaction_pooler_url(database_url):
        connect_args["prepare_threshold"] = None
        os.environ.setdefault("DB_DISABLE_PREPARED_STATEMENTS", "true")
        log_warn("偵測到 Supabase 交易模式 pooler；部署檢查連線已停用 prepared statements。")

    return create_async_engine(
        database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


@dataclass(frozen=True)
class SchemaState:
    has_core_tables: bool
    has_alembic_version_table: bool
    alembic_versions: tuple[str, ...]


async def check_connection(database_url: str) -> None:
    engine = async_engine_for_deploy(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT
                        current_database() AS database_name,
                        current_user AS user_name,
                        inet_server_port() AS server_port
                    """
                )
            )
            row = result.mappings().one()
            log_success(
                "已連上資料庫 "
                f"{row['database_name']} as {row['user_name']} "
                f"(port={row['server_port']})"
            )
    except SQLAlchemyError as exc:
        abort(f"無法建立 Supabase Staging 連線：{exc}")
    finally:
        await engine.dispose()


async def ensure_extensions(database_url: str) -> None:
    engine = async_engine_for_deploy(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT extname
                    FROM pg_extension
                    WHERE extname IN ('vector', 'pg_trgm', 'fuzzystrmatch')
                    """
                )
            )
            installed = {row[0] for row in result.all()}

        missing = [extension for extension in REQUIRED_EXTENSIONS if extension not in installed]
        if not missing:
            log_success("必要 PostgreSQL extensions 已全部存在：vector, pg_trgm, fuzzystrmatch")
            return

        log_warn(f"缺少 extensions：{', '.join(missing)}，開始嘗試自動建立。")
        for extension in missing:
            try:
                async with engine.begin() as connection:
                    await connection.execute(
                        text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')
                    )
                log_success(f"已啟用 extension：{extension}")
            except SQLAlchemyError as exc:
                log_error(
                    f"無法建立 extension：{extension}\n"
                    "請先至 Supabase SQL Editor 手動啟用擴充功能：\n"
                    f'    CREATE EXTENSION IF NOT EXISTS "{extension}";\n'
                    f"原始錯誤：{exc}"
                )
                raise SystemExit(2) from exc
    finally:
        await engine.dispose()


async def inspect_schema_state(database_url: str) -> SchemaState:
    engine = async_engine_for_deploy(database_url)
    try:
        async with engine.connect() as connection:
            core_table_checks = [
                f"to_regclass('public.{table_name}') IS NOT NULL"
                for table_name in CORE_TABLES
            ]
            result = await connection.execute(
                text(
                    f"""
                    SELECT
                        ({' OR '.join(core_table_checks)}) AS has_core_tables,
                        to_regclass('public.alembic_version') IS NOT NULL AS has_alembic_version_table
                    """
                )
            )
            row = result.mappings().one()
            has_alembic_version_table = bool(row["has_alembic_version_table"])

            versions: tuple[str, ...] = ()
            if has_alembic_version_table:
                version_rows = await connection.execute(
                    text("SELECT version_num FROM alembic_version ORDER BY version_num")
                )
                versions = tuple(str(item[0]) for item in version_rows.all())

            return SchemaState(
                has_core_tables=bool(row["has_core_tables"]),
                has_alembic_version_table=has_alembic_version_table,
                alembic_versions=versions,
            )
    except SQLAlchemyError as exc:
        abort(f"檢查 schema 狀態失敗：{exc}")
    finally:
        await engine.dispose()


def run_alembic(args: list[str], *, database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    if is_transaction_pooler_url(database_url):
        env.setdefault("DB_DISABLE_PREPARED_STATEMENTS", "true")

    command = [sys.executable, "-m", "alembic", *args]
    log_info(f"執行 Alembic：{' '.join(command)}")

    process = subprocess.run(
        command,
        cwd=BACKEND_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    if process.stdout:
        for line in process.stdout.splitlines():
            print(colorize(f"      {line}", Color.DIM))

    if process.returncode != 0:
        abort(
            f"Alembic {' '.join(args)} 失敗，exit code={process.returncode}。"
        )

    log_success(f"Alembic {' '.join(args)} 完成")


def apply_alembic_lifecycle(schema_state: SchemaState, *, database_url: str) -> None:
    if schema_state.has_core_tables and not schema_state.alembic_versions:
        log_warn(
            "偵測到 departments/courses 已存在，但沒有 Alembic 版本印記；"
            "判定可能曾被 create_all() 初始化，將執行 alembic stamp head。"
        )
        run_alembic(["stamp", "head"], database_url=database_url)
        return

    if schema_state.has_core_tables and schema_state.alembic_versions:
        log_info(
            "偵測到既有 Alembic 版本："
            f"{', '.join(schema_state.alembic_versions)}，將正常 upgrade head。"
        )
        run_alembic(["upgrade", "head"], database_url=database_url)
        return

    log_info("偵測為全新資料庫，將執行 alembic upgrade head。")
    run_alembic(["upgrade", "head"], database_url=database_url)


async def run_seed_departments(*, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    log_info("開始初始化成大科系基礎資料。")
    try:
        from seed_departments import main as seed_departments_main

        await seed_departments_main(dry_run=False)
    except Exception as exc:  # noqa: BLE001 - deployment script should summarize.
        abort(f"科系種子資料初始化失敗：{exc}")
    log_success("科系種子資料初始化完成")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy NCKUall database schema to Supabase Staging safely.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional .env file to load before reading DATABASE_URL.",
    )
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Run extension checks and Alembic only; skip department seeding.",
    )
    parser.add_argument(
        "--force-upgrade",
        action="store_true",
        help=(
            "Always run alembic upgrade head. Use only when you are sure the "
            "database was not initialized by create_all()."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if args.env_file is not None:
        load_env_file(args.env_file.resolve())

    raw_database_url = os.getenv("DATABASE_URL")
    if not raw_database_url:
        abort(
            "缺少 DATABASE_URL。請先設定 Supabase Staging 連線字串後再執行。"
        )

    ensure_runtime_dependencies()
    database_url = normalize_database_url(raw_database_url)
    os.environ["DATABASE_URL"] = database_url

    print(colorize("\nNCKUall Supabase Staging Deploy Guard", Color.BOLD + Color.MAGENTA))
    log_info(f"Backend root：{BACKEND_ROOT}")
    log_info(f"Database URL：{masked_database_url(database_url)}")

    await check_connection(database_url)
    await ensure_extensions(database_url)

    schema_state = await inspect_schema_state(database_url)
    if schema_state.has_core_tables:
        log_warn("雲端資料庫已存在核心資料表：departments 或 courses。")
    else:
        log_success("未偵測到既有核心資料表，資料庫看起來是乾淨狀態。")

    if args.force_upgrade:
        log_warn("--force-upgrade 已啟用，將略過 stamp 判斷直接 upgrade head。")
        run_alembic(["upgrade", "head"], database_url=database_url)
    else:
        apply_alembic_lifecycle(schema_state, database_url=database_url)

    if args.skip_seed:
        log_warn("已依 --skip-seed 略過科系種子資料初始化。")
    else:
        await run_seed_departments(database_url=database_url)

    print(colorize("\n部署防禦流程完成：Supabase Staging schema 已就緒。\n", Color.BOLD + Color.GREEN))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        abort("部署流程被使用者中止。", code=130)
