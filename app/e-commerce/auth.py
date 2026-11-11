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
"""Phone-OTP login, database sessions, and role-based authorization.

Note: Core security and service logic has been organized into:
- ``core.security``: session signing, cookie manipulation, DB user/session dependencies, RBAC.
- ``services.auth_service``: phone normalization, OTP state, rate limiting, and verification.
- ``services.session_service``: database session persistence and revocation.
- ``services.user_service``: user account retrieval and creation.
- ``db.models``: User, UserSession, UserRole models.

This module re-exports them for backwards compatibility.
"""

from core.security import (
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
from db.models.session import UserSession
from db.models.user import User, UserRole
from services.auth_service import (
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_REQUESTS,
    OTP_REQUEST_WINDOW,
    OTP_RESEND_COOLDOWN,
    OTP_TTL_SECONDS,
    OtpError,
    _hash_code,
    _otp_records,
    _otp_request_times,
    mask_phone,
    normalize_phone,
    request_otp,
    reset_auth_state,
    verify_otp,
)
from services.session_service import (
    create_database_session,
    get_valid_session,
    revoke_all_user_sessions,
    revoke_session,
)
from services.user_service import (
    get_or_create_user_by_phone,
    get_user_by_id,
    get_user_by_phone,
)

__all__ = [
    "COOKIE_SECURE",
    "OTP_LENGTH",
    "OTP_MAX_ATTEMPTS",
    "OTP_MAX_REQUESTS",
    "OTP_REQUEST_WINDOW",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL_SECONDS",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "CurrentDbUserDep",
    "CurrentUserDep",
    "DbSessionDep",
    "OtpError",
    "RequireAdminDep",
    "RequireDbUserDep",
    "RequireStaffDep",
    "RequireUserDep",
    "User",
    "UserRole",
    "UserSession",
    "_hash_code",
    "_otp_records",
    "_otp_request_times",
    "create_database_session",
    "create_session",
    "delete_session",
    "get_current_db_user",
    "get_current_user",
    "get_or_create_user_by_phone",
    "get_user_by_id",
    "get_user_by_phone",
    "get_valid_session",
    "mask_phone",
    "normalize_phone",
    "read_session",
    "read_session_token",
    "request_otp",
    "require_authenticated_user",
    "require_roles",
    "require_user",
    "reset_auth_state",
    "revoke_all_user_sessions",
    "revoke_session",
    "verify_otp",
]
