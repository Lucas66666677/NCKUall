from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Literal

from starlette.responses import Response

SameSite = Literal["lax", "strict", "none"]


@dataclass(frozen=True)
class SecureCookieSettings:
    httponly: bool = True
    secure: bool = True
    samesite: SameSite = "lax"
    path: str = "/"


def get_secure_cookie_settings() -> SecureCookieSettings:
    same_site = getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if same_site not in {"lax", "strict", "none"}:
        same_site = "lax"

    return SecureCookieSettings(
        httponly=getenv("AUTH_COOKIE_HTTPONLY", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        secure=getenv("AUTH_COOKIE_SECURE", "true").strip().lower()
        in {"1", "true", "yes", "on"},
        samesite=same_site,  # type: ignore[arg-type]
    )


def set_auth_cookie(
    response: Response,
    *,
    key: str,
    value: str,
    max_age: int,
    settings: SecureCookieSettings | None = None,
) -> None:
    cookie_settings = settings or get_secure_cookie_settings()
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=cookie_settings.httponly,
        secure=cookie_settings.secure,
        samesite=cookie_settings.samesite,
        path=cookie_settings.path,
    )


def clear_auth_cookie(
    response: Response,
    *,
    key: str,
    settings: SecureCookieSettings | None = None,
) -> None:
    cookie_settings = settings or get_secure_cookie_settings()
    response.delete_cookie(
        key=key,
        path=cookie_settings.path,
        httponly=cookie_settings.httponly,
        secure=cookie_settings.secure,
        samesite=cookie_settings.samesite,
    )
