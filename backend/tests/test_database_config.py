from __future__ import annotations

from app.database import (
    engine_options,
    is_transaction_pooler_url,
    psycopg_connect_args,
    should_use_read_session,
    should_disable_prepared_statements,
)


DIRECT_URL = "postgresql+psycopg://postgres:secret@db.project.supabase.co:5432/postgres"
POOLER_URL = (
    "postgresql+psycopg://postgres.project:secret@"
    "aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres"
)


def test_transaction_pooler_url_is_detected_by_port_and_host(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DB_POOLER_MODE", raising=False)

    assert is_transaction_pooler_url(POOLER_URL) is True
    assert is_transaction_pooler_url(DIRECT_URL) is False


def test_pooler_mode_override_controls_prepared_statement_detection(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_POOLER_MODE", "transaction")
    monkeypatch.delenv("DB_DISABLE_PREPARED_STATEMENTS", raising=False)
    assert should_disable_prepared_statements(DIRECT_URL) is True

    monkeypatch.setenv("DB_POOLER_MODE", "direct")
    assert should_disable_prepared_statements(POOLER_URL) is False

    monkeypatch.setenv("DB_DISABLE_PREPARED_STATEMENTS", "true")
    assert should_disable_prepared_statements(DIRECT_URL) is True


def test_psycopg_connect_args_disable_prepared_statements_for_pooler(
    monkeypatch,
) -> None:
    monkeypatch.delenv("DB_POOLER_MODE", raising=False)
    monkeypatch.delenv("DB_DISABLE_PREPARED_STATEMENTS", raising=False)

    direct_args = psycopg_connect_args(DIRECT_URL)
    pooler_args = psycopg_connect_args(POOLER_URL)

    assert "prepare_threshold" not in direct_args
    assert pooler_args["prepare_threshold"] is None
    assert pooler_args["application_name"] == "nckuall-api"


def test_engine_options_include_high_concurrency_pool_settings(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DB_POOL_SIZE", "20")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "50")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "30")
    monkeypatch.setenv("DB_POOL_RECYCLE", "1800")
    monkeypatch.setenv("DB_POOL_PRE_PING", "true")
    monkeypatch.setenv("DB_POOL_USE_LIFO", "true")

    options = engine_options(POOLER_URL)

    assert options["pool_size"] == 20
    assert options["max_overflow"] == 50
    assert options["pool_timeout"] == 30
    assert options["pool_recycle"] == 1800
    assert options["pool_pre_ping"] is True
    assert options["pool_use_lifo"] is True
    assert options["connect_args"]["prepare_threshold"] is None


def test_http_method_routes_read_queries_to_replica() -> None:
    class RequestLike:
        def __init__(self, method: str) -> None:
            self.method = method

    assert should_use_read_session(RequestLike("GET")) is True
    assert should_use_read_session(RequestLike("HEAD")) is True
    assert should_use_read_session(RequestLike("OPTIONS")) is True
    assert should_use_read_session(RequestLike("POST")) is False
    assert should_use_read_session(RequestLike("PUT")) is False
    assert should_use_read_session(None) is False
