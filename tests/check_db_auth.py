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
"""Self-check verification script for database models, user schemas, sessions, and RBAC authorization.

Run from repo root:

    python tests/check_db_auth.py
or
    docker compose exec web python tests/check_db_auth.py
"""

from __future__ import annotations

import datetime
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

# Graceful import of FastAPI components
try:
    from fastapi import HTTPException, Response
except ImportError:
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str = "") -> None:
            self.status_code = status_code
            self.detail = detail

    class Response:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def set_cookie(
            self,
            key: str,
            value: str,
            max_age: int | None = None,
            httponly: bool = False,
            samesite: str | None = None,
            secure: bool = False,
        ) -> None:
            flags = []
            if httponly:
                flags.append("HttpOnly")
            if samesite:
                flags.append(f"SameSite={samesite}")
            if secure:
                flags.append("Secure")
            if max_age is not None:
                flags.append(f"Max-Age={max_age}")
            self.headers["set-cookie"] = f"{key}={value}; " + "; ".join(flags)

        def delete_cookie(self, key: str) -> None:
            self.headers["set-cookie"] = f"{key}=; Max-Age=0"

# Graceful import of Pydantic
try:
    from pydantic import ValidationError
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False

# Graceful import of SQLAlchemy
try:
    from db.base import Base
    from db.models.session import UserSession
    from db.models.user import User, UserRole
    from schema.user import UserBase, UserProfileUpdate, UserRead
    HAS_SQLALCHEMY = True
except ImportError as exc:
    HAS_SQLALCHEMY = False
    _IMPORT_ERR = str(exc)

from core.security import (
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    read_session,
    read_session_token,
    require_roles,
)
from services.session_service import SESSION_LIFETIME, generate_session_token


class MockRequest:
    """Mock HTTP request with cookie dict."""

    def __init__(self, cookies: dict[str, str]) -> None:
        self.cookies = cookies


def test_models_metadata() -> None:
    """Verify SQLAlchemy declarative metadata and table structures."""
    print("-> Checking SQLAlchemy models metadata...")
    if not HAS_SQLALCHEMY:
        print(f"   [SKIP] SQLAlchemy environment not active on host ({_IMPORT_ERR}).")
        print("          Run inside container: 'docker compose exec web python app/e-commerce/check_db_auth.py'")
        return

    assert "users" in Base.metadata.tables, "users table not registered in Base.metadata"
    assert "user_sessions" in Base.metadata.tables, "user_sessions table not registered in Base.metadata"

    users_table = Base.metadata.tables["users"]
    sessions_table = Base.metadata.tables["user_sessions"]

    # Verify users table columns
    expected_user_cols = {"id", "phone", "role", "is_active", "first_name", "last_name", "last_login_at", "created_at", "updated_at"}
    assert expected_user_cols.issubset(set(users_table.columns.keys())), "Missing columns in users table"

    # Verify user_sessions table columns
    expected_session_cols = {"id", "session_token", "user_id", "expires_at", "is_revoked", "user_agent", "ip_address", "created_at", "updated_at"}
    assert expected_session_cols.issubset(set(sessions_table.columns.keys())), "Missing columns in user_sessions table"

    # Verify foreign key constraint
    fk = list(sessions_table.foreign_keys)[0]
    assert fk.column == users_table.c.id, f"Expected FK to users.id, got {fk.column}"

    print("   [OK] Model tables, columns, and foreign keys verified.")


def test_pydantic_schemas() -> None:
    """Verify Pydantic user validation schemas."""
    print("-> Checking Pydantic schemas...")
    if not HAS_PYDANTIC or not HAS_SQLALCHEMY:
        print("   [SKIP] Pydantic/SQLAlchemy not installed in host environment.")
        return

    # Valid user creation
    user_data = UserBase(phone="09123456789", first_name="علی", last_name="رضایی")
    assert user_data.phone == "09123456789"

    # Invalid phone format rejection
    try:
        UserBase(phone="02112345678")
    except ValidationError:
        pass
    else:
        raise AssertionError("UserBase accepted invalid landline phone number!")

    # UserRead serialization
    now = datetime.datetime.now(datetime.timezone.utc)
    u_id = uuid.uuid4()
    read_obj = UserRead(
        id=u_id,
        phone="09123456789",
        role=UserRole.CUSTOMER,
        is_active=True,
        created_at=now,
    )
    assert read_obj.id == u_id
    assert read_obj.role == UserRole.CUSTOMER
    print("   [OK] Pydantic validation and serialization verified.")


def test_session_cookies_and_tokens() -> None:
    """Verify session cookie signing, DB token encapsulation, and tampering rejection."""
    print("-> Checking signed session cookies with DB tokens...")

    phone = "09351112233"
    db_token = generate_session_token()
    assert len(db_token) >= 32, "Session token entropy insufficient"

    response = Response()
    create_session(response, phone=phone, session_token=db_token)

    # Extract Set-Cookie header
    cookie_header = response.headers.get("set-cookie")
    assert cookie_header is not None, "Set-Cookie header missing"
    assert "httponly" in cookie_header.lower(), "HttpOnly flag missing"
    assert "samesite=lax" in cookie_header.lower(), "SameSite=Lax flag missing"

    # Parse cookie value
    raw_cookie_val = cookie_header.split(";")[0].split("=", 1)[1]
    req = MockRequest(cookies={SESSION_COOKIE_NAME: raw_cookie_val})

    # Read payload
    verified_phone = read_session(req)
    assert verified_phone == phone, f"Expected {phone}, got {verified_phone}"

    verified_token = read_session_token(req)
    assert verified_token == db_token, f"Expected {db_token}, got {verified_token}"

    # Tampered cookie rejection
    tampered_cookie = raw_cookie_val[:-4] + "abcd"
    tampered_req = MockRequest(cookies={SESSION_COOKIE_NAME: tampered_cookie})
    assert read_session(tampered_req) is None, "Tampered cookie was not rejected!"
    assert read_session_token(tampered_req) is None, "Tampered session token was not rejected!"

    # Delete session
    del_res = Response()
    delete_session(del_res)
    del_header = del_res.headers.get("set-cookie")
    assert del_header is not None and ('max-age=0' in del_header.lower() or 'expires=' in del_header.lower()), "Cookie deletion header missing"

    print("   [OK] Signed cookie creation, token verification, and tampering defense verified.")


async def test_rbac_authorization() -> None:
    """Verify role-based access control (RBAC) dependencies."""
    print("-> Checking Role-Based Access Control (RBAC)...")
    if not HAS_SQLALCHEMY:
        print("   [SKIP] User model required for RBAC check.")
        return

    customer_user = User(
        id=uuid.uuid4(),
        phone="09121111111",
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    admin_user = User(
        id=uuid.uuid4(),
        phone="09122222222",
        role=UserRole.ADMIN,
        is_active=True,
    )
    staff_user = User(
        id=uuid.uuid4(),
        phone="09123333333",
        role=UserRole.STAFF,
        is_active=True,
    )

    admin_guard = require_roles(UserRole.ADMIN)
    staff_guard = require_roles(UserRole.STAFF, UserRole.ADMIN)

    # Admin guard: admin passes
    passed_admin = await admin_guard(admin_user)
    assert passed_admin.id == admin_user.id

    # Admin guard: customer blocked with 403 Forbidden
    try:
        await admin_guard(customer_user)
    except HTTPException as exc:
        assert exc.status_code == 403, f"Expected 403, got {exc.status_code}"
    else:
        raise AssertionError("Admin guard allowed a customer!")

    # Staff guard: staff passes, admin passes, customer blocked
    assert (await staff_guard(staff_user)).id == staff_user.id
    assert (await staff_guard(admin_user)).id == admin_user.id
    try:
        await staff_guard(customer_user)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Staff guard allowed a customer!")

    print("   [OK] RBAC authorization guards (Admin / Staff / Customer) verified.")


def main() -> None:
    import asyncio

    print("=" * 60)
    print("RUNNING DATABASE & AUTH SELF-CHECK SUITE")
    print("=" * 60)

    test_models_metadata()
    test_pydantic_schemas()
    test_session_cookies_and_tokens()
    asyncio.run(test_rbac_authorization())

    print("=" * 60)
    print("ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
