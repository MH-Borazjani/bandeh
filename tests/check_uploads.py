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
"""Runnable self-check for file uploads + the stock endpoint. Run from repo root:

    python tests/check_uploads.py

In-memory SQLite + a temp upload dir — no server, no live Postgres.
Products and photos live in Directus now, so there is no product
create or photo upload endpoint anymore. What is checked here:

1. The staff stock endpoint: staff 200, customer 403, guest 401;
   unknown product 404; stock below reserved 422.
2. The upload validator (service level): valid PNG round-trip,
   server-made filenames, oversized file (413, no partial file left),
   wrong extension (415), fake content (400).
3. Serving: files already on disk come back with bytes + content
   type; missing file 404, path traversal 404.
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi import Response, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import SESSION_COOKIE_NAME, create_session
from db.base import Base
from db.models.product_stock import ProductStock
from db.models.user import User, UserRole
from db.session import get_db
from integrations.directus_client import get_directus_client
from main import app
from services.session_service import create_database_session
from services.upload_service import (
    MAX_UPLOAD_SIZE,
    UploadError,
    get_upload_dir,
    save_upload,
)

# Small "images": only the magic bytes are real — the validator checks
# magic bytes, and the test names say so.
TINY_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100

PRODUCT_ID = uuid.uuid4()


class _FakeDirectusClient:
    """CMS stand-in: the one product the stock endpoint sets stock for."""

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        if str(product_id) != str(PRODUCT_ID):
            return None
        return {
            "id": str(PRODUCT_ID),
            "name": "یخچال",
            "description": "دو درب",
            "price_toman": 45_000_000,
            "stock": 0,
            "reserved": 0,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "category": None,
            "tag": None,
            "condition": "new",
            "condition_stars": None,
            "specs": {},
        }


def cookie_value(response: Response, name: str) -> str:
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
    app.dependency_overrides[get_directus_client] = lambda: _FakeDirectusClient()
    client = TestClient(app)

    async with Session() as s:
        staff = User(phone="09123333333", role=UserRole.STAFF, is_active=True)
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        s.add_all(
            [
                staff,
                customer,
                ProductStock(
                    directus_product_id=str(PRODUCT_ID), stock=5, reserved=2
                ),
            ]
        )
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

    # 1. Stock endpoint: staff sets stock, others are refused --------------
    set_session(staff_cookie)
    resp = client.patch(f"/products/{PRODUCT_ID}/stock", json=10)
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["stock"] == 10, resp.json()
    assert resp.json()["name"] == "یخچال"
    # Below the 2 reserved units: refused, stock unchanged.
    resp = client.patch(f"/products/{PRODUCT_ID}/stock", json=1)
    assert resp.status_code == 422, resp.status_code
    resp = client.get(f"/products/{PRODUCT_ID}")
    assert resp.json()["stock"] == 10, resp.json()
    # Unknown product: 404, even for staff.
    assert (
        client.patch(f"/products/{uuid.uuid4()}/stock", json=1).status_code == 404
    )
    set_session(customer_cookie)
    assert client.patch(f"/products/{PRODUCT_ID}/stock", json=1).status_code == 403
    set_session(None)
    assert client.patch(f"/products/{PRODUCT_ID}/stock", json=1).status_code == 401
    print("   [OK] stock endpoint: staff 200, customer 403, guest 401, unknown 404, below reserved 422")

    # 2. Upload validator (service level): server-made name, file on disk ---
    filename = await save_upload(
        UploadFile(filename="my-fridge-photo.png", file=io.BytesIO(TINY_PNG)), tmp
    )
    assert filename != "my-fridge-photo.png", "the client filename must not survive"
    assert filename.endswith(".png") and len(filename) == 32 + 4  # uuid hex + .png
    assert (tmp / filename).read_bytes() == TINY_PNG
    print("   [OK] save_upload: server-made name, file on disk")

    # 3. Serve it back --------------------------------------------------------
    resp = client.get(f"/uploads/{filename}")
    assert resp.status_code == 200, resp.status_code
    assert resp.content == TINY_PNG
    assert "image/png" in resp.headers["content-type"]
    print("   [OK] serve: bytes + content-type round-trip")

    # 4. Oversized: 413, and no partial file left on disk --------------------
    before = {p.name for p in tmp.iterdir()}
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_UPLOAD_SIZE
    try:
        await save_upload(
            UploadFile(filename="big.png", file=io.BytesIO(big)), tmp
        )
        raise AssertionError("oversized upload did not raise")
    except UploadError as exc:
        assert exc.status_code == 413, exc.status_code
    assert {p.name for p in tmp.iterdir()} == before, "partial file was left behind"
    print("   [OK] oversized: 413, no partial file")

    # 5. Wrong extension 415, fake content 400 ------------------------------
    try:
        await save_upload(
            UploadFile(filename="evil.exe", file=io.BytesIO(TINY_PNG)), tmp
        )
        raise AssertionError("wrong extension did not raise")
    except UploadError as exc:
        assert exc.status_code == 415, exc.status_code
    try:
        await save_upload(
            UploadFile(
                filename="fake.png",
                file=io.BytesIO(b"this is not a png at all"),
            ),
            tmp,
        )
        raise AssertionError("fake content did not raise")
    except UploadError as exc:
        assert exc.status_code == 400, exc.status_code
    print("   [OK] wrong extension 415, fake content 400")

    # 6. Serve: missing file 404, traversal 404 ------------------------------
    assert client.get("/uploads/nope.png").status_code == 404
    assert client.get("/uploads/..%2Fcheck_uploads.py").status_code == 404
    assert client.get("/uploads/.hidden").status_code == 404
    print("   [OK] serve: missing 404, traversal 404")

    print("All upload checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
