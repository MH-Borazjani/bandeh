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
"""Business services module."""

from .auth_service import (
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_REQUESTS,
    OTP_REQUEST_WINDOW,
    OTP_RESEND_COOLDOWN,
    OTP_TTL_SECONDS,
    OtpError,
    mask_phone,
    normalize_phone,
    request_otp,
    reset_auth_state,
    to_ascii_digits,
    verify_otp,
)
from .session_service import (
    create_database_session,
    generate_session_token,
    get_valid_session,
    revoke_all_user_sessions,
    revoke_session,
)
from .user_service import (
    get_or_create_user_by_phone,
    get_user_by_id,
    get_user_by_phone,
    record_user_login,
)

__all__ = [
    "OTP_LENGTH",
    "OTP_MAX_ATTEMPTS",
    "OTP_MAX_REQUESTS",
    "OTP_REQUEST_WINDOW",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL_SECONDS",
    "OtpError",
    "create_database_session",
    "generate_session_token",
    "get_or_create_user_by_phone",
    "get_user_by_id",
    "get_user_by_phone",
    "get_valid_session",
    "mask_phone",
    "normalize_phone",
    "record_user_login",
    "request_otp",
    "reset_auth_state",
    "revoke_all_user_sessions",
    "revoke_session",
    "to_ascii_digits",
    "verify_otp",
]

