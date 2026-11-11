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
"""Comprehensive regression & edge-case test suite (stdlib unittest).

Run from the repository root:

    PYTHONPATH=app/e-commerce .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

import httpx
from fastapi import Response, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure app/e-commerce is importable regardless of working directory
E_COMMERCE_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(E_COMMERCE_DIR) not in sys.path:
    sys.path.insert(0, str(E_COMMERCE_DIR))

from core.cache import get_cache, reset_cache_for_tests  # noqa: E402
from core.money import toman_to_rial  # noqa: E402
from core.security import (  # noqa: E402
    REFRESH_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    create_session,
    set_refresh_cookie,
)
from db.base import Base  # noqa: E402
from db.models.order import OrderStatus  # noqa: E402
from db.models.payment_attempt import PaymentAttempt, PaymentStatus  # noqa: E402
from db.models.product import Product  # noqa: E402
from db.models.refresh_token import RefreshToken  # noqa: E402
from db.models.session import UserSession  # noqa: E402
from db.models.user import User, UserRole  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.directus_client import (  # noqa: E402
    FALLBACK_CATEGORIES,
    DirectusClient,
    format_toman_price,
)
from integrations.zibal_client import ZibalClient, ZibalError, get_zibal_client  # noqa: E402
from main import app  # noqa: E402
from services.order_service import OrderError, create_order  # noqa: E402
from services.refresh_service import issue_refresh_token  # noqa: E402
from services.search_service import search_products_ilike  # noqa: E402
from services.session_service import create_database_session  # noqa: E402
from services.upload_service import UploadError, save_upload  # noqa: E402


def _extract_cookie(response: Response, name: str) -> str:
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list"
    )
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


class _FailingCache:
    """Cache double that always raises on get and set."""

    async def get(self, key: str) -> str | None:
        raise RuntimeError("redis connection lost")

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        raise RuntimeError("redis connection lost")

    async def delete(self, key: str) -> None:
        pass

    async def delete_by_prefix(self, prefix: str) -> int:
        return 0


class _CorruptCache:
    """Cache double returning invalid JSON."""

    async def get(self, key: str) -> str | None:
        return "{not valid json"

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass

    async def delete_by_prefix(self, prefix: str) -> int:
        return 0


class HardeningUnitTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for money, formatting, cache, uploads, and Zibal transport errors."""

    def test_toman_to_rial_conversion(self) -> None:
        self.assertEqual(toman_to_rial(0), 0)
        self.assertEqual(toman_to_rial(125_000), 1_250_000)

    def test_format_toman_price_rejects_bool_and_handles_edge_inputs(self) -> None:
        self.assertEqual(format_toman_price(True), "")
        self.assertEqual(format_toman_price(False), "")
        self.assertEqual(format_toman_price(None), "")
        self.assertEqual(format_toman_price(0), "۰")
        self.assertEqual(format_toman_price(45_000_000), "۴۵٬۰۰۰٬۰۰۰")
        self.assertEqual(format_toman_price("توافقی"), "توافقی")

    async def test_directus_client_survives_cache_errors_and_corrupt_json(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if "categories" in str(request.url):
                return httpx.Response(200, json={"data": [{"name": "Cat", "description": "Desc"}]})
            return httpx.Response(200, json={"data": [{"name": "Prod", "price": 1000, "tag": None}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client_failing = DirectusClient(http, cache=_FailingCache())
            data = await client_failing.get_landing_content()
            self.assertEqual(data["categories"], [{"name": "Cat", "desc": "Desc"}])

            client_corrupt = DirectusClient(http, cache=_CorruptCache())
            data2 = await client_corrupt.get_landing_content()
            self.assertEqual(data2["products"][0]["price"], "۱٬۰۰۰")

        # Fallback when Directus itself fails + cache fails
        def down_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(down_handler)) as http:
            client_both_down = DirectusClient(http, cache=_FailingCache())
            data3 = await client_both_down.get_landing_content()
            self.assertEqual(data3["categories"], FALLBACK_CATEGORIES)

    async def test_cache_cold_start_lock_builds_once(self) -> None:
        reset_cache_for_tests()
        old_url = os.environ.get("REDIS_URL")
        os.environ["REDIS_URL"] = "redis://127.0.0.1:6399"
        try:
            c1, c2, c3 = await asyncio.gather(get_cache(), get_cache(), get_cache())
            self.assertIs(c1, c2)
            self.assertIs(c2, c3)
        finally:
            reset_cache_for_tests()
            if old_url is None:
                os.environ.pop("REDIS_URL", None)
            else:
                os.environ["REDIS_URL"] = old_url

    async def test_zibal_client_wraps_transport_and_malformed_responses(self) -> None:
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as http:
            zibal = ZibalClient(http)
            with self.assertRaises(ZibalError) as ctx:
                await zibal.request_payment(10_000, "http://cb", "ord-1")
            self.assertIsNone(ctx.exception.result)

            with self.assertRaises(ZibalError) as vctx:
                await zibal.verify_payment(12345)
            self.assertIsNone(vctx.exception.result)

        def missing_track_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"result": 100, "message": "ok"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(missing_track_handler)) as http:
            zibal = ZibalClient(http)
            with self.assertRaises(ZibalError):
                await zibal.request_payment(10_000, "http://cb", "ord-1")

    async def test_upload_webp_empty_and_case_insensitive_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp)

            # Valid WebP (RIFF + 4 bytes size + WEBP)
            webp_bytes = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 16
            webp_file = UploadFile(filename="photo.WEBP", file=BytesIO(webp_bytes))
            saved_name = await save_upload(webp_file, upload_dir)
            self.assertTrue(saved_name.endswith(".webp"))
            self.assertTrue((upload_dir / saved_name).is_file())

            # Fake RIFF (AVI instead of WEBP) -> 400
            fake_webp = UploadFile(filename="fake.webp", file=BytesIO(b"RIFF\x24\x00\x00\x00AVI "))
            with self.assertRaises(UploadError) as ctx:
                await save_upload(fake_webp, upload_dir)
            self.assertEqual(ctx.exception.status_code, 400)

            # Empty 0-byte JPG -> 400
            empty_jpg = UploadFile(filename="empty.JPG", file=BytesIO(b""))
            with self.assertRaises(UploadError) as ctx2:
                await save_upload(empty_jpg, upload_dir)
            self.assertEqual(ctx2.exception.status_code, 400)

            # Valid uppercase .JPG
            valid_jpg = UploadFile(filename="CAM_01.JPG", file=BytesIO(b"\xff\xd8\xff\xe0\x00\x10JFIF"))
            saved_jpg = await save_upload(valid_jpg, upload_dir)
            self.assertTrue(saved_jpg.endswith(".jpg"))


class HardeningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """End-to-end DB & HTTP integration tests with in-memory SQLite."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

        async def override_get_db():
            async with self.Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        self._prev_overrides = dict(app.dependency_overrides)
        app.dependency_overrides[get_db] = override_get_db

    async def asyncTearDown(self) -> None:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self._prev_overrides)
        await self.engine.dispose()

    async def _mint_cookie(self, user: User, *, with_db_session: bool = True) -> str:
        token: str | None = None
        if with_db_session:
            async with self.Session() as s:
                rec = await create_database_session(s, user)
                await s.commit()
                token = rec.session_token
        resp = Response()
        create_session(resp, user.phone, session_token=token)
        return _extract_cookie(resp, SESSION_COOKIE_NAME)

    async def test_duplicate_product_id_in_order_cannot_overreserve_stock(self) -> None:
        async with self.Session() as s:
            user = User(phone="09120000001", role=UserRole.CUSTOMER, is_active=True)
            product = Product(
                name="Blender",
                description="100% copper \\ motor",
                price_toman=500_000,
                stock=3,
                reserved=0,
                is_active=True,
            )
            s.add_all([user, product])
            await s.commit()
            await s.refresh(user)
            await s.refresh(product)

        async with self.Session() as s:
            # 2 + 2 = 4 > stock (3): must be rejected!
            with self.assertRaises(OrderError):
                await create_order(
                    s,
                    user,
                    [
                        {"product_id": product.id, "quantity": 2},
                        {"product_id": product.id, "quantity": 2},
                    ],
                )

            # 1 + 2 = 3 <= stock (3): valid and merged into one line!
            order = await create_order(
                s,
                user,
                [
                    {"product_id": product.id, "quantity": 1},
                    {"product_id": product.id, "quantity": 2},
                ],
            )
            self.assertEqual(len(order.items), 1)
            self.assertEqual(order.items[0].quantity, 3)
            self.assertEqual(order.total_toman, 1_500_000)
            self.assertEqual(order.total_rial, 15_000_000)

    async def test_search_backslash_escape_and_whitespace_query(self) -> None:
        async with self.Session() as s:
            p1 = Product(
                name="Coffee Grinder",
                description="path\\model_x",
                price_toman=100_000,
                stock=5,
                is_active=True,
            )
            p2 = Product(
                name="Toaster",
                description="pathmodel_x",
                price_toman=100_000,
                stock=5,
                is_active=True,
            )
            s.add_all([p1, p2])
            await s.commit()

        async with self.Session() as s:
            self.assertEqual(await search_products_ilike(s, "   \t  "), [])
            matched = await search_products_ilike(s, "\\")
            self.assertEqual(len(matched), 1)
            self.assertEqual(matched[0].name, "Coffee Grinder")

    async def test_inactive_user_blocked_in_fallback_and_refresh(self) -> None:
        async with self.Session() as s:
            inactive_user = User(
                phone="09120000002", role=UserRole.CUSTOMER, is_active=False
            )
            s.add(inactive_user)
            await s.commit()
            await s.refresh(inactive_user)
            refresh_token, _ = await issue_refresh_token(s, inactive_user)
            await s.commit()

        no_token_cookie = await self._mint_cookie(inactive_user, with_db_session=False)
        client = TestClient(app)

        # Fallback cookie path must deny inactive user
        client.cookies.set(SESSION_COOKIE_NAME, no_token_cookie)
        r = client.get("/orders/00000000-0000-0000-0000-000000000001")
        self.assertEqual(r.status_code, 401)

        # Refresh endpoint must deny inactive user and clear cookies
        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE_NAME, refresh_token)
        r_ref = client.post("/auth/refresh")
        self.assertEqual(r_ref.status_code, 401)

    async def test_revoked_refresh_token_reuse_kills_db_sessions_too(self) -> None:
        async with self.Session() as s:
            user = User(phone="09120000003", role=UserRole.CUSTOMER, is_active=True)
            s.add(user)
            await s.commit()
            await s.refresh(user)
            session_rec = await create_database_session(s, user)
            old_refresh, _ = await issue_refresh_token(s, user)
            await s.commit()

        client = TestClient(app)
        # Rotate once legitimately
        client.cookies.set(REFRESH_COOKIE_NAME, old_refresh)
        r1 = client.post("/auth/refresh")
        self.assertEqual(r1.status_code, 200)

        # Replay stolen old_refresh -> must revoke all refresh tokens AND all DB sessions!
        client.cookies.clear()
        client.cookies.set(REFRESH_COOKIE_NAME, old_refresh)
        r2 = client.post("/auth/refresh")
        self.assertEqual(r2.status_code, 401)

        async with self.Session() as s:
            live_sessions = (
                await s.execute(
                    select(UserSession).where(
                        UserSession.user_id == user.id,
                        UserSession.is_revoked.is_(False),
                    )
                )
            ).scalars().all()
            live_tokens = (
                await s.execute(
                    select(RefreshToken).where(
                        RefreshToken.user_id == user.id,
                        RefreshToken.is_revoked.is_(False),
                    )
                )
            ).scalars().all()
            self.assertEqual(len(live_sessions), 0)
            self.assertEqual(len(live_tokens), 0)
            _ = session_rec  # keep reference clarity

    async def test_admin_overview_counts_live_sessions_accurately(self) -> None:
        async with self.Session() as s:
            admin = User(phone="09120000004", role=UserRole.ADMIN, is_active=True)
            cust = User(phone="09120000005", role=UserRole.CUSTOMER, is_active=True)
            s.add_all([admin, cust])
            await s.commit()
            await s.refresh(admin)
            await s.refresh(cust)
            revoked_sess = await create_database_session(s, cust)
            revoked_sess.is_revoked = True
            await s.commit()

        admin_cookie = await self._mint_cookie(admin, with_db_session=True)
        client = TestClient(app)
        client.cookies.set(SESSION_COOKIE_NAME, admin_cookie)
        r = client.get("/admin/overview")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["admin"], admin.phone)
        self.assertEqual(body["total_users"], 2)
        self.assertEqual(body["live_sessions"], 1)

    async def test_payment_network_error_returns_502_and_releases_hold(self) -> None:
        async with self.Session() as s:
            user = User(phone="09120000006", role=UserRole.CUSTOMER, is_active=True)
            product = Product(
                name="Microwave", price_toman=200_000, stock=4, reserved=0, is_active=True
            )
            low_product = Product(
                name="Bolt", price_toman=50, stock=10, reserved=0, is_active=True
            )
            s.add_all([user, product, low_product])
            await s.commit()
            await s.refresh(user)
            await s.refresh(product)
            await s.refresh(low_product)

        def network_fail_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("gateway unreachable", request=request)

        async_client = httpx.AsyncClient(transport=httpx.MockTransport(network_fail_handler))
        app.dependency_overrides[get_zibal_client] = lambda: ZibalClient(async_client)

        try:
            cookie = await self._mint_cookie(user, with_db_session=True)
            client = TestClient(app)
            client.cookies.set(SESSION_COOKIE_NAME, cookie)

            # Create normal order -> start payment when gateway is unreachable -> 502 + hold released
            r_order = client.post(
                "/orders",
                json={"items": [{"product_id": str(product.id), "quantity": 2}]},
            )
            self.assertEqual(r_order.status_code, 201)
            order_id = r_order.json()["id"]

            r_start = client.post(f"/payments/{order_id}/start")
            self.assertEqual(r_start.status_code, 502)

            async with self.Session() as s:
                prod = await s.get(Product, product.id)
                self.assertEqual(prod.reserved, 0)
                self.assertEqual(prod.stock, 4)
                attempts = (
                    await s.execute(
                        select(PaymentAttempt).where(
                            PaymentAttempt.order_id == uuid.UUID(order_id)
                        )
                    )
                ).scalars().all()
                self.assertEqual(len(attempts), 1)
                self.assertEqual(attempts[0].status, PaymentStatus.FAILED.value)

            # Re-starting a failed order is rejected with 409
            r_restart = client.post(f"/payments/{order_id}/start")
            self.assertEqual(r_restart.status_code, 409)

            # Callback when verify endpoint times out -> 502 instead of 500
            async with self.Session() as s:
                order2 = await create_order(
                    s, user, [{"product_id": product.id, "quantity": 1}]
                )
                attempt2 = PaymentAttempt(
                    order_id=order2.id,
                    amount_rial=order2.total_rial,
                    track_id=987654,
                    status=PaymentStatus.WAITING.value,
                )
                order2.status = OrderStatus.WAITING_PAYMENT.value
                s.add(attempt2)
                await s.commit()

            r_cb = client.get(
                "/payments/callback",
                params={"trackId": 987654, "orderId": str(order2.id)},
            )
            self.assertEqual(r_cb.status_code, 502)

            # Below Zibal minimum (50 Toman = 500 Rial < 1000 Rial) -> 400 + no hold left
            r_low = client.post(
                "/orders",
                json={"items": [{"product_id": str(low_product.id), "quantity": 1}]},
            )
            self.assertEqual(r_low.status_code, 201)
            low_order_id = r_low.json()["id"]
            r_low_start = client.post(f"/payments/{low_order_id}/start")
            self.assertEqual(r_low_start.status_code, 400)
            async with self.Session() as s:
                lp = await s.get(Product, low_product.id)
                self.assertEqual(lp.reserved, 0)
        finally:
            await async_client.aclose()


if __name__ == "__main__":
    unittest.main()
