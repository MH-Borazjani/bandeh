"""Phone-OTP login with cookie sessions: the Auth topic applied to Bandeh.

The whole login story in one paragraph: Iranian shops (Digikala and the
rest) don't ask for a password -- the user types their mobile number, the
shop texts them a short code, they type the code back, and they're in.
That is exactly what this module does:

1. ``normalize_phone`` -- accept the many ways Iranians write a mobile
   number (``0912...``, ``+98912...``, Persian digits ``۰۹۱۲...``) and
   turn them all into one canonical form: ``09xxxxxxxxx``.
2. ``request_otp`` -- make a random 5-digit code, remember only its
   SHA256 hash for 120 seconds, and hand it to ``sms.send_otp``.
   Rate limits stop attackers from spamming a number or guessing codes.
3. ``verify_otp`` -- check the code the user typed. Right code: the
   one-time record is deleted (a code works exactly once) and the caller
   logs the user in. Wrong code 5 times: the record dies too.
4. ``create_session`` / ``read_session`` / ``delete_session`` -- after a
   successful login the server signs the phone number with its secret key
   (itsdangerous) and stores it in an HttpOnly cookie named
   ``bandeh_session``. Every later request carries that cookie, so the
   server knows who is asking without any database lookup. Signing means
   the user cannot forge or edit the cookie: any tampering invalidates
   the signature and the cookie is treated as absent.
5. ``get_current_user`` / ``require_user`` -- FastAPI dependencies that
   read the session cookie. Pages use them to know who is logged in, or
   to bounce strangers back to ``/login``.

Deliberately NOT here (later topics, or explicit non-goals):
- No passwords anywhere. Phone + SMS code IS the authentication.
- No database. OTP records live in plain dicts in memory, and the
  session lives inside the signed cookie itself. This keeps the auth
  topic focused: the database topic will add a users table later.
  Upgrade path: the OTP dicts are the only thing that would move to
  Redis (``redis_host`` already exists in settings for exactly this).
- No JWT-in-header. The JWT lesson taught header tokens; Bandeh uses
  cookies because browsers send them automatically -- no JavaScript
  needed, which keeps the HTMX frontend dead simple.
- No "log out everywhere" / session revocation list. Logging out deletes
  the cookie on this browser. Revoking other browsers needs a
  server-side session store -- a documented future step, not v1.

Security notes, stated plainly:
- OTP codes are stored as SHA256 hashes, never plaintext. If someone
  dumps the server's memory, they get hashes, not usable codes.
- Code comparison uses ``hmac.compare_digest`` (constant time), so an
  attacker cannot guess the code one digit at a time by measuring
  response speed.
- The session cookie is HttpOnly (JavaScript cannot steal it) and
  SameSite=Lax (other sites cannot ride on it), exactly like the cart
  cookie in ``cookies.py``.
- ``secure`` is False in development because local servers run over
  plain http and browsers refuse to send ``Secure`` cookies over http.
  In production, behind HTTPS, set ``BANDEH_COOKIE_SECURE=1``.
  Never ship production with ``secure=False``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from sms import send_otp

# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------

# Persian (۰۱۲۳۴۵۶۷۸۹) and Arabic (٠١٢٣٤٥٦٧٨٩) digits, in order, so index i
# of each string is the same digit as str(i). Users often type these on a
# Persian keyboard; we translate them instead of rejecting the number.
_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

# After normalisation every valid number looks like this: 09 + 9 digits.
_PHONE_RE = re.compile(r"^09\d{9}$")


def _to_ascii_digits(text: str) -> str:
    """Replace Persian/Arabic digits with 0-9. Everything else untouched."""
    out = text
    for i in range(10):
        out = out.replace(_PERSIAN_DIGITS[i], str(i))
        out = out.replace(_ARABIC_DIGITS[i], str(i))
    return out


def normalize_phone(raw: str) -> str:
    """Turn any common spelling of an Iranian mobile number into 09xxxxxxxxx.

    Accepted: ``09123456789``, ``+989123456789``, ``00989123456789``,
    ``989123456789``, Persian/Arabic digits, spaces and dashes anywhere.
    Anything else raises ``ValueError`` with a Persian message, because
    this error is shown directly to the user in the login form.
    """
    digits = _to_ascii_digits(raw or "")
    # People paste numbers with spaces/dashes: "0912 345 6789".
    digits = digits.replace(" ", "").replace("-", "")

    # +989123456789 -> drop "+98", put back the trunk "0": 09123456789.
    # (The national number after +98 never starts with 0.)
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
    """Hide the middle of the number for display: 0912••• ••89.

    Shown on the account page so the user can recognise their own number
    without the full number sitting on the screen.
    """
    return f"{phone[:4]} ••• ••{phone[-2:]}"


# ---------------------------------------------------------------------------
# One-time codes (OTP)
# ---------------------------------------------------------------------------

OTP_LENGTH = 5  # 5 digits: short enough to type, long enough to not guess.
OTP_TTL_SECONDS = 120  # a code lives 2 minutes, then it is dead.
OTP_RESEND_COOLDOWN = 60  # must wait 60s before asking for a new code.
OTP_MAX_REQUESTS = 3  # ...and at most 3 codes per 10 minutes per number.
OTP_REQUEST_WINDOW = 600
OTP_MAX_ATTEMPTS = 5  # 5 wrong guesses kills the code.


class OtpError(Exception):
    """A login-code problem we can show to the user (Persian message)."""


@dataclass
class _OtpRecord:
    """One outstanding login code. Only the hash is stored, never the code."""

    code_hash: str
    expires_at: float
    attempts_left: int = OTP_MAX_ATTEMPTS


# phone -> outstanding code. In-memory on purpose (see module docstring):
# the database topic comes later; Redis is the natural upgrade.
_otp_records: dict[str, _OtpRecord] = {}
# phone -> timestamps of recent code requests, for the rate limit.
_otp_request_times: dict[str, list[float]] = {}


def reset_auth_state() -> None:
    """Forget all OTP records and rate-limit history. Only for tests.

    ``check_auth.py`` calls this between test sections so each section
    starts from a clean slate, exactly like a freshly started server.
    """
    _otp_records.clear()
    _otp_request_times.clear()


def _hash_code(code: str) -> str:
    """SHA256 of the code. Hashing means a memory dump leaks no usable code."""
    return hashlib.sha256(code.encode()).hexdigest()


def _prune_request_times(phone: str, now: float) -> list[float]:
    """Drop request timestamps older than the rate-limit window."""
    recent = [t for t in _otp_request_times.get(phone, []) if now - t < OTP_REQUEST_WINDOW]
    _otp_request_times[phone] = recent
    return recent


def request_otp(raw_phone: str) -> str:
    """Create a login code for the phone number and send it by SMS.

    Returns the normalised phone number. Raises ``ValueError`` for a bad
    number and ``OtpError`` when the user must wait (cooldown) or asked
    too often (rate limit). Both carry Persian messages for the form.
    """
    phone = normalize_phone(raw_phone)
    now = time.time()

    recent = _prune_request_times(phone, now)
    if recent and now - recent[-1] < OTP_RESEND_COOLDOWN:
        wait = int(OTP_RESEND_COOLDOWN - (now - recent[-1]))
        raise OtpError(f"کد ارسال شد؛ لطفاً {wait} ثانیه دیگر دوباره تلاش کن")
    if len(recent) >= OTP_MAX_REQUESTS:
        raise OtpError("تعداد درخواست کد زیاد شد؛ ده دقیقه دیگر تلاش کن")

    # secrets is the right tool for security codes: unlike random, it is
    # unpredictable. :05d keeps leading zeros, so "04213" stays 5 digits.
    code = f"{secrets.randbelow(100000):05d}"
    _otp_records[phone] = _OtpRecord(
        code_hash=_hash_code(code),
        expires_at=now + OTP_TTL_SECONDS,
    )
    recent.append(now)

    send_otp(phone, code)
    return phone


def verify_otp(raw_phone: str, code: str) -> bool:
    """Check the code the user typed. True = correct, log them in.

    A code works exactly once: a correct code deletes its record, and 5
    wrong guesses (or 120 seconds passing) kill it too. Returns False for
    every failure -- the caller shows a generic "wrong code" message so an
    attacker learns nothing about which part failed.
    """
    try:
        phone = normalize_phone(raw_phone)
    except ValueError:
        return False

    record = _otp_records.get(phone)
    if record is None:
        return False  # no code was ever requested for this number
    if time.time() > record.expires_at:
        del _otp_records[phone]
        return False  # too slow: the code expired

    # compare_digest runs in constant time: it always takes the same
    # amount of time, so timing the response reveals nothing about the
    # code. Plain == would leak digit-by-digit timing info.
    if hmac.compare_digest(record.code_hash, _hash_code(_to_ascii_digits(code.strip()))):
        del _otp_records[phone]  # one-time means one-time
        return True

    record.attempts_left -= 1
    if record.attempts_left <= 0:
        del _otp_records[phone]  # too many guesses: start over
    return False


# ---------------------------------------------------------------------------
# Cookie sessions
# ---------------------------------------------------------------------------

SESSION_COOKIE_NAME = "bandeh_session"
# 30 days: Iranian shops keep you logged in, and so do we. The signature
# itself also expires after 30 days, so an old stolen cookie dies on its
# own even if the browser kept it.
SESSION_MAX_AGE = 60 * 60 * 24 * 30

# secure=False in development because local servers run over plain http
# and browsers never send Secure cookies over http (same rule as the cart
# cookie in cookies.py). Production MUST set BANDEH_COOKIE_SECURE=1.
COOKIE_SECURE = os.environ.get("BANDEH_COOKIE_SECURE", "0") == "1"

# The key that signs session cookies. Anyone with this key can forge
# sessions, so production MUST set BANDEH_SECRET_KEY to a long random
# value (32+ characters). The fallback below is deliberately weak and is
# only here so `fastapi dev` works out of the box on a laptop.
_SECRET_KEY = os.environ.get("BANDEH_SECRET_KEY") or "dev-only-insecure-key-do-not-use-in-production"


def _serializer() -> URLSafeTimedSerializer:
    """The signer. Same secret in = same signature out; itsdangerous
    raises BadSignature if even one character of the cookie was changed."""
    return URLSafeTimedSerializer(_SECRET_KEY, salt="bandeh-session")


def create_session(response: Response, phone: str) -> None:
    """Log the user in: put a signed session cookie on the response.

    The cookie holds ``{"phone": ..., "login_at": ...}`` -- just enough to
    know who this is. The signature is the whole security: the server
    trusts the cookie ONLY if the signature checks out.
    """
    token = _serializer().dumps({"phone": phone, "login_at": int(time.time())})
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,  # JavaScript cannot read it: XSS can't steal it.
        samesite="lax",  # other sites can't ride on it: CSRF protection.
        secure=COOKIE_SECURE,
    )


def read_session(request: Request) -> str | None:
    """Who is making this request? The phone number, or None.

    Returns None for every failure mode (no cookie, forged cookie,
    expired signature) -- deliberately the same answer, so an attacker
    can't tell the cases apart.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        # max_age re-checks the signature's age: a cookie older than 30
        # days is rejected even if its signature is otherwise valid.
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    phone = data.get("phone")
    return phone if isinstance(phone, str) and _PHONE_RE.match(phone) else None


def delete_session(response: Response) -> None:
    """Log the user out: tell the browser to drop the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME)


# ---------------------------------------------------------------------------
# FastAPI dependencies (official FastAPI skill style: Annotated aliases)
# ---------------------------------------------------------------------------


def get_current_user(request: Request) -> str | None:
    """The logged-in phone number, or None. For pages that adapt to guests."""
    return read_session(request)


# Reusable alias: `phone: CurrentUserDep` in a route signature.
CurrentUserDep = Annotated[str | None, Depends(get_current_user)]


def require_user(phone: CurrentUserDep) -> str:
    """The logged-in phone number, guaranteed. For protected pages.

    Not logged in? Raise a 303 redirect to /auth/login instead of a 401:
    a browser that followed a link to a members-only page should land on
    the login form, not stare at an error code. HTTPException carries the
    status and the Location header; Starlette turns it into the response.
    """
    if phone is None:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    return phone


# Reusable alias: `phone: RequireUserDep` protects the whole route.
RequireUserDep = Annotated[str, Depends(require_user)]
