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
"""Runnable self-check for file uploads. Run from repo root:

    python tests/check_uploads.py

In-memory SQLite + a temp upload dir — no server, no live Postgres.
Covers: staff-only upload (401/403), valid PNG round-trip, server-made
filenames, oversized file (413, no partial file left), wrong extension
(415), fake content (400), path traversal on serve (404), and the
background resize job being scheduled.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import SESSION_COOKIE_NAME, create_session
from db.base import Base
from db.models.user import User, UserRole
from db.session import get_db
from main import app
from services import image_tasks
from services.session_service import create_database_session
from services.upload_service import MAX_UPLOAD_SIZE, get_upload_dir

# Small "images": only the magic bytes are real — the validator checks
# magic bytes, and the test names say so.
TINY_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


def cookie_value(response: Response, name: str) -> str:
    """Pull one cookie's value out of a response's Set-Cookie headers."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list"
    )
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


async def main_async() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="bandeh-uploads-"))

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
    app.dependency_overrides[get_upload_dir] = lambda: tmp
    client = TestClient(app)

    async with Session() as s:
        staff = User(phone="09123333333", role=UserRole.STAFF, is_active=True)
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        s.add_all([staff, customer])
        await s.commit()
        await s.refresh(staff)
        await s.refresh(customer)

    async def mint_session(user: User) -> str:
        async with Session() as s:
            record = await create_database_session(s, user)
            await s.commit()
            token = record.session_token
        resp = Response()
        create_session(resp, user.phone, session_token=token)
        return cookie_value(resp, SESSION_COOKIE_NAME)

    staff_cookie = await mint_session(staff)
    customer_cookie = await mint_session(customer)

    def set_session(cookie: str | None) -> None:
        client.cookies.clear()
        if cookie:
            client.cookies.set(SESSION_COOKIE_NAME, cookie)

    # 1. Create a product: staff 201, customer 403, guest 401 --------------
    set_session(staff_cookie)
    resp = client.post(
        "/products",
        json={"name": "یخچال", "description": "دو درب", "price_toman": 45000000, "stock": 5},
    )
    assert resp.status_code == 201, resp.status_code
    product_id = resp.json()["id"]
    assert resp.json()["reserved"] == 0
    assert resp.json()["image_url"] is None

    set_session(customer_cookie)
    assert client.post("/products", json={"name": "x", "price_toman": 1}).status_code == 403
    set_session(None)
    assert client.post("/products", json={"name": "x", "price_toman": 1}).status_code == 401
    print("   [OK] product create: staff 201, customer 403, guest 401")

    # 2. Valid PNG upload: server-made name, file on disk, job scheduled ---
    image_tasks.PENDING_JOBS.clear()
    set_session(staff_cookie)
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("my-fridge-photo.png", TINY_PNG, "image/png")},
    )
    assert resp.status_code == 200, resp.status_code
    filename = resp.json()["filename"]
    assert filename != "my-fridge-photo.png", "the client filename must not survive"
    assert filename.endswith(".png") and len(filename) == 32 + 4  # uuid hex + .png
    assert (tmp / filename).read_bytes() == TINY_PNG
    assert resp.json()["image_url"] == f"/uploads/{filename}"
    assert len(image_tasks.PENDING_JOBS) == 1
    assert image_tasks.PENDING_JOBS[0]["filename"] == filename
    print("   [OK] upload: server-made name, file on disk, resize job queued")

    # 3. Serve it back + product shows the image URL ------------------------
    resp = client.get(f"/uploads/{filename}")
    assert resp.status_code == 200, resp.status_code
    assert resp.content == TINY_PNG
    assert "image/png" in resp.headers["content-type"]

    resp = client.get(f"/products/{product_id}")
    assert resp.status_code == 200
    assert resp.json()["image_url"] == f"/uploads/{filename}"
    print("   [OK] serve: bytes + content-type round-trip, product shows image_url")

    # 4. Upload gate: customer 403, guest 401 --------------------------------
    set_session(customer_cookie)
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("x.png", TINY_PNG, "image/png")},
    )
    assert resp.status_code == 403, resp.status_code
    set_session(None)
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("x.png", TINY_PNG, "image/png")},
    )
    assert resp.status_code == 401, resp.status_code
    print("   [OK] upload gate: customer 403, guest 401")

    # 5. Oversized: 413, and no partial file left on disk --------------------
    set_session(staff_cookie)
    before = {p.name for p in tmp.iterdir()}
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_UPLOAD_SIZE
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 413, resp.status_code
    assert {p.name for p in tmp.iterdir()} == before, "partial file was left behind"
    print("   [OK] oversized: 413, no partial file")

    # 6. Wrong extension 415, fake content 400 ------------------------------
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("evil.exe", TINY_PNG, "application/octet-stream")},
    )
    assert resp.status_code == 415, resp.status_code
    resp = client.post(
        f"/products/{product_id}/photo",
        files={"file": ("fake.png", b"this is not a png at all", "image/png")},
    )
    assert resp.status_code == 400, resp.status_code
    print("   [OK] wrong extension 415, fake content 400")

    # 7. Serve: missing file 404, traversal 404 ------------------------------
    assert client.get("/uploads/nope.png").status_code == 404
    assert client.get("/uploads/..%2Fcheck_uploads.py").status_code == 404
    assert client.get("/uploads/.hidden").status_code == 404
    print("   [OK] serve: missing 404, traversal 404")

    print("All upload checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
