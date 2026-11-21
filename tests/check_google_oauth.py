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
"""Runnable self-check for Google OAuth login. Run from repo root:

    python tests/check_google_oauth.py

In-memory SQLite + httpx MockTransport (no real Google). Covers:
login page shows the Google button only when configured; /auth/google
redirects to Google with client_id + state; callback with a bad state
is refused; a Google token error is refused; an unverified email is
refused; a new Google account asks for the phone once and the OTP step
links it; a returning Google account logs straight in; a Google account
already linked to another user never merges accounts.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.base import Base
from db.models.user import User, UserRole
from db.session import get_db
from integrations import sms as sms_module
from integrations.http_client import get_http_client
from main import app
from services.auth_service import request_otp

os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"


def make_transport(state: dict) -> httpx.MockTransport:
    """A fake Google: token exchange + userinfo, both scripted by state."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            if state.get("token_status", 200) != 200:
                return httpx.Response(
                    state["token_status"], json={"error": "invalid_grant"}
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-test",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if request.url.host == "openidconnect.googleapis.com":
            return httpx.Response(200, json=state["userinfo"])
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def cookie_value(response, name: str) -> str:
    """Pull one cookie's value out of a response's Set-Cookie headers."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list", None
    )
    assert callable(getlist)
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


async def main_async() -> None:
    state: dict = {
        "token_status": 200,
        "userinfo": {
            "sub": "google-sub-1",
            "email": "mamad@gmail.com",
            "email_verified": True,
            "given_name": "Mohammad",
            "family_name": "Borazjani",
        },
    }

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def test_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_get_db
    app.dependency_overrides[get_http_client] = lambda: httpx.AsyncClient(
        transport=make_transport(state)
    )
    client = TestClient(app, follow_redirects=False)

    # 1. Login page shows the Google button when configured.
    r = client.get("/auth/login")
    assert r.status_code == 200, r.status_code
    assert "/auth/google" in r.text and "ورود با گوگل" in r.text
    print("1. login page shows the Google button when configured")

    # 2. /auth/google redirects to Google with client_id + state cookie.
    r = client.get("/auth/google")
    assert r.status_code == 303, r.status_code
    location = r.headers["location"]
    assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    query = parse_qs(urlparse(location).query)
    assert query["client_id"] == ["test-client-id.apps.googleusercontent.com"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == [query["state"][0]] and len(query["state"][0]) >= 20
    state_cookie = cookie_value(r, "google_oauth_state")
    print("2. /auth/google redirects to Google with client_id + state")

    # 3. Callback with a bad state is refused.
    r = client.get("/auth/google/callback?code=abc&state=wrong-state")
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?error=google"
    print("3. callback with a bad state is refused")

    # 4. Callback with no code is refused.
    r = client.get(
        f"/auth/google/callback?state={query['state'][0]}"
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?error=google"
    print("4. callback with no code is refused")

    # 5. Google token error is refused.
    state["token_status"] = 400
    r = client.get(
        f"/auth/google/callback?code=bad-code&state={query['state'][0]}",
        cookies={"google_oauth_state": state_cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?error=google"
    state["token_status"] = 200
    print("5. Google token error is refused")

    # 6. Unverified email is refused.
    state["userinfo"] = dict(state["userinfo"], email_verified=False)
    r = client.get(
        f"/auth/google/callback?code=abc&state={query['state'][0]}",
        cookies={"google_oauth_state": state_cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?error=google"
    state["userinfo"] = dict(state["userinfo"], email_verified=True)
    print("6. unverified Google email is refused")

    # 7. New Google account: callback asks for the phone once.
    r = client.get(
        f"/auth/google/callback?code=abc&state={query['state'][0]}",
        cookies={"google_oauth_state": state_cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login?google=phone"
    pending_cookie = cookie_value(r, "google_oauth_pending")
    print("7. new Google account asks for the phone once")

    # 8. The OTP step links the Google identity to the phone user.
    phone = request_otp("09123456789")
    code = sms_module._DEV_LAST_CODE[phone]
    r = client.post(
        "/auth/otp/verify",
        data={"phone": phone, "code": code},
        cookies={"google_oauth_pending": pending_cookie},
    )
    assert r.status_code == 200, r.status_code
    assert r.headers.get("HX-Redirect") == "/auth/account"
    async with Session() as s:
        from sqlalchemy import select

        user = (
            await s.execute(select(User).where(User.phone == phone))
        ).scalar_one()
        assert user.google_sub == "google-sub-1"
        assert user.google_email == "mamad@gmail.com"
        assert user.first_name == "Mohammad"
    print("8. OTP step links the Google identity to the phone user")

    # 9. Returning Google account logs straight in (new state + callback).
    r = client.get("/auth/google")
    location = r.headers["location"]
    query = parse_qs(urlparse(location).query)
    state_cookie = cookie_value(r, "google_oauth_state")
    r = client.get(
        f"/auth/google/callback?code=abc&state={query['state'][0]}",
        cookies={"google_oauth_state": state_cookie},
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/account"
    assert "bandeh_session=" in str(r.headers.get("set-cookie", ""))
    print("9. returning Google account logs straight in")

    # 10. A Google account linked to another user never merges accounts:
    # a second phone tries to link the SAME google-sub-1; the OTP login
    # still succeeds, but the link is refused and user 1 keeps it.
    state["userinfo"] = dict(state["userinfo"], sub="google-sub-1")
    r = client.get("/auth/google")
    location = r.headers["location"]
    query = parse_qs(urlparse(location).query)
    state_cookie = cookie_value(r, "google_oauth_state")
    r = client.get(
        f"/auth/google/callback?code=abc&state={query['state'][0]}",
        cookies={"google_oauth_state": state_cookie},
    )
    assert r.headers["location"] == "/auth/account", r.headers["location"]
    # The callback logged in as user 1 directly (sub already known), so
    # force the pending path instead: a fresh sub-2 link works, then a
    # second phone cannot steal sub-1. Re-run the pending flow with
    # sub-1's identity forged into the pending cookie is not possible
    # (signed), so verify the rule at the service level instead.
    from services.oauth_service import link_google_account

    async with Session() as s:
        from sqlalchemy import select

        from integrations.google_oauth import GoogleUserInfo

        user1 = (
            await s.execute(select(User).where(User.phone == phone))
        ).scalar_one()
        user2 = User(phone="09129876543", role=UserRole.CUSTOMER, is_active=True)
        s.add(user2)
        await s.commit()
        try:
            await link_google_account(
                s,
                user2,
                GoogleUserInfo(
                    sub="google-sub-1",
                    email="mamad@gmail.com",
                    email_verified=True,
                ),
            )
            raise AssertionError("link to another user must fail")
        except ValueError:
            pass
        await s.refresh(user1)
        await s.refresh(user2)
        assert user1.google_sub == "google-sub-1"
        assert user2.google_sub is None
    print("10. Google accounts never merge two shop users")

    # 11. Without keys configured, the button hides and /auth/google bounces.
    del os.environ["GOOGLE_CLIENT_ID"]
    del os.environ["GOOGLE_CLIENT_SECRET"]
    client.cookies.clear()
    r = client.get("/auth/login")
    assert r.status_code == 200, r.status_code
    assert "/auth/google" not in r.text
    r = client.get("/auth/google")
    assert r.status_code == 303
    assert r.headers["location"] == "/auth/login"
    print("11. without keys the button hides and /auth/google bounces")

    print("ALL GOOGLE-OAUTH CHECKS PASSED")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
