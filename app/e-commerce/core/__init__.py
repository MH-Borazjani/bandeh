"""Core module: security, session management, cookies, and templates."""

from .cookies import (
    CART_COOKIE_MAX_AGE,
    CART_COOKIE_NAME,
    delete_cart_cookie,
    ensure_cart_id,
)
from .security import (
    COOKIE_SECURE,
    CurrentUserDep,
    RequireUserDep,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    create_session,
    delete_session,
    get_current_user,
    read_session,
    require_user,
)
from .templates import TEMPLATES_DIR, templates

__all__ = [
    "CART_COOKIE_MAX_AGE",
    "CART_COOKIE_NAME",
    "COOKIE_SECURE",
    "CurrentUserDep",
    "RequireUserDep",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "TEMPLATES_DIR",
    "create_session",
    "delete_cart_cookie",
    "delete_session",
    "ensure_cart_id",
    "get_current_user",
    "read_session",
    "require_user",
    "templates",
]

