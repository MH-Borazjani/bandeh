"""Security utilities: signed session cookies, DB session validation, and RBAC authorization.

Session cookies are cryptographically signed using itsdangerous (HMAC-SHA256).
The payload encapsulates both the user's canonical phone and a server-side session token.
FastAPI dependencies support both fast cookie extraction and full PostgreSQL session verification.
"""

from __future__ import annotations

import os
import re
import time
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User, UserRole
from db.session import get_db

# Normalized Iranian phone pattern: 09 followed by 9 digits
_PHONE_RE = re.compile(r"^09\d{9}$")

SESSION_COOKIE_NAME = "bandeh_session"
# 30 days session lifetime in seconds
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _load_security_config() -> tuple[str, bool]:
    """Load signing secret key and cookie secure flag from env_settings or environment."""
    try:
        from env_settings import get_settings

        settings = get_settings()
        secret_key = settings.secret_key.get_secret_value()
        cookie_secure = settings.environment == "production"
        return secret_key, cookie_secure
    except Exception:
        secret_key = (
            os.environ.get("SECRET_KEY")
            or os.environ.get("BANDEH_SECRET_KEY")
            or "dev-only-insecure-key-do-not-use-in-production"
        )
        cookie_secure = os.environ.get("BANDEH_COOKIE_SECURE", "0") == "1"
        return secret_key, cookie_secure


_SECRET_KEY, COOKIE_SECURE = _load_security_config()


def _get_serializer() -> URLSafeTimedSerializer:
    """Return serializer for cryptographically signing and verifying session cookies."""
    return URLSafeTimedSerializer(_SECRET_KEY, salt="bandeh-session")


def create_session(
    response: Response,
    phone: str,
    session_token: str | None = None,
) -> None:
    """Store signed session payload in an HttpOnly, SameSite=Lax cookie."""
    payload: dict[str, str | int] = {
        "phone": phone,
        "login_at": int(time.time()),
    }
    if session_token:
        payload["session_token"] = session_token

    token = _get_serializer().dumps(payload)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )


def read_session_payload(request: Request) -> dict[str, str | int] | None:
    """Read and cryptographically verify the cookie payload."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        return None
    try:
        data = _get_serializer().loads(cookie, max_age=SESSION_MAX_AGE)
        if isinstance(data, dict):
            return data
    except (BadSignature, SignatureExpired):
        return None
    return None


def read_session(request: Request) -> str | None:
    """Read and verify the session cookie. Return verified phone number or None."""
    payload = read_session_payload(request)
    if not payload:
        return None
    phone = payload.get("phone")
    return phone if isinstance(phone, str) and _PHONE_RE.match(phone) else None


def read_session_token(request: Request) -> str | None:
    """Extract the database session token from verified session cookie."""
    payload = read_session_payload(request)
    if not payload:
        return None
    token = payload.get("session_token")
    return token if isinstance(token, str) else None


def delete_session(response: Response) -> None:
    """Instruct the client browser to drop the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)


def get_current_user(request: Request) -> str | None:
    """Fast dependency: returns the verified phone number from signed cookie or None."""
    return read_session(request)


# Dependency alias for optional phone identity
CurrentUserDep = Annotated[str | None, Depends(get_current_user)]


def require_user(phone: CurrentUserDep) -> str:
    """Dependency: ensures verified phone exists, or redirects to /auth/login."""
    if phone is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return phone


# Dependency alias for protected phone routes
RequireUserDep = Annotated[str, Depends(require_user)]


async def get_current_db_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User | None:
    """Full DB-backed dependency: verifies session token against PostgreSQL."""
    session_token = read_session_token(request)
    if session_token:
        from services.session_service import get_valid_session

        session_record = await get_valid_session(db, session_token)
        if session_record and session_record.user:
            return session_record.user

    # Fallback to phone lookup in DB if no session token stored yet
    phone = read_session(request)
    if phone:
        from services.user_service import get_user_by_phone

        return await get_user_by_phone(db, phone)

    return None


CurrentDbUserDep = Annotated[User | None, Depends(get_current_db_user)]


async def require_authenticated_user(
    request: Request,
    user: CurrentDbUserDep,
) -> User:
    """Dependency: requires an active registered user entity in PostgreSQL."""
    if user is None:
        # Check if incoming request expects JSON/API or HTML page
        accept_header = request.headers.get("accept", "")
        if "text/html" in accept_header:
            raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
        raise HTTPException(status_code=401, detail="ورود به حساب کاربری الزامی است.")
    return user


RequireDbUserDep = Annotated[User, Depends(require_authenticated_user)]


def require_roles(*allowed_roles: UserRole) -> Callable:
    """Role-based authorization dependency factory (RBAC)."""

    async def _role_guard(user: RequireDbUserDep) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"شما دسترسی مجاز ندارید. نقش‌های مجاز: {[r.value for r in allowed_roles]}",
            )
        return user

    return _role_guard


RequireAdminDep = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
RequireStaffDep = Annotated[User, Depends(require_roles(UserRole.STAFF, UserRole.ADMIN))]


__all__ = [
    "COOKIE_SECURE",
    "CurrentDbUserDep",
    "CurrentUserDep",
    "RequireAdminDep",
    "RequireDbUserDep",
    "RequireStaffDep",
    "RequireUserDep",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "create_session",
    "delete_session",
    "get_current_db_user",
    "get_current_user",
    "read_session",
    "read_session_payload",
    "read_session_token",
    "require_authenticated_user",
    "require_roles",
    "require_user",
]
