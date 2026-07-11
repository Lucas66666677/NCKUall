from __future__ import annotations

from os import getenv
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pydantic import BaseModel


NCKU_EMAIL_DOMAINS = ("@ncku.edu.tw", "@gs.ncku.edu.tw")

bearer_scheme = HTTPBearer(auto_error=False)


class AuthUser(BaseModel):
    """Authenticated Supabase user extracted from a verified JWT."""

    user_id: str | None = None
    email: str
    claims: dict[str, Any]


def _decode_supabase_jwt(token: str) -> dict[str, Any]:
    jwt_secret = getenv("SUPABASE_JWT_SECRET")
    if not jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase JWT 驗證尚未設定",
        )

    try:
        return jwt.decode(
            token,
            jwt_secret,
            algorithms=["HS256"],
            audience=getenv("SUPABASE_JWT_AUDIENCE", "authenticated"),
            options={"verify_aud": bool(getenv("SUPABASE_JWT_AUDIENCE"))},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="無效或過期的登入憑證",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthUser:
    """Level 1: any signed-in Supabase user."""

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="請先登入",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = _decode_supabase_jwt(credentials.credentials)
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入憑證缺少 email",
        )

    return AuthUser(user_id=claims.get("sub"), email=email, claims=claims)


def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthUser | None:
    """
    Return a verified user when a token is present, otherwise a guest.

    Invalid tokens deliberately raise 401 instead of silently falling back to
    guest limits.
    """

    if credentials is None:
        return None

    claims = _decode_supabase_jwt(credentials.credentials)
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登入憑證缺少 email",
        )
    return AuthUser(user_id=claims.get("sub"), email=email, claims=claims)


def verify_ncku_user(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """Level 2: signed-in user with an NCKU email domain."""

    if not user.email.endswith(NCKU_EMAIL_DOMAINS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="發布評價需綁定成大信箱",
        )

    return user


def is_admin_user(user: AuthUser) -> bool:
    """Read administrator status only from trusted JWT claims."""

    app_metadata = user.claims.get("app_metadata")
    return (
        (
            isinstance(app_metadata, dict)
            and app_metadata.get("is_admin") is True
        )
        or user.claims.get("is_admin") is True
    )


def verify_admin_user(
    user: Annotated[AuthUser | None, Depends(get_optional_user)],
) -> AuthUser:
    """
    Require a server-managed admin claim.

    Supabase user_metadata is intentionally ignored because users can modify it.
    """

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="僅限管理員存取",
        )

    if not is_admin_user(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="僅限管理員存取",
        )
    return user


def verify_visual_ingestion_user(
    user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """Allow trusted administrators or verified NCKU accounts."""

    if is_admin_user(user) or user.email.endswith(NCKU_EMAIL_DOMAINS):
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="僅限管理員或成大認證帳號上傳資料",
    )
