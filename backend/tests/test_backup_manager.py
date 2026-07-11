from __future__ import annotations

import hashlib
import hmac
import tarfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.models import Base
from scripts.backup_manager import (
    FORMAT_NAME,
    FORMAT_VERSION,
    MANIFEST_NAME,
    SIGNATURE_NAME,
    BackupIntegrityError,
    BackupManagerError,
    add_regular_file,
    canonical_json_bytes,
    deserialize_value,
    extract_and_verify_archive,
    schema_hash,
    serialize_value,
    strict_json_loads,
    table_schema,
    validate_table_files,
)


TEST_SECRET = "test-backup-hmac-secret-with-32-characters"


def build_empty_archive(
    root: Path,
    *,
    tamper_table: str | None = None,
) -> Path:
    staging = root / "source"
    tables_directory = staging / "tables"
    tables_directory.mkdir(parents=True)
    entries = []

    for table in Base.metadata.sorted_tables:
        payload = b""
        table_file = tables_directory / f"{table.name}.ndjson"
        table_file.write_bytes(payload)
        entries.append(
            {
                **table_schema(table),
                "file": f"tables/{table.name}.ndjson",
                "row_count": 0,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    manifest = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "backup_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "database_schema": "public",
        "alembic_revisions": ["test_revision"],
        "schema_sha256": schema_hash(Base.metadata),
        "hnsw_indexes": [
            {
                "schema": "public",
                "name": "ix_courses_embedding_hnsw_cosine",
                "table": "courses",
            }
        ],
        "tables": entries,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    signature = hmac.new(
        TEST_SECRET.encode(),
        manifest_bytes,
        hashlib.sha256,
    ).hexdigest()
    (staging / MANIFEST_NAME).write_bytes(manifest_bytes)
    (staging / SIGNATURE_NAME).write_text(
        signature,
        encoding="ascii",
    )
    if tamper_table is not None:
        (tables_directory / f"{tamper_table}.ndjson").write_bytes(
            b'{"tampered":true}\n'
        )

    archive_path = root / "backup.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
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
        for entry in entries:
            add_regular_file(
                archive,
                staging / entry["file"],
                entry["file"],
            )
    return archive_path


def test_sql_value_round_trip_including_vector_and_json() -> None:
    vector_type = Vector(3)
    assert deserialize_value(
        serialize_value([0.25, -1, 2.5], vector_type),
        vector_type,
    ) == [0.25, -1.0, 2.5]

    numeric_type = Numeric(10, 4)
    assert deserialize_value(
        serialize_value(Decimal("123.4500"), numeric_type),
        numeric_type,
    ) == Decimal("123.4500")

    timestamp_type = DateTime(timezone=True)
    timestamp = datetime.now(UTC)
    assert deserialize_value(
        serialize_value(timestamp, timestamp_type),
        timestamp_type,
    ) == timestamp

    array_type = ARRAY(String())
    assert deserialize_value(
        serialize_value(["光電", "交換"], array_type),
        array_type,
    ) == ["光電", "交換"]

    json_type = JSONB()
    json_value = {
        "resource_id": uuid4(),
        "score": Decimal("4.75"),
        "tags": ["課程", "實驗室"],
    }
    assert deserialize_value(
        serialize_value(json_value, json_type),
        json_type,
    ) == json_value


def test_vector_dimension_and_non_finite_values_are_rejected() -> None:
    with pytest.raises(BackupManagerError):
        serialize_value([1.0, 2.0], Vector(3))
    with pytest.raises(BackupManagerError):
        serialize_value([1.0, float("nan"), 3.0], Vector(3))


def test_strict_json_rejects_duplicate_keys_and_nan() -> None:
    with pytest.raises(BackupIntegrityError, match="Duplicate"):
        strict_json_loads(b'{"id":1,"id":2}')
    with pytest.raises(BackupIntegrityError, match="Non-standard"):
        strict_json_loads(b'{"score":NaN}')


def test_signed_archive_verifies_before_row_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_HMAC_SECRET", TEST_SECRET)
    archive_path = build_empty_archive(tmp_path)
    restore_staging = tmp_path / "restore"
    restore_staging.mkdir()

    manifest = extract_and_verify_archive(
        archive_path,
        restore_staging,
        max_uncompressed_bytes=10 * 1024 * 1024,
    )
    validate_table_files(
        restore_staging,
        manifest,
        max_row_bytes=1024 * 1024,
    )

    assert all(
        entry["row_count"] == 0
        for entry in manifest["tables"]
    )


def test_archive_tampering_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_HMAC_SECRET", TEST_SECRET)
    archive_path = build_empty_archive(
        tmp_path,
        tamper_table="courses",
    )
    restore_staging = tmp_path / "restore"
    restore_staging.mkdir()

    with pytest.raises(
        BackupIntegrityError,
        match="Size mismatch|Checksum",
    ):
        extract_and_verify_archive(
            archive_path,
            restore_staging,
            max_uncompressed_bytes=10 * 1024 * 1024,
        )


def test_wrong_hmac_secret_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_HMAC_SECRET", TEST_SECRET)
    archive_path = build_empty_archive(tmp_path)
    monkeypatch.setenv(
        "BACKUP_HMAC_SECRET",
        "a-different-backup-secret-with-32-characters",
    )
    restore_staging = tmp_path / "restore"
    restore_staging.mkdir()

    with pytest.raises(
        BackupIntegrityError,
        match="HMAC",
    ):
        extract_and_verify_archive(
            archive_path,
            restore_staging,
            max_uncompressed_bytes=10 * 1024 * 1024,
        )
