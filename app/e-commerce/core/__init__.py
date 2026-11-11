# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Core module: security, session management, cookies, and templates."""

from .cookies import (
    CART_COOKIE_MAX_AGE,
    CART_COOKIE_NAME,
    delete_cart_cookie,
    ensure_cart_id,
)
from .security import (
    COOKIE_SECURE,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    CurrentDbUserDep,
    CurrentUserDep,
    DbSessionDep,
    RequireAdminDep,
    RequireDbUserDep,
    RequireStaffDep,
    RequireUserDep,
    create_session,
    delete_session,
    get_current_db_user,
    get_current_user,
    read_session,
    read_session_token,
    require_authenticated_user,
    require_roles,
    require_user,
)
from .templates import TEMPLATES_DIR, templates

__all__ = [
    "CART_COOKIE_MAX_AGE",
    "CART_COOKIE_NAME",
    "COOKIE_SECURE",
    "CurrentDbUserDep",
    "CurrentUserDep",
    "DbSessionDep",
    "RequireAdminDep",
    "RequireDbUserDep",
    "RequireStaffDep",
    "RequireUserDep",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "TEMPLATES_DIR",
    "create_session",
    "delete_cart_cookie",
    "delete_session",
    "ensure_cart_id",
    "get_current_db_user",
    "get_current_user",
    "read_session",
    "read_session_token",
    "require_authenticated_user",
    "require_roles",
    "require_user",
    "templates",
]
