"""SMS delivery adapter.

Integrates with SMS gateway providers (e.g., Kavenegar, Ghasedak).
In development, OTP codes are logged to stdout.
"""

from __future__ import annotations

# Maps phone -> last sent code for local development and automated testing
_DEV_LAST_CODE: dict[str, str] = {}


def send_otp(phone: str, code: str) -> None:
    """Deliver a 5-digit OTP verification code to the target phone number."""
    print(f"[DEV SMS] code for {phone}: {code}")
    _DEV_LAST_CODE[phone] = code


__all__ = ["_DEV_LAST_CODE", "send_otp"]

