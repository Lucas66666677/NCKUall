from __future__ import annotations

import json
import logging
import logging.config
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from os import getenv
from sys import stdout
from typing import Any


request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)
request_method_context: ContextVar[str | None] = ContextVar(
    "request_method",
    default=None,
)
request_path_context: ContextVar[str | None] = ContextVar(
    "request_path",
    default=None,
)

RESERVED_LOG_RECORD_FIELDS = frozenset(
    logging.makeLogRecord({}).__dict__.keys()
)
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*=\s*"
        r"([^&\s,;]+)"
    ),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+(@)"),
)
SENSITIVE_EXTRA_KEYS = (
    "authorization",
    "cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "dsn",
    "database_url",
    "redis_url",
)


def redact_text(value: str) -> str:
    redacted = value
    redacted = SENSITIVE_TEXT_PATTERNS[0].sub(r"\1[Filtered]", redacted)
    redacted = SENSITIVE_TEXT_PATTERNS[1].sub(r"\1=[Filtered]", redacted)
    redacted = SENSITIVE_TEXT_PATTERNS[2].sub(r"\1[Filtered]\2", redacted)
    return redacted


def is_sensitive_extra_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_EXTRA_KEYS)


class JsonFormatter(logging.Formatter):
    """Render cloud-friendly, single-line JSON without leaking arbitrary data."""

    def format(self, record: logging.LogRecord) -> str:
        exception: dict[str, str] | None = None
        if record.exc_info:
            exception = {
                "type": record.exc_info[0].__name__,
                "message": redact_text(str(record.exc_info[1])),
                "stacktrace": redact_text(self.formatException(record.exc_info)),
            }

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            # Google Cloud recognizes "severity"; "level" remains convenient
            # for Render and generic JSON log search.
            "severity": record.levelname,
            "level": record.levelname,
            "service": getenv("SERVICE_NAME", "nckuall-api"),
            "environment": getenv("APP_ENV", "development"),
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "request_id": getattr(record, "request_id", None)
            or request_id_context.get(),
            "method": getattr(record, "method", None)
            or request_method_context.get(),
            "path": getattr(record, "path", None)
            or request_path_context.get(),
            "status_code": getattr(record, "status_code", None),
            "duration_ms": getattr(record, "duration_ms", None),
            "async_function": getattr(record, "async_function", None)
            or record.funcName,
        }
        if exception is not None:
            payload["exception"] = exception

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in RESERVED_LOG_RECORD_FIELDS
            and key
            not in {
                "request_id",
                "method",
                "path",
                "status_code",
                "duration_ms",
                "async_function",
            }
            and isinstance(value, (str, int, float, bool, type(None)))
        }
        extra = {
            key: "[Filtered]"
            if is_sensitive_extra_key(key)
            else redact_text(value)
            if isinstance(value, str)
            else value
            for key, value in extra.items()
        }
        if extra:
            payload["attributes"] = extra

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure application, Uvicorn, and Gunicorn logs for stdout ingestion."""

    level = getenv("LOG_LEVEL", "INFO").upper()
    configuration: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": JsonFormatter,
            },
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": stdout,
            },
        },
        "root": {
            "handlers": ["stdout"],
            "level": level,
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["stdout"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["stdout"],
                "level": level,
                "propagate": False,
            },
            # Access logs are emitted by ObservabilityMiddleware with richer
            # request context, so disable Uvicorn's duplicate access line.
            "uvicorn.access": {
                "handlers": [],
                "level": "WARNING",
                "propagate": False,
            },
            "gunicorn.error": {
                "handlers": ["stdout"],
                "level": level,
                "propagate": False,
            },
            "gunicorn.access": {
                "handlers": [],
                "level": "WARNING",
                "propagate": False,
            },
        },
    }
    logging.config.dictConfig(configuration)
    logging.captureWarnings(True)


def bind_request_context(
    *,
    request_id: str,
    method: str,
    path: str,
) -> tuple[Token, Token, Token]:
    """Bind request metadata to the current async task."""

    return (
        request_id_context.set(request_id),
        request_method_context.set(method),
        request_path_context.set(path),
    )


def reset_request_context(tokens: tuple[Token, Token, Token]) -> None:
    """Restore context variables after an ASGI request completes."""

    request_id_context.reset(tokens[0])
    request_method_context.reset(tokens[1])
    request_path_context.reset(tokens[2])
