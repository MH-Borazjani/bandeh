"""Phone-OTP login and cookie sessions.

Note: Core security and service logic has been organized into:
- ``core.security``: session signing, cookie manipulation, FastAPI dependencies.
- ``services.auth_service``: phone normalization, OTP state, rate limiting, and verification.

This module re-exports them for backwards compatibility.
"""

from core.security import (
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

__all__ = [
    "COOKIE_SECURE",
    "CurrentUserDep",
    "OTP_LENGTH",
    "OTP_MAX_ATTEMPTS",
    "OTP_MAX_REQUESTS",
    "OTP_REQUEST_WINDOW",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL_SECONDS",
    "OtpError",
    "RequireUserDep",
    "SESSION_COOKIE_NAME",
    "SESSION_MAX_AGE",
    "_hash_code",
    "_otp_records",
    "_otp_request_times",
    "create_session",
    "delete_session",
    "get_current_user",
    "mask_phone",
    "normalize_phone",
    "read_session",
    "request_otp",
    "require_user",
    "reset_auth_state",
    "verify_otp",
]
