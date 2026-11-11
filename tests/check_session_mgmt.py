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
"""Runnable self-check for session management & authorization. Run from repo root:

    python tests/check_session_mgmt.py

In-memory SQLite only — no server, no live Postgres, no live Redis.
Covers: admin gate (401/403/200 over HTTP), refresh rotation, reuse
detection (stolen-token family nuke), expired refresh token, logout
revokes the refresh token, logout-everywhere revokes everything, and
the no-token cookie fallback still working.
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import uuid
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import (
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_session,
)
from db.base import Base
from db.models.refresh_token import RefreshToken
from db.models.session import UserSession
from db.models.user import User, UserRole
from db.session import get_db
from main import app
from services.refresh_service import issue_refresh_token
from services.session_service import create_database_session


class _HasHeaders(Protocol):
    @property
    def headers(self) -> object: ...


def _set_cookie_headers(response: _HasHeaders) -> list[str]:
    """All Set-Cookie headers. Starlette uses getlist, httpx uses get_list."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list", None
    )
    assert callable(getlist)
    return list(getlist("set-cookie"))


def cookie_value(response: _HasHeaders, name: str) -> str:
    """Pull one cookie's value out of a response's Set-Cookie headers."""
    for header in _set_cookie_headers(response):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


def cookie_is_cleared(response: _HasHeaders, name: str) -> bool:
    """True when a response tells the browser to delete ``name``."""
    for header in _set_cookie_headers(response):
        if header.startswith(name + "=") and "Max-Age=0" in header:
            return True
    return False


async def main_async() -> None:
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
    client = TestClient(app)

    # --- Setup: one customer, one admin ----------------------------------
    async with Session() as s:
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        admin = User(phone="09122222222", role=UserRole.ADMIN, is_active=True)
        s.add_all([customer, admin])
        await s.commit()
        await s.refresh(customer)
        await s.refresh(admin)

    async def mint_session(user: User) -> str:
        """Create a DB session row and return a signed cookie value for it."""
        async with Session() as s:
            record = await create_database_session(s, user)
            await s.commit()
            token = record.session_token
        resp = Response()
        create_session(resp, user.phone, session_token=token)
        return cookie_value(resp, SESSION_COOKIE_NAME)

    async def mint_refresh(user: User) -> str:
        """Issue a refresh token and return its plaintext (cookie value)."""
        async with Session() as s:
            token, _ = await issue_refresh_token(s, user)
            await s.commit()
        return token

    def set_cookies(session_cookie: str | None = None, refresh_cookie: str | None = None) -> None:
        client.cookies.clear()
        if session_cookie:
            client.cookies.set(SESSION_COOKIE_NAME, session_cookie)
        if refresh_cookie:
            client.cookies.set(REFRESH_COOKIE_NAME, refresh_cookie)

    async def live_refresh_count(user_id: uuid.UUID) -> int:
        async with Session() as s:
            result = await s.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user_id,
                    RefreshToken.is_revoked == False,  # noqa: E712
                )
            )
            return len(result.scalars().all())

    async def live_session_count(user_id: uuid.UUID) -> int:
        async with Session() as s:
            result = await s.execute(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.is_revoked == False,  # noqa: E712
                )
            )
            return len(result.scalars().all())

    # 1. Admin gate: guest 401, customer 403, admin 200 -------------------
    resp = client.get("/admin/overview")
    assert resp.status_code == 401, resp.status_code

    set_cookies(session_cookie=await mint_session(customer))
    resp = client.get("/admin/overview")
    assert resp.status_code == 403, resp.status_code

    set_cookies(session_cookie=await mint_session(admin))
    resp = client.get("/admin/overview")
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["total_users"] == 2, resp.json()
    assert resp.json()["live_sessions"] == 2, resp.json()  # one per user above
    print("   [OK] admin gate: 401 guest, 403 customer, 200 admin")

    # 2. Refresh rotation: old token dies, new token works -----------------
    first_refresh = await mint_refresh(customer)
    set_cookies(refresh_cookie=first_refresh)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200, resp.status_code
    second_refresh = cookie_value(resp, REFRESH_COOKIE_NAME)
    assert second_refresh != first_refresh
    assert cookie_value(resp, SESSION_COOKIE_NAME)
    assert await live_refresh_count(customer.id) == 1

    set_cookies(refresh_cookie=second_refresh)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200, resp.status_code
    third_refresh = cookie_value(resp, REFRESH_COOKIE_NAME)
    assert third_refresh != second_refresh
    assert await live_refresh_count(customer.id) == 1
    print("   [OK] refresh rotates: one live token at a time")

    # 3. Reuse detection: presenting the dead first token nukes the family -
    set_cookies(refresh_cookie=first_refresh)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401, resp.status_code
    assert cookie_is_cleared(resp, REFRESH_COOKIE_NAME)
    assert cookie_is_cleared(resp, SESSION_COOKIE_NAME)
    assert await live_refresh_count(customer.id) == 0, "stolen-token reuse must nuke the family"
    print("   [OK] reuse of a revoked token nukes the whole family")

    # 4. Expired token: 401, but the family is NOT nuked -------------------
    async with Session() as s:
        expired_token, record = await issue_refresh_token(s, customer)
        record.expires_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            seconds=1
        )
        await s.commit()
    good_token = await mint_refresh(customer)
    before = await live_refresh_count(customer.id)
    assert before == 2  # expired-but-unrevoked row + the good token
    set_cookies(refresh_cookie=expired_token)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401, resp.status_code
    assert await live_refresh_count(customer.id) == before, "expired token must not nuke the family"
    set_cookies(refresh_cookie=good_token)
    resp = client.post("/auth/refresh")
    assert resp.status_code == 200, resp.status_code
    print("   [OK] expired token: 401, family intact")

    # 5. Logout revokes the refresh token ----------------------------------
    logout_refresh = await mint_refresh(customer)
    logout_session = await mint_session(customer)
    set_cookies(session_cookie=logout_session, refresh_cookie=logout_refresh)
    resp = client.post("/auth/logout")
    assert resp.status_code in (200, 303), resp.status_code
    set_cookies(refresh_cookie=logout_refresh)
    assert client.post("/auth/refresh").status_code == 401
    print("   [OK] logout revokes the refresh token")

    # 6. Logout-everywhere: all sessions + tokens die, admin gate closes --
    admin_session = await mint_session(admin)
    admin_refresh = await mint_refresh(admin)
    set_cookies(session_cookie=admin_session, refresh_cookie=admin_refresh)
    resp = client.post("/auth/logout-everywhere")
    assert resp.status_code in (200, 303), resp.status_code
    assert await live_refresh_count(admin.id) == 0
    assert await live_session_count(admin.id) == 0
    set_cookies(refresh_cookie=admin_refresh)
    assert client.post("/auth/refresh").status_code == 401
    set_cookies(session_cookie=admin_session)
    assert client.get("/admin/overview").status_code == 401, "revoked session must not re-identify via phone"
    print("   [OK] logout-everywhere revokes sessions + tokens on all devices")

    # 7. Refresh with no cookie / unknown token -> 401 ----------------------
    set_cookies()
    assert client.post("/auth/refresh").status_code == 401
    set_cookies(refresh_cookie="never-issued-token")
    assert client.post("/auth/refresh").status_code == 401
    print("   [OK] refresh without a token, or with an unknown token: 401")

    # 8. Cookie without a session token still works (DB-down login path) ---
    resp = Response()
    create_session(resp, admin.phone)  # no session_token, like login with DB down
    set_cookies(session_cookie=cookie_value(resp, SESSION_COOKIE_NAME))
    assert client.get("/admin/overview").status_code == 200
    print("   [OK] no-token cookie fallback still works")

    print("All session-management checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
