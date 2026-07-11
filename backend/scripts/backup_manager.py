"""
Streaming, signed backup and atomic restore for the NCKUall PostgreSQL schema.

Examples:
    python scripts/backup_manager.py --backup --output-dir ./backups
    python scripts/backup_manager.py --restore ./backups/nckuall-....tar.gz

Required environment:
    DATABASE_URL
    BACKUP_HMAC_SECRET  # stable random value with at least 32 characters
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import suppress
import hashlib
import hmac
import json
import math
import os
import re
import sys
import tarfile
import tempfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SQLEnum,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Uuid,
    select,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    create_async_engine,
)
from sqlalchemy.sql.sqltypes import JSON as SQLJSON

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models import Base  # noqa: E402


FORMAT_NAME = "nckuall-postgresql-backup"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.hmac"
TABLE_FILE_PATTERN = re.compile(r"^tables/[a-z0-9_]+\.ndjson$")
ADVISORY_LOCK_NAME = "nckuall_backup_restore_v1"
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_UNCOMPRESSED_GB = 100.0
DEFAULT_MAX_ROW_MB = 16.0


class BackupManagerError(RuntimeError):
    """Expected operational failure with a safe message."""


class BackupIntegrityError(BackupManagerError):
    """Backup contents do not match their signed manifest."""


def database_url_from_environment() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise BackupManagerError("DATABASE_URL is required.")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://",
            "postgresql+psycopg://",
            1,
        )
    elif database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )
    if not database_url.startswith("postgresql+psycopg://"):
        raise BackupManagerError(
            "DATABASE_URL must use postgresql+psycopg."
        )
    return database_url


def backup_hmac_secret() -> bytes:
    secret = os.getenv("BACKUP_HMAC_SECRET", "")
    if len(secret) < 32:
        raise BackupManagerError(
            "BACKUP_HMAC_SECRET must contain at least 32 characters."
        )
    return secret.encode("utf-8")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def strict_json_loads(payload: bytes) -> Any:
    def reject_duplicate_keys(
        pairs: list[tuple[str, Any]],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise BackupIntegrityError(
                    f"Duplicate JSON key: {key}"
                )
            result[key] = value
        return result

    def reject_non_standard_constant(value: str) -> None:
        raise BackupIntegrityError(
            f"Non-standard JSON constant: {value}"
        )

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_standard_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupIntegrityError("Invalid JSON document.") from exc


def type_signature(column_type: Any) -> str:
    return column_type.compile(dialect=postgresql.dialect())


def table_schema(table: Table) -> dict[str, Any]:
    return {
        "name": table.name,
        "columns": [
            {
                "name": column.name,
                "type": type_signature(column.type),
                "nullable": column.nullable,
                "primary_key": column.primary_key,
            }
            for column in table.columns
        ],
        "foreign_keys": sorted(
            f"{foreign_key.parent.name}->{foreign_key.target_fullname}"
            for foreign_key in table.foreign_keys
        ),
    }


def schema_descriptor(metadata: MetaData) -> list[dict[str, Any]]:
    return [
        table_schema(table)
        for table in metadata.sorted_tables
    ]


def schema_hash(metadata: MetaData) -> str:
    return hashlib.sha256(
        canonical_json_bytes(schema_descriptor(metadata))
    ).hexdigest()


def ensure_finite(value: float) -> float:
    if not math.isfinite(value):
        raise BackupManagerError(
            "Non-finite floating-point value cannot be backed up."
        )
    return value


def ensure_finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise BackupManagerError(
            "Non-finite decimal value cannot be backed up."
        )
    return value


def serialize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return ensure_finite(value)
    if isinstance(value, Decimal):
        return {
            "__ncku_backup_type__": "decimal",
            "value": str(ensure_finite_decimal(value)),
        }
    if isinstance(value, UUID):
        return {
            "__ncku_backup_type__": "uuid",
            "value": str(value),
        }
    if isinstance(value, datetime):
        return {
            "__ncku_backup_type__": "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, date):
        return {
            "__ncku_backup_type__": "date",
            "value": value.isoformat(),
        }
    if isinstance(value, Enum):
        return serialize_json_value(value.value)
    if isinstance(value, bytes):
        return {
            "__ncku_backup_type__": "bytes",
            "value": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise BackupManagerError(
                "JSON object keys must be strings."
            )
        return {
            key: serialize_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [serialize_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return serialize_json_value(value.tolist())
    raise BackupManagerError(
        f"Unsupported JSON value type: {type(value).__name__}"
    )


def deserialize_json_value(value: Any) -> Any:
    if isinstance(value, list):
        return [deserialize_json_value(item) for item in value]
    if not isinstance(value, dict):
        if isinstance(value, float):
            return ensure_finite(value)
        return value

    if set(value) == {"__ncku_backup_type__", "value"}:
        marker = value["__ncku_backup_type__"]
        raw_value = value["value"]
        if not isinstance(raw_value, str):
            raise BackupIntegrityError(
                "Invalid tagged JSON value."
            )
        if marker == "decimal":
            decimal_value = Decimal(raw_value)
            if not decimal_value.is_finite():
                raise BackupIntegrityError(
                    "Non-finite tagged decimal value."
                )
            return decimal_value
        if marker == "uuid":
            return UUID(raw_value)
        if marker == "datetime":
            return datetime.fromisoformat(raw_value)
        if marker == "date":
            return date.fromisoformat(raw_value)
        if marker == "bytes":
            return base64.b64decode(
                raw_value,
                validate=True,
            )
        raise BackupIntegrityError(
            f"Unknown tagged JSON value: {marker}"
        )
    return {
        key: deserialize_json_value(item)
        for key, item in value.items()
    }


def vector_as_float_list(value: Any, dimension: int | None) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        raise BackupManagerError("Vector value must be an array.")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(
            item,
            (int, float, Decimal),
        ):
            raise BackupManagerError(
                "Vector elements must be numeric."
            )
        result.append(ensure_finite(float(item)))
    if dimension is not None and len(result) != dimension:
        raise BackupManagerError(
            f"Vector dimension mismatch: expected {dimension}, "
            f"received {len(result)}."
        )
    return result


def serialize_value(value: Any, column_type: Any) -> Any:
    if value is None:
        return None
    if isinstance(column_type, Vector):
        return vector_as_float_list(value, column_type.dim)
    if isinstance(column_type, ARRAY):
        if not isinstance(value, (list, tuple)):
            raise BackupManagerError("ARRAY value must be a list.")
        return [
            serialize_value(item, column_type.item_type)
            for item in value
        ]
    if isinstance(column_type, SQLJSON):
        return serialize_json_value(value)
    if isinstance(column_type, SQLEnum):
        return value.value if isinstance(value, Enum) else str(value)
    if isinstance(column_type, DateTime):
        if not isinstance(value, datetime):
            raise BackupManagerError("Invalid datetime value.")
        return value.isoformat()
    if isinstance(column_type, Date):
        if not isinstance(value, date):
            raise BackupManagerError("Invalid date value.")
        return value.isoformat()
    if isinstance(column_type, (Uuid, postgresql.UUID)):
        return str(value)
    if isinstance(column_type, Numeric):
        return str(ensure_finite_decimal(Decimal(str(value))))
    if isinstance(column_type, Float):
        return ensure_finite(float(value))
    if isinstance(column_type, Integer):
        if isinstance(value, bool):
            raise BackupManagerError("Boolean supplied for integer column.")
        return int(value)
    if isinstance(column_type, Boolean):
        if not isinstance(value, bool):
            raise BackupManagerError("Invalid boolean value.")
        return value
    if isinstance(column_type, LargeBinary):
        return base64.b64encode(value).decode("ascii")
    if isinstance(column_type, String):
        return str(value)
    raise BackupManagerError(
        f"Unsupported SQL type: {type_signature(column_type)}"
    )


def deserialize_value(value: Any, column_type: Any) -> Any:
    if value is None:
        return None
    try:
        if isinstance(column_type, Vector):
            return vector_as_float_list(value, column_type.dim)
        if isinstance(column_type, ARRAY):
            if not isinstance(value, list):
                raise BackupIntegrityError(
                    "ARRAY backup value must be a list."
                )
            return [
                deserialize_value(item, column_type.item_type)
                for item in value
            ]
        if isinstance(column_type, SQLJSON):
            return deserialize_json_value(value)
        if isinstance(column_type, SQLEnum):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Enum backup value must be a string."
                )
            if column_type.enum_class is not None:
                return column_type.enum_class(value)
            return value
        if isinstance(column_type, DateTime):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Datetime backup value must be a string."
                )
            result = datetime.fromisoformat(value)
            if column_type.timezone and result.tzinfo is None:
                raise BackupIntegrityError(
                    "Timezone-aware datetime lost its timezone."
                )
            return result
        if isinstance(column_type, Date):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Date backup value must be a string."
                )
            return date.fromisoformat(value)
        if isinstance(column_type, (Uuid, postgresql.UUID)):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "UUID backup value must be a string."
                )
            return UUID(value)
        if isinstance(column_type, Numeric):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Numeric backup value must be a string."
                )
            decimal_value = Decimal(value)
            if not decimal_value.is_finite():
                raise BackupIntegrityError(
                    "Numeric backup value must be finite."
                )
            return decimal_value
        if isinstance(column_type, Float):
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float),
            ):
                raise BackupIntegrityError(
                    "Float backup value must be numeric."
                )
            return ensure_finite(float(value))
        if isinstance(column_type, Integer):
            if isinstance(value, bool) or not isinstance(value, int):
                raise BackupIntegrityError(
                    "Integer backup value must be an integer."
                )
            return value
        if isinstance(column_type, Boolean):
            if not isinstance(value, bool):
                raise BackupIntegrityError(
                    "Boolean backup value must be true or false."
                )
            return value
        if isinstance(column_type, LargeBinary):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Binary backup value must be base64 text."
                )
            return base64.b64decode(value, validate=True)
        if isinstance(column_type, String):
            if not isinstance(value, str):
                raise BackupIntegrityError(
                    "Text backup value must be a string."
                )
            return value
    except (
        ValueError,
        TypeError,
        InvalidOperation,
    ) as exc:
        raise BackupIntegrityError(
            f"Invalid value for SQL type {type_signature(column_type)}."
        ) from exc
    raise BackupIntegrityError(
        f"Unsupported SQL type: {type_signature(column_type)}"
    )


def serialize_row(row: Any, table: Table) -> dict[str, Any]:
    return {
        column.name: serialize_value(
            row[column.name],
            column.type,
        )
        for column in table.columns
    }


def deserialize_row(payload: Any, table: Table) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BackupIntegrityError(
            f"Rows for {table.name} must be JSON objects."
        )
    expected_columns = {column.name for column in table.columns}
    if set(payload) != expected_columns:
        missing = sorted(expected_columns - set(payload))
        extra = sorted(set(payload) - expected_columns)
        raise BackupIntegrityError(
            f"Column mismatch for {table.name}; "
            f"missing={missing}, extra={extra}."
        )
    return {
        column.name: deserialize_value(
            payload[column.name],
            column.type,
        )
        for column in table.columns
    }


async def database_context(
    connection: AsyncConnection,
) -> tuple[str, list[str], set[str], list[dict[str, str]]]:
    schema = await connection.scalar(text("SELECT current_schema()"))
    if not isinstance(schema, str) or not schema:
        raise BackupManagerError(
            "Could not determine the PostgreSQL schema."
        )

    revisions = sorted(
        str(value)
        for value in (
            await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )
        ).scalars()
    )
    if not revisions:
        raise BackupManagerError(
            "alembic_version is empty; migrate the database first."
        )

    table_names = {
        str(value)
        for value in (
            await connection.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_catalog.pg_tables
                    WHERE schemaname = current_schema()
                    """
                )
            )
        ).scalars()
        if str(value) != "alembic_version"
    }
    index_rows = (
        await connection.execute(
            text(
                """
                SELECT schemaname, indexname, tablename
                FROM pg_catalog.pg_indexes
                WHERE schemaname = current_schema()
                  AND indexdef ILIKE '% USING hnsw %'
                ORDER BY indexname
                """
            )
        )
    ).mappings()
    hnsw_indexes = [
        {
            "schema": str(row["schemaname"]),
            "name": str(row["indexname"]),
            "table": str(row["tablename"]),
        }
        for row in index_rows
    ]
    return schema, revisions, table_names, hnsw_indexes


def validate_database_tables(actual_tables: set[str]) -> None:
    expected_tables = {
        table.name
        for table in Base.metadata.sorted_tables
    }
    if actual_tables != expected_tables:
        raise BackupManagerError(
            "Database table set does not match app.models.Base; "
            f"missing={sorted(expected_tables - actual_tables)}, "
            f"unmanaged={sorted(actual_tables - expected_tables)}."
        )


async def dump_table(
    connection: AsyncConnection,
    table: Table,
    destination: Path,
    *,
    batch_size: int,
) -> dict[str, Any]:
    digest = hashlib.sha256()
    row_count = 0
    byte_count = 0
    buffer = bytearray()
    primary_key_columns = list(table.primary_key.columns)
    statement = select(table)
    if primary_key_columns:
        statement = statement.order_by(*primary_key_columns)
    statement = statement.execution_options(
        stream_results=True,
        yield_per=batch_size,
    )

    with destination.open("wb") as output:
        async with connection.stream(statement) as result:
            async for row in result.mappings():
                encoded = canonical_json_bytes(
                    serialize_row(row, table)
                )
                buffer.extend(encoded)
                row_count += 1
                if len(buffer) >= 1024 * 1024:
                    output.write(buffer)
                    digest.update(buffer)
                    byte_count += len(buffer)
                    buffer.clear()
        if buffer:
            output.write(buffer)
            digest.update(buffer)
            byte_count += len(buffer)
        output.flush()
        os.fsync(output.fileno())

    return {
        **table_schema(table),
        "file": f"tables/{table.name}.ndjson",
        "row_count": row_count,
        "bytes": byte_count,
        "sha256": digest.hexdigest(),
    }


def add_regular_file(
    archive: tarfile.TarFile,
    source: Path,
    archive_name: str,
) -> None:
    archive.add(
        source,
        arcname=archive_name,
        recursive=False,
        filter=lambda info: normalize_tar_info(info),
    )


def normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o600
    return info


async def create_backup(
    *,
    database_url: str,
    output_dir: Path,
    batch_size: int,
) -> Path:
    secret = backup_hmac_secret()
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_name = (
        f"nckuall-backup-{timestamp}-{uuid4().hex[:8]}.tar.gz"
    )
    final_archive = output_dir / archive_name
    partial_archive = output_dir / f".{archive_name}.part"
    engine = create_async_engine(database_url, pool_pre_ping=True)

    try:
        if engine.dialect.name != "postgresql":
            raise BackupManagerError(
                "Only PostgreSQL backups are supported."
            )
        with tempfile.TemporaryDirectory(
            prefix=".nckuall-backup-",
            dir=output_dir,
        ) as temporary_directory:
            staging = Path(temporary_directory)
            tables_directory = staging / "tables"
            tables_directory.mkdir()

            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            "SET TRANSACTION ISOLATION LEVEL "
                            "REPEATABLE READ READ ONLY"
                        )
                    )
                    await connection.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtext(:lock_name))"
                        ),
                        {"lock_name": ADVISORY_LOCK_NAME},
                    )
                    (
                        database_schema,
                        alembic_revisions,
                        actual_tables,
                        hnsw_indexes,
                    ) = await database_context(connection)
                    validate_database_tables(actual_tables)
                    if not hnsw_indexes:
                        raise BackupManagerError(
                            "No HNSW indexes were found; apply vector "
                            "index migrations before backing up."
                        )

                    table_entries = []
                    for table in Base.metadata.sorted_tables:
                        print(f"Backing up {table.name}...")
                        table_entries.append(
                            await dump_table(
                                connection,
                                table,
                                tables_directory
                                / f"{table.name}.ndjson",
                                batch_size=batch_size,
                            )
                        )

            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "backup_id": str(uuid4()),
                "created_at": datetime.now(UTC).isoformat(),
                "database_schema": database_schema,
                "alembic_revisions": alembic_revisions,
                "schema_sha256": schema_hash(Base.metadata),
                "hnsw_indexes": hnsw_indexes,
                "tables": table_entries,
            }
            manifest_bytes = canonical_json_bytes(manifest)
            signature = hmac.new(
                secret,
                manifest_bytes,
                hashlib.sha256,
            ).hexdigest()
            (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
            (staging / SIGNATURE_NAME).write_text(
                f"{signature}\n",
                encoding="ascii",
            )

            with tarfile.open(
                partial_archive,
                mode="w:gz",
                compresslevel=9,
            ) as archive:
                add_regular_file(
                    archive,
                    staging / MANIFEST_NAME,
                    MANIFEST_NAME,
                )
                add_regular_file(
                    archive,
                    staging / SIGNATURE_NAME,
                    SIGNATURE_NAME,
                )
                for entry in table_entries:
                    add_regular_file(
                        archive,
                        staging / entry["file"],
                        entry["file"],
                    )

            os.replace(partial_archive, final_archive)
            try:
                final_archive.chmod(0o600)
            except OSError:
                pass
            print(
                f"Backup complete: {final_archive} "
                f"({sum(item['row_count'] for item in table_entries)} rows)"
            )
            return final_archive
    finally:
        await engine.dispose()
        if partial_archive.exists():
            with suppress(OSError):
                partial_archive.unlink()


def read_small_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum_bytes: int,
) -> bytes:
    if not member.isfile() or member.size > maximum_bytes:
        raise BackupIntegrityError(
            f"Invalid archive member: {member.name}"
        )
    source = archive.extractfile(member)
    if source is None:
        raise BackupIntegrityError(
            f"Could not read archive member: {member.name}"
        )
    data = source.read(maximum_bytes + 1)
    if len(data) != member.size:
        raise BackupIntegrityError(
            f"Truncated archive member: {member.name}"
        )
    return data


def validate_manifest_structure(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise BackupIntegrityError(
            "Manifest must be a JSON object."
        )
    if (
        manifest.get("format") != FORMAT_NAME
        or manifest.get("format_version") != FORMAT_VERSION
    ):
        raise BackupIntegrityError(
            "Unsupported backup format or version."
        )
    if manifest.get("schema_sha256") != schema_hash(Base.metadata):
        raise BackupIntegrityError(
            "Backup schema does not match the running application."
        )
    revisions = manifest.get("alembic_revisions")
    tables = manifest.get("tables")
    indexes = manifest.get("hnsw_indexes")
    database_schema = manifest.get("database_schema")
    if (
        not isinstance(revisions, list)
        or not revisions
        or not all(isinstance(item, str) for item in revisions)
        or not isinstance(database_schema, str)
        or not database_schema
        or not isinstance(tables, list)
        or not isinstance(indexes, list)
        or not indexes
    ):
        raise BackupIntegrityError(
            "Manifest metadata is incomplete."
        )

    expected_tables = {
        table.name: table
        for table in Base.metadata.sorted_tables
    }
    validated_indexes: set[tuple[str, str, str]] = set()
    for index in indexes:
        if (
            not isinstance(index, dict)
            or set(index) != {"schema", "name", "table"}
            or not all(
                isinstance(index.get(key), str)
                and bool(index[key])
                for key in ("schema", "name", "table")
            )
            or index["schema"] != database_schema
            or index["table"] not in expected_tables
        ):
            raise BackupIntegrityError(
                "Manifest contains invalid HNSW index metadata."
            )
        identity = (
            index["schema"],
            index["name"],
            index["table"],
        )
        if identity in validated_indexes:
            raise BackupIntegrityError(
                "Manifest contains duplicate HNSW indexes."
            )
        validated_indexes.add(identity)

    entries_by_name: dict[str, dict[str, Any]] = {}
    for entry in tables:
        if not isinstance(entry, dict):
            raise BackupIntegrityError(
                "Invalid table manifest entry."
            )
        name = entry.get("name")
        file_name = entry.get("file")
        if (
            not isinstance(name, str)
            or name in entries_by_name
            or name not in expected_tables
            or not isinstance(file_name, str)
            or not TABLE_FILE_PATTERN.fullmatch(file_name)
            or file_name != f"tables/{name}.ndjson"
            or isinstance(entry.get("row_count"), bool)
            or not isinstance(entry.get("row_count"), int)
            or entry["row_count"] < 0
            or isinstance(entry.get("bytes"), bool)
            or not isinstance(entry.get("bytes"), int)
            or entry["bytes"] < 0
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise BackupIntegrityError(
                f"Invalid manifest entry for table: {name!r}."
            )
        expected_schema = table_schema(expected_tables[name])
        for key in ("columns", "foreign_keys"):
            if entry.get(key) != expected_schema[key]:
                raise BackupIntegrityError(
                    f"Schema mismatch for table {name}."
                )
        entries_by_name[name] = entry

    if set(entries_by_name) != set(expected_tables):
        raise BackupIntegrityError(
            "Backup does not contain every application table."
        )
    manifest["_entries_by_name"] = entries_by_name
    return manifest


def extract_and_verify_archive(
    archive_path: Path,
    staging: Path,
    *,
    max_uncompressed_bytes: int,
) -> dict[str, Any]:
    secret = backup_hmac_secret()
    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise BackupIntegrityError(
            "Backup is not a readable tar.gz archive."
        ) from exc

    with archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            raise BackupIntegrityError(
                "Archive contains duplicate member names."
            )
        if MANIFEST_NAME not in names or SIGNATURE_NAME not in names:
            raise BackupIntegrityError(
                "Archive manifest or signature is missing."
            )
        if any(
            not member.isfile()
            or (
                member.name not in {MANIFEST_NAME, SIGNATURE_NAME}
                and not TABLE_FILE_PATTERN.fullmatch(member.name)
            )
            for member in members
        ):
            raise BackupIntegrityError(
                "Archive contains an unsafe or unexpected member."
            )
        if sum(member.size for member in members) > max_uncompressed_bytes:
            raise BackupIntegrityError(
                "Archive exceeds the configured uncompressed size limit."
            )

        manifest_bytes = read_small_member(
            archive,
            archive.getmember(MANIFEST_NAME),
            maximum_bytes=4 * 1024 * 1024,
        )
        signature_bytes = read_small_member(
            archive,
            archive.getmember(SIGNATURE_NAME),
            maximum_bytes=1024,
        )
        expected_signature = hmac.new(
            secret,
            manifest_bytes,
            hashlib.sha256,
        ).hexdigest()
        try:
            supplied_signature = signature_bytes.decode(
                "ascii"
            ).strip()
        except UnicodeDecodeError as exc:
            raise BackupIntegrityError(
                "Manifest signature is invalid."
            ) from exc
        if not hmac.compare_digest(
            supplied_signature,
            expected_signature,
        ):
            raise BackupIntegrityError(
                "Manifest HMAC verification failed."
            )
        manifest = validate_manifest_structure(
            strict_json_loads(manifest_bytes)
        )

        expected_names = {
            MANIFEST_NAME,
            SIGNATURE_NAME,
            *(
                entry["file"]
                for entry in manifest["tables"]
            ),
        }
        if set(names) != expected_names:
            raise BackupIntegrityError(
                "Archive files do not match the signed manifest."
            )

        for entry in manifest["tables"]:
            member = archive.getmember(entry["file"])
            if member.size != entry["bytes"]:
                raise BackupIntegrityError(
                    f"Size mismatch for {entry['file']}."
                )
            source = archive.extractfile(member)
            if source is None:
                raise BackupIntegrityError(
                    f"Could not read {entry['file']}."
                )
            destination = staging / f"{entry['name']}.ndjson"
            digest = hashlib.sha256()
            bytes_written = 0
            with destination.open("wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    bytes_written += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if (
                bytes_written != entry["bytes"]
                or digest.hexdigest() != entry["sha256"]
            ):
                raise BackupIntegrityError(
                    f"Checksum verification failed for "
                    f"{entry['file']}."
                )
        return manifest


def iter_table_rows(
    source: Path,
    table: Table,
    *,
    max_row_bytes: int,
):
    with source.open("rb") as input_file:
        for line_number, raw_line in enumerate(input_file, start=1):
            if len(raw_line) > max_row_bytes:
                raise BackupIntegrityError(
                    f"{table.name} row {line_number} exceeds "
                    "the configured size limit."
                )
            if not raw_line.strip():
                raise BackupIntegrityError(
                    f"{table.name} contains a blank row."
                )
            try:
                payload = strict_json_loads(raw_line)
            except BackupIntegrityError as exc:
                raise BackupIntegrityError(
                    f"Invalid JSON in {table.name} row "
                    f"{line_number}."
                ) from exc
            yield deserialize_row(payload, table)


def validate_table_files(
    staging: Path,
    manifest: dict[str, Any],
    *,
    max_row_bytes: int,
) -> None:
    entries = manifest["_entries_by_name"]
    for table in Base.metadata.sorted_tables:
        print(f"Validating {table.name}...")
        row_count = sum(
            1
            for _row in iter_table_rows(
                staging / f"{table.name}.ndjson",
                table,
                max_row_bytes=max_row_bytes,
            )
        )
        if row_count != entries[table.name]["row_count"]:
            raise BackupIntegrityError(
                f"Row count mismatch for {table.name}: "
                f"expected {entries[table.name]['row_count']}, "
                f"received {row_count}."
            )


def quote_qualified(
    connection: AsyncConnection,
    schema: str,
    name: str,
) -> str:
    preparer = connection.dialect.identifier_preparer
    return (
        f"{preparer.quote_schema(schema)}."
        f"{preparer.quote(name)}"
    )


async def insert_table_rows(
    connection: AsyncConnection,
    table: Table,
    source: Path,
    *,
    batch_size: int,
    max_row_bytes: int,
) -> int:
    batch: list[dict[str, Any]] = []
    inserted = 0
    for row in iter_table_rows(
        source,
        table,
        max_row_bytes=max_row_bytes,
    ):
        batch.append(row)
        if len(batch) >= batch_size:
            await connection.execute(table.insert(), batch)
            inserted += len(batch)
            batch.clear()
    if batch:
        await connection.execute(table.insert(), batch)
        inserted += len(batch)
    return inserted


async def restore_backup(
    *,
    database_url: str,
    archive_path: Path,
    batch_size: int,
    max_uncompressed_bytes: int,
    max_row_bytes: int,
    lock_timeout_seconds: int,
) -> None:
    archive_path = archive_path.expanduser().resolve()
    if not archive_path.is_file():
        raise BackupManagerError(
            f"Backup file does not exist: {archive_path}"
        )

    with tempfile.TemporaryDirectory(
        prefix="nckuall-restore-"
    ) as temporary_directory:
        staging = Path(temporary_directory)
        manifest = extract_and_verify_archive(
            archive_path,
            staging,
            max_uncompressed_bytes=max_uncompressed_bytes,
        )
        validate_table_files(
            staging,
            manifest,
            max_row_bytes=max_row_bytes,
        )

        engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
        )
        try:
            if engine.dialect.name != "postgresql":
                raise BackupManagerError(
                    "Only PostgreSQL restores are supported."
                )
            async with engine.connect() as context_connection:
                (
                    current_schema,
                    current_revisions,
                    actual_tables,
                    current_hnsw_indexes,
                ) = await database_context(context_connection)
            validate_database_tables(actual_tables)
            if current_schema != manifest["database_schema"]:
                raise BackupIntegrityError(
                    "Target database schema differs from the backup."
                )
            if current_revisions != manifest["alembic_revisions"]:
                raise BackupIntegrityError(
                    "Target Alembic revision differs from the backup."
                )
            expected_indexes = {
                (item["schema"], item["name"], item["table"])
                for item in manifest["hnsw_indexes"]
            }
            available_indexes = {
                (item["schema"], item["name"], item["table"])
                for item in current_hnsw_indexes
            }
            if expected_indexes != available_indexes:
                raise BackupIntegrityError(
                    "Target HNSW indexes differ from the backup."
                )

            async with engine.connect() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
                        )
                    )
                    lock_acquired = await connection.scalar(
                        text(
                            "SELECT pg_try_advisory_xact_lock("
                            "hashtext(:lock_name))"
                        ),
                        {"lock_name": ADVISORY_LOCK_NAME},
                    )
                    if not lock_acquired:
                        raise BackupManagerError(
                            "Another backup or restore is currently running."
                        )
                    await connection.execute(
                        text(
                            "SELECT set_config("
                            "'lock_timeout', :timeout, true)"
                        ),
                        {
                            "timeout": (
                                f"{lock_timeout_seconds}s"
                            )
                        },
                    )
                    await connection.execute(
                        text(
                            "SELECT set_config("
                            "'statement_timeout', '0', true)"
                        )
                    )
                    transaction_revisions = sorted(
                        str(value)
                        for value in (
                            await connection.execute(
                                text(
                                    "SELECT version_num "
                                    "FROM alembic_version"
                                )
                            )
                        ).scalars()
                    )
                    if (
                        transaction_revisions
                        != manifest["alembic_revisions"]
                    ):
                        raise BackupIntegrityError(
                            "Alembic revision changed before restore."
                        )

                    qualified_tables = ", ".join(
                        quote_qualified(
                            connection,
                            current_schema,
                            table.name,
                        )
                        for table in Base.metadata.sorted_tables
                    )
                    await connection.execute(
                        text(
                            f"TRUNCATE TABLE {qualified_tables} "
                            "RESTART IDENTITY"
                        )
                    )

                    total_rows = 0
                    for table in Base.metadata.sorted_tables:
                        print(f"Restoring {table.name}...")
                        inserted = await insert_table_rows(
                            connection,
                            table,
                            staging / f"{table.name}.ndjson",
                            batch_size=batch_size,
                            max_row_bytes=max_row_bytes,
                        )
                        expected_count = manifest[
                            "_entries_by_name"
                        ][table.name]["row_count"]
                        if inserted != expected_count:
                            raise BackupIntegrityError(
                                f"Insert count mismatch for {table.name}."
                            )
                        total_rows += inserted

                    for index in current_hnsw_indexes:
                        qualified_index = quote_qualified(
                            connection,
                            index["schema"],
                            index["name"],
                        )
                        print(f"Reindexing {index['name']}...")
                        await connection.execute(
                            text(
                                f"REINDEX INDEX {qualified_index}"
                            )
                        )

            print(
                f"Restore complete: {total_rows} rows, "
                f"{len(current_hnsw_indexes)} HNSW indexes rebuilt."
            )
        finally:
            await engine.dispose()


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(
            "Value must be greater than zero."
        )
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError(
            "Value must be greater than zero."
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or atomically restore a signed NCKUall database backup."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--backup",
        action="store_true",
        help="Create a timestamped tar.gz backup.",
    )
    action.add_argument(
        "--restore",
        type=Path,
        metavar="BACKUP_FILE",
        help="Verify and atomically restore a backup.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("backups"),
        help="Backup destination directory (default: ./backups).",
    )
    parser.add_argument(
        "--batch-size",
        type=positive_integer,
        default=DEFAULT_BATCH_SIZE,
    )
    parser.add_argument(
        "--max-uncompressed-gb",
        type=positive_float,
        default=DEFAULT_MAX_UNCOMPRESSED_GB,
        help="Restore archive safety limit.",
    )
    parser.add_argument(
        "--max-row-mb",
        type=positive_float,
        default=DEFAULT_MAX_ROW_MB,
        help="Maximum serialized size of one row.",
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=positive_integer,
        default=30,
        help="Fail restore when table locks cannot be acquired in time.",
    )
    return parser


async def async_main(args: argparse.Namespace) -> None:
    database_url = database_url_from_environment()
    if args.backup:
        await create_backup(
            database_url=database_url,
            output_dir=args.output_dir,
            batch_size=args.batch_size,
        )
        return
    await restore_backup(
        database_url=database_url,
        archive_path=args.restore,
        batch_size=args.batch_size,
        max_uncompressed_bytes=int(
            args.max_uncompressed_gb * 1024**3
        ),
        max_row_bytes=int(args.max_row_mb * 1024**2),
        lock_timeout_seconds=args.lock_timeout_seconds,
    )


def main() -> None:
    try:
        asyncio.run(async_main(build_parser().parse_args()))
    except BackupManagerError as exc:
        print(f"Backup manager failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
