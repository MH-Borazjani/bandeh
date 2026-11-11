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
"""Authentication and phone OTP business logic.

Handles:
- Iranian mobile number normalization and masking
- Cryptographic OTP generation (secrets module)
- SHA-256 code hashing and constant-time comparison (hmac.compare_digest)
- Rate limiting and cooldown enforcement
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

try:
    from integrations.sms import send_otp
except ImportError:
    from sms import send_otp

# Persian (۰-۹) and Arabic (٠-٩) digits mapped to standard ASCII 0-9
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_PHONE_RE = re.compile(r"^09\d{9}$")


def to_ascii_digits(text: str) -> str:
    """Convert Persian and Arabic digit characters to ASCII 0-9."""
    out = text
    for i in range(10):
        out = out.replace(_PERSIAN_DIGITS[i], str(i))
        out = out.replace(_ARABIC_DIGITS[i], str(i))
    return out


def normalize_phone(raw: str) -> str:
    """Normalize any standard Iranian phone number format into canonical 09xxxxxxxxx.

    Supports: 0912..., +98912..., 0098912..., 98912..., Persian/Arabic digits,
    and formats with spaces or hyphens.
    """
    digits = to_ascii_digits(raw or "").replace(" ", "").replace("-", "")

    if digits.startswith("+98"):
        digits = "0" + digits[3:]
    elif digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]

    if not _PHONE_RE.match(digits):
        raise ValueError("شماره موبایل معتبر نیست. مثال: 09123456789")
    return digits


def mask_phone(phone: str) -> str:
    """Mask phone number for safe display: e.g. 0912••• ••89."""
    return f"{phone[:4]} ••• ••{phone[-2:]}"


OTP_LENGTH = 5
OTP_TTL_SECONDS = 120
OTP_RESEND_COOLDOWN = 60
OTP_MAX_REQUESTS = 3
OTP_REQUEST_WINDOW = 600
OTP_MAX_ATTEMPTS = 5


class OtpError(Exception):
    """Domain exception raised when an OTP rule (rate limit, cooldown) is violated."""


@dataclass
class _OtpRecord:
    """Stored hash of an active OTP code."""

    code_hash: str
    expires_at: float
    attempts_left: int = OTP_MAX_ATTEMPTS


_otp_records: dict[str, _OtpRecord] = {}
_otp_request_times: dict[str, list[float]] = {}


def reset_auth_state() -> None:
    """Reset in-memory OTP records and rate limit histories (used for tests)."""
    _otp_records.clear()
    _otp_request_times.clear()


def _hash_code(code: str) -> str:
    """Compute SHA-256 hash of the plain OTP code."""
    return hashlib.sha256(code.encode()).hexdigest()


def _prune_request_times(phone: str, now: float) -> list[float]:
    """Filter out request timestamps older than the sliding window."""
    recent = [t for t in _otp_request_times.get(phone, []) if now - t < OTP_REQUEST_WINDOW]
    _otp_request_times[phone] = recent
    return recent


def request_otp(raw_phone: str) -> str:
    """Generate and dispatch an OTP code to the requested phone number."""
    phone = normalize_phone(raw_phone)
    now = time.time()

    recent = _prune_request_times(phone, now)
    if recent and now - recent[-1] < OTP_RESEND_COOLDOWN:
        wait = int(OTP_RESEND_COOLDOWN - (now - recent[-1]))
        raise OtpError(f"کد ارسال شد؛ لطفاً {wait} ثانیه دیگر دوباره تلاش کن")
    if len(recent) >= OTP_MAX_REQUESTS:
        raise OtpError("تعداد درخواست کد زیاد شد؛ ده دقیقه دیگر تلاش کن")

    # Generate cryptographically secure random 5-digit code
    code = f"{secrets.randbelow(100000):05d}"
    _otp_records[phone] = _OtpRecord(
        code_hash=_hash_code(code),
        expires_at=now + OTP_TTL_SECONDS,
    )
    recent.append(now)

    send_otp(phone, code)
    return phone


def verify_otp(raw_phone: str, code: str) -> bool:
    """Verify submitted code. Constant-time comparison protects against timing attacks."""
    try:
        phone = normalize_phone(raw_phone)
    except ValueError:
        return False

    record = _otp_records.get(phone)
    if record is None:
        return False
    if time.time() > record.expires_at:
        del _otp_records[phone]
        return False

    if hmac.compare_digest(record.code_hash, _hash_code(to_ascii_digits(code.strip()))):
        del _otp_records[phone]
        return True

    record.attempts_left -= 1
    if record.attempts_left <= 0:
        del _otp_records[phone]
    return False


__all__ = [
    "OTP_LENGTH",
    "OTP_MAX_ATTEMPTS",
    "OTP_MAX_REQUESTS",
    "OTP_REQUEST_WINDOW",
    "OTP_RESEND_COOLDOWN",
    "OTP_TTL_SECONDS",
    "OtpError",
    "mask_phone",
    "normalize_phone",
    "request_otp",
    "reset_auth_state",
    "to_ascii_digits",
    "verify_otp",
]

