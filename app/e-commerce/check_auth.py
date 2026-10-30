"""Runnable self-check for auth.py + sms.py + the auth routes.

Run from app/e-commerce:

    python check_auth.py

No server, no database, no real SMS: OTP records live in memory, the
dev SMS backend keeps the last code in-process, and the HTTP flow is
exercised with FastAPI's TestClient (in-process, no network).

What is checked, in order:
 1-2.  Phone normalisation (Latin/+98/0098/98 forms, Persian and Arabic
        digits, spaces/dashes) and rejection of bad numbers.
 3-6.  OTP: happy path (hash stored, never plaintext; code works once),
        5 wrong guesses kills the code, expiry kills the code,
        resend cooldown and the 3-per-10-minutes rate limit.
 7-9.  Sessions: cookie flags, signature verifies, tampered cookie
        rejected, logout deletes the cookie.
 10-17. Full HTTP flow with TestClient: login page -> request code ->
        wrong code rejected -> right code logs in (HX-Redirect +
        session cookie) -> /auth/account shows the masked number ->
        strangers are bounced to /auth/login -> logout ends it.
"""

import time

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

import auth
import sms
from auth import (
    OTP_MAX_ATTEMPTS,
    SESSION_COOKIE_NAME,
    OtpError,
    create_session,
    delete_session,
    normalize_phone,
    read_session,
    request_otp,
    reset_auth_state,
    verify_otp,
)
from auth_routes import router as auth_router


def _request_with_cookie(cookie_value: str) -> Request:
    """A fake incoming request carrying our session cookie."""
    scope = {
        "type": "http",
        "method": "GET",
        "headers": [(b"cookie", f"{SESSION_COOKIE_NAME}={cookie_value}".encode())],
    }
    return Request(scope)


def _cookie_value(set_cookie_header: str) -> str:
    """Pull `bandeh_session=<token>` out of a Set-Cookie header."""
    return set_cookie_header.split(";")[0].split("=", 1)[1]


def main() -> None:
    reset_auth_state()
    phone = "09123456789"

    # 1. Every common spelling of an Iranian mobile number -> 09xxxxxxxxx.
    spellings = {
        "09123456789": phone,
        "+989123456789": phone,
        "00989123456789": phone,
        "989123456789": phone,
        "۰۹۱۲۳۴۵۶۷۸۹": phone,  # Persian digits
        "٠٩١٢٣٤٥٦٧٨٩": phone,  # Arabic digits
        "0912 345 6789": phone,  # spaces
        "0912-345-6789": phone,  # dashes
    }
    for raw, expected in spellings.items():
        assert normalize_phone(raw) == expected, raw

    # 2. Garbage in -> clear Persian error out.
    for bad in ["12345", "0912345678", "091234567890", "abc", "", "02122334455"]:
        try:
            normalize_phone(bad)
        except ValueError as exc:
            assert "موبایل" in str(exc), bad
        else:
            raise AssertionError(f"accepted bad number: {bad!r}")

    # 3. Happy path: request -> hash stored (never plaintext) -> verify once.
    reset_auth_state()
    assert request_otp("۰۹۱۲۳۴۵۶۷۸۹") == phone  # Persian digits accepted
    record = auth._otp_records[phone]
    code = sms._DEV_LAST_CODE[phone]
    assert len(code) == 5 and code.isdigit(), code
    assert record.code_hash != code, "code stored in plaintext!"
    assert len(record.code_hash) == 64, "expected a SHA256 hex digest"
    assert verify_otp(phone, code) is True
    assert phone not in auth._otp_records, "code was not consumed"
    assert verify_otp(phone, code) is False, "code worked twice!"

    # 4. Persian-digit code typed by the user also verifies.
    reset_auth_state()
    request_otp(phone)
    code = sms._DEV_LAST_CODE[phone]
    fa_code = "".join("۰۱۲۳۴۵۶۷۸۹"[int(d)] for d in code)
    assert verify_otp(phone, fa_code) is True

    # 5. Five wrong guesses -> the code dies, even the right one is useless.
    reset_auth_state()
    request_otp(phone)
    code = sms._DEV_LAST_CODE[phone]
    for _ in range(OTP_MAX_ATTEMPTS):
        assert verify_otp(phone, "00000") is False
    assert phone not in auth._otp_records, "code survived 5 wrong guesses"
    assert verify_otp(phone, code) is False

    # 6. An expired code is rejected and cleaned up.
    reset_auth_state()
    request_otp(phone)
    code = sms._DEV_LAST_CODE[phone]
    auth._otp_records[phone].expires_at = time.time() - 1  # pretend 2 min passed
    assert verify_otp(phone, code) is False
    assert phone not in auth._otp_records

    # 7. Resend cooldown: asking twice in a row is refused, politely.
    reset_auth_state()
    request_otp(phone)
    try:
        request_otp(phone)
    except OtpError as exc:
        assert "ثانیه" in str(exc), str(exc)
    else:
        raise AssertionError("cooldown not enforced")

    # 8. Rate limit: at most 3 codes per 10 minutes per number.
    reset_auth_state()
    now = time.time()
    auth._otp_request_times[phone] = [now - 500, now - 400, now - 300]
    try:
        request_otp(phone)
    except OtpError as exc:
        assert "ده دقیقه" in str(exc), str(exc)
    else:
        raise AssertionError("rate limit not enforced")

    # 9. Session cookie: set with the right flags...
    response = Response()
    create_session(response, phone)
    header = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE_NAME}=" in header, header
    assert "httponly" in header.lower(), header
    assert "samesite=lax" in header.lower(), header
    token = _cookie_value(header)

    # 10. ...the signature verifies and yields the phone number...
    assert read_session(_request_with_cookie(token)) == phone

    # 11. ...a tampered cookie is rejected like it never existed...
    tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
    assert read_session(_request_with_cookie(tampered)) is None
    assert read_session(_request_with_cookie("")) is None

    # 12. ...and logout deletes the cookie.
    response = Response()
    delete_session(response)
    header = response.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in header, header
    assert "max-age=0" in header.lower(), header

    # 13-17. Full HTTP flow through the real routes.
    flow_app = FastAPI()
    flow_app.include_router(auth_router)
    client = TestClient(flow_app)

    # 13. The login page renders.
    r = client.get("/auth/login")
    assert r.status_code == 200 and "شماره موبایل" in r.text, r.status_code

    # 14. Bad number -> the same form comes back with a Persian error.
    r = client.post("/auth/otp/request", data={"phone": "123"})
    assert r.status_code == 200 and "معتبر نیست" in r.text, r.text[:200]

    # 15. Good number -> the code form comes back.
    reset_auth_state()
    r = client.post("/auth/otp/request", data={"phone": phone})
    assert r.status_code == 200 and "کد ۵ رقمی" in r.text, r.text[:200]
    code = sms._DEV_LAST_CODE[phone]

    # 16. Wrong code -> error, and crucially NO session cookie.
    r = client.post("/auth/otp/verify", data={"phone": phone, "code": "00000"})
    assert r.status_code == 200 and "اشتباه" in r.text, r.text[:200]
    assert SESSION_COOKIE_NAME not in r.headers.get("set-cookie", "")

    # 17. Right code -> HX-Redirect to the account page + session cookie.
    r = client.post("/auth/otp/verify", data={"phone": phone, "code": code})
    assert r.status_code == 200, r.status_code
    assert r.headers.get("hx-redirect") == "/auth/account", dict(r.headers)
    assert f"{SESSION_COOKIE_NAME}=" in r.headers["set-cookie"]

    # 18. The account page shows the masked number (cookie kept by client).
    r = client.get("/auth/account")
    assert r.status_code == 200 and "0912" in r.text, r.status_code

    # 19. A stranger (no cookie) is bounced to the login page.
    stranger = TestClient(flow_app)
    r = stranger.get("/auth/account", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert r.headers["location"] == "/auth/login", r.headers["location"]

    # 20. Logout deletes the cookie; the account page bounces again.
    r = client.post("/auth/logout", follow_redirects=False)
    assert r.status_code == 303, r.status_code
    assert "max-age=0" in r.headers.get("set-cookie", "").lower()
    r = client.get("/auth/account", follow_redirects=False)
    assert r.status_code == 303, r.status_code

    print("All auth checks passed.")


if __name__ == "__main__":
    main()
