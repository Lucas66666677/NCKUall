from __future__ import annotations

from os import getenv


DEFAULT_DEV_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")
DEFAULT_CORS_ORIGIN_REGEX = r"^https://[a-z0-9-]+\.vercel\.app$"
DEFAULT_CORS_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
DEFAULT_CORS_HEADERS = (
    "Accept",
    "Authorization",
    "Content-Type",
    "Origin",
    "X-API-KEY",
    "X-Request-ID",
    "X-Requested-With",
)
DEFAULT_EXPOSE_HEADERS = (
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "X-Request-ID",
)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]


def cors_allow_credentials() -> bool:
    return getenv("CORS_ALLOW_CREDENTIALS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_cors_origins() -> list[str]:
    origins = _split_csv(getenv("CORS_ORIGINS"))
    if not origins:
        origins = list(DEFAULT_DEV_ORIGINS)

    if cors_allow_credentials() and "*" in origins:
        raise RuntimeError(
            "Unsafe CORS configuration: CORS_ORIGINS cannot contain '*' "
            "when CORS_ALLOW_CREDENTIALS=true. Set explicit frontend origins."
        )
    return origins


def get_cors_origin_regex() -> str | None:
    return getenv("CORS_ORIGIN_REGEX", DEFAULT_CORS_ORIGIN_REGEX).strip() or None


def get_cors_methods() -> list[str]:
    methods = _split_csv(getenv("CORS_ALLOW_METHODS")) or list(
        DEFAULT_CORS_METHODS,
    )
    if cors_allow_credentials() and "*" in methods:
        raise RuntimeError(
            "Unsafe CORS configuration: CORS_ALLOW_METHODS cannot contain '*' "
            "when CORS_ALLOW_CREDENTIALS=true."
        )
    return methods


def get_cors_headers() -> list[str]:
    headers = _split_csv(getenv("CORS_ALLOW_HEADERS")) or list(
        DEFAULT_CORS_HEADERS,
    )
    if cors_allow_credentials() and "*" in headers:
        raise RuntimeError(
            "Unsafe CORS configuration: CORS_ALLOW_HEADERS cannot contain '*' "
            "when CORS_ALLOW_CREDENTIALS=true."
        )
    return headers


def get_cors_expose_headers() -> list[str]:
    return _split_csv(getenv("CORS_EXPOSE_HEADERS")) or list(
        DEFAULT_EXPOSE_HEADERS,
    )
