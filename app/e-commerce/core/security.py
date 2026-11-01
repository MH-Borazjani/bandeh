"""Security utilities: signed session cookies and FastAPI auth dependencies.

Session tokens are signed with itsdangerous (HMAC-SHA256). The signature
guarantees that clients cannot tamper with the stored phone number.
"""

from __future__ import annotations

import os
import re
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Normalized phone pattern: 09 followed by 9 digits
_PHONE_RE = re.compile(r"^09\d{9}$")

SESSION_COOKIE_NAME = "bandeh_session"
# 30 days session lifetime
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _load_security_config() -> tuple[str, bool]:
    """Load the signing secret key and cookie secure flag from env_settings or environment."""
    try:
        from env_settings import get_settings

        settings = get_settings()
        secret_key = settings.secret_key.get_secret_value()
        cookie_secure = settings.environment == "production"
        return secret_key, cookie_secure
    except Exception:
        # Fallback for lightweight testing or environments without a full .env file
        secret_key = (
            os.environ.get("SECRET_KEY")
            or os.environ.get("BANDEH_SECRET_KEY")
            or "dev-only-insecure-key-do-not-use-in-production"
        )
        cookie_secure = os.environ.get("BANDEH_COOKIE_SECURE", "0") == "1"
        return secret_key, cookie_secure


_SECRET_KEY, COOKIE_SECURE = _load_security_config()


def _get_serializer() -> URLSafeTimedSerializer:
    """Return serializer for signing and verifying session cookies."""
    return URLSafeTimedSerializer(_SECRET_KEY, salt="bandeh-session")


def create_session(response: Response, phone: str) -> None:
    """Store signed session data in an HttpOnly, SameSite=Lax cookie."""
    token = _get_serializer().dumps({"phone": phone, "login_at": int(time.time())})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
    )


def read_session(request: Request) -> str | None:
    """Read and verify the session cookie. Return the verified phone number or None."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

    phone = data.get("phone")
    return phone if isinstance(phone, str) and _PHONE_RE.match(phone) else None


def delete_session(response: Response) -> None:
    """Instruct the browser to drop the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)


def get_current_user(request: Request) -> str | None:
    """Dependency: returns the currently logged-in user's phone, or None."""
    return read_session(request)


# Reusable dependency alias for optional authentication
CurrentUserDep = Annotated[str | None, Depends(get_current_user)]


def require_user(phone: CurrentUserDep) -> str:
    """Dependency: guarantees a logged-in user or redirects to /auth/login."""
    if phone is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return phone


# Reusable dependency alias for protected routes
RequireUserDep = Annotated[str, Depends(require_user)]


__all__ = [
    "COOKIE_SECURE",
    "CurrentUserDep",
    "RequireUserDep",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "create_session",
    "delete_session",
    "get_current_user",
    "read_session",
    "require_user",
]

