from __future__ import annotations

from copy import deepcopy
from os import getenv
import re
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration


FILTERED = "[Filtered]"
SENSITIVE_KEY_PARTS = (
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
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s,;]+"),
    re.compile(
        r"(?i)\b(password|passwd|secret|token|api[_-]?key)\s*=\s*"
        r"([^&\s,;]+)"
    ),
    re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+(@)"),
)


def is_sensitive_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def scrub_sensitive_data(value: Any) -> Any:
    """Recursively filter credentials while preserving useful error context."""

    if isinstance(value, dict):
        return {
            key: FILTERED if is_sensitive_key(key) else scrub_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_sensitive_data(item) for item in value)
    if isinstance(value, str):
        scrubbed = SENSITIVE_VALUE_PATTERNS[0].sub(r"\1[Filtered]", value)
        scrubbed = SENSITIVE_VALUE_PATTERNS[1].sub(r"\1=[Filtered]", scrubbed)
        return SENSITIVE_VALUE_PATTERNS[2].sub(
            r"\1[Filtered]\2",
            scrubbed,
        )
    return value


def before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Final application-side scrub before an error leaves the service."""

    return scrub_sensitive_data(deepcopy(event))


def initialize_sentry() -> bool:
    """Initialize Sentry only when a DSN is configured."""

    dsn = getenv("SENTRY_DSN")
    if not dsn:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=getenv("SENTRY_ENVIRONMENT", getenv("APP_ENV", "production")),
        release=getenv("SENTRY_RELEASE"),
        integrations=[FastApiIntegration(transaction_style="endpoint")],
        traces_sample_rate=float(getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        sample_rate=float(getenv("SENTRY_ERROR_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_send=before_send,
        before_send_transaction=before_send,
    )
    return True
