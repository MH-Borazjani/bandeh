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
"""Unit and integration tests for the Bandeh purchase path (Stage 2).

Covers:
- Cart page returning 200 and rendering design system empty state and controls.
- Checkout page returning 200 and rendering address form fields.
- Checkout creating an order with recipient name, phone, and delivery address.
- Payment result page rendering both success and failure states with order number and next steps.
- Existing JSON callback staying backwards-compatible for API callers.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

# Ensure app/e-commerce is in sys.path
APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import httpx  # noqa: E402
from fastapi import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.security import SESSION_COOKIE_NAME, create_session  # noqa: E402
from db.base import Base  # noqa: E402
from db.models.order import Order  # noqa: E402
from db.models.product import Product  # noqa: E402
from db.models.user import User, UserRole  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.directus_client import get_directus_client  # noqa: E402
from integrations.zibal_client import ZibalClient, get_zibal_client  # noqa: E402
from main import app  # noqa: E402
from services.session_service import create_database_session  # noqa: E402


def cookie_value(response: Response, name: str) -> str:
    """Pull one cookie value out of set-cookie headers."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list", None
    )
    assert callable(getlist)
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


class FakeDirectusClient:
    """Mock Directus client simulating CMS responses."""

    def __init__(self, products: dict[str, dict] | None = None) -> None:
        self.products = products or {}

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        return self.products.get(str(product_id))

    async def list_products(self, limit: int = 20) -> list[dict]:
        return list(self.products.values())[:limit]

    async def get_landing_content(self) -> dict:
        return {
            "categories": [{"name": "یخچال", "desc": "لوازم خانگی"}],
            "products": list(self.products.values()),
        }


def make_mock_transport(state: dict) -> httpx.MockTransport:
    """Mock transport for Zibal payment gateway calls."""

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/v1/request":
            track_id = state["next_track_id"]
            state["next_track_id"] += 1
            return httpx.Response(
                200, json={"result": 100, "message": "success", "trackId": track_id}
            )
        if request.url.path == "/v1/verify":
            verify_fn = state.get("verify")
            if callable(verify_fn):
                body = json.loads(request.content)
                return httpx.Response(200, json=verify_fn(body))
            return httpx.Response(200, json=state.get("verify_body", {}))
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


class PurchasePathTests(unittest.TestCase):
    """Test suite for the Stage 2 purchase path."""

    engine: AsyncEngine
    Session: async_sessionmaker[AsyncSession]
    client: TestClient
    user: User
    product: Product
    session_cookie: str
    zibal_state: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        async def init_db() -> None:
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(init_db())
        cls.Session = async_sessionmaker(
            cls.engine, expire_on_commit=False, class_=AsyncSession
        )

        async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
            async with cls.Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_db] = override_get_db

        cls.zibal_state = {
            "requests": [],
            "next_track_id": 998800,
            "verify": None,
            "verify_body": {
                "result": 100,
                "paidAt": "2026-11-14T12:00:00+00:00",
                "cardNumber": "603799******4321",
                "status": 1,
                "refNumber": 123456789,
                "amount": 250000000,
            },
        }

        mock_transport = make_mock_transport(cls.zibal_state)
        app.dependency_overrides[get_zibal_client] = lambda: ZibalClient(
            httpx.AsyncClient(transport=mock_transport)
        )

        # Seed user and product
        async def seed() -> None:
            async with cls.Session() as s:
                u = User(phone="09123456780", role=UserRole.CUSTOMER, is_active=True)
                p = Product(
                    name="ماشین ظرفشویی ۱۴ نفره بنده",
                    description="سه سبد، تمام استیل با موتور اینورتر",
                    price_toman=25000000,
                    stock=10,
                    reserved=0,
                    is_active=True,
                )
                s.add_all([u, p])
                await s.commit()
                await s.refresh(u)
                await s.refresh(p)
                cls.user = u
                cls.product = p

                db_sess = await create_database_session(s, u)
                await s.commit()
                resp = Response()
                create_session(resp, u.phone, session_token=db_sess.session_token)
                cls.session_cookie = cookie_value(resp, SESSION_COOKIE_NAME)

        asyncio.run(seed())

        fake_cms = FakeDirectusClient(
            products={
                str(cls.product.id): {
                    "id": str(cls.product.id),
                    "name": cls.product.name,
                    "price_toman": cls.product.price_toman,
                    "stock": cls.product.stock,
                    "reserved": cls.product.reserved,
                }
            }
        )
        app.dependency_overrides[get_directus_client] = lambda: fake_cms

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.clear()
        if cls.engine:
            asyncio.run(cls.engine.dispose())

    def setUp(self) -> None:
        self.client.cookies.clear()

    def test_cart_page_returns_200_and_renders_design_system(self) -> None:
        """Cart page must return 200, contain Persian titles, empty state, and shop.js."""
        resp = self.client.get("/cart")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        # Page identity and structure
        self.assertIn("سبد خرید", html)
        self.assertIn("بازگشت به فروشگاه", html)

        # Empty cart state elements
        self.assertIn('id="cart-empty-state"', html)
        self.assertIn("سبد خرید شما خالی است", html)

        # Filled cart content and controls
        self.assertIn('id="cart-content"', html)
        self.assertIn('id="cart-items"', html)
        self.assertIn('id="cart-total-price"', html)
        self.assertIn("تومان", html)
        self.assertIn('id="btn-checkout"', html)
        self.assertIn('id="btn-clear-cart"', html)

        # Design system wiring
        self.assertIn("/static/js/shop.js", html)
        self.assertIn('id="theme-toggle"', html)

    def test_checkout_page_returns_200_and_renders_address_form(self) -> None:
        """Checkout page must return 200 and render delivery address form inputs."""
        # Unauthenticated access also renders 200
        resp = self.client.get("/checkout")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        self.assertIn("اطلاعات ارسال و پرداخت", html)
        self.assertIn('id="recipient-name"', html)
        self.assertIn('name="recipient_name"', html)
        self.assertIn('id="recipient-phone"', html)
        self.assertIn('name="recipient_phone"', html)
        self.assertIn('id="delivery-address"', html)
        self.assertIn('name="delivery_address"', html)
        self.assertIn("زیبال", html)
        self.assertIn('id="btn-submit-order"', html)

        # Authenticated access pre-fills phone
        self.client.cookies.set(SESSION_COOKIE_NAME, self.session_cookie)
        auth_resp = self.client.get("/checkout")
        self.assertEqual(auth_resp.status_code, 200)
        self.assertIn(self.user.phone, auth_resp.text)

    def test_checkout_creates_order_with_delivery_address(self) -> None:
        """POST /orders must store recipient name, phone, and delivery address."""
        self.client.cookies.set(SESSION_COOKIE_NAME, self.session_cookie)
        payload = {
            "items": [{"product_id": str(self.product.id), "quantity": 2}],
            "recipient_name": "سارا احمدی",
            "recipient_phone": "09129876543",
            "delivery_address": "تهران، میدان انقلاب، خیابان کارگر شمالی، پلاک ۱۲، واحد ۴",
        }
        resp = self.client.post("/orders", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()

        # Check API response
        self.assertEqual(data["recipient_name"], "سارا احمدی")
        self.assertEqual(data["recipient_phone"], "09129876543")
        self.assertEqual(
            data["delivery_address"],
            "تهران، میدان انقلاب، خیابان کارگر شمالی، پلاک ۱۲، واحد ۴",
        )
        self.assertEqual(data["total_toman"], 50000000)
        order_id = uuid.UUID(data["id"])

        # Check Database persistence
        async def check_db() -> None:
            async with self.Session() as s:
                order = (
                    await s.execute(select(Order).where(Order.id == order_id))
                ).scalar_one()
                self.assertEqual(order.recipient_name, "سارا احمدی")
                self.assertEqual(order.recipient_phone, "09129876543")
                self.assertEqual(
                    order.delivery_address,
                    "تهران، میدان انقلاب، خیابان کارگر شمالی، پلاک ۱۲، واحد ۴",
                )

        asyncio.run(check_db())

        # Check read endpoint preserves address
        read_resp = self.client.get(f"/orders/{order_id}")
        self.assertEqual(read_resp.status_code, 200)
        read_data = read_resp.json()
        self.assertEqual(read_data["recipient_name"], "سارا احمدی")
        self.assertEqual(read_data["recipient_phone"], "09129876543")
        self.assertEqual(
            read_data["delivery_address"],
            "تهران، میدان انقلاب، خیابان کارگر شمالی، پلاک ۱۲، واحد ۴",
        )

    def test_payment_result_page_renders_success_state(self) -> None:
        """HTML payment result page must display success badge, order number, and next steps."""
        self.client.cookies.set(SESSION_COOKIE_NAME, self.session_cookie)

        # 1. Create order
        order_resp = self.client.post(
            "/orders",
            json={
                "items": [{"product_id": str(self.product.id), "quantity": 1}],
                "recipient_name": "رضا باقری",
                "recipient_phone": "09121112233",
                "delivery_address": "مشهد، بلوار سجاد",
            },
        )
        self.assertEqual(order_resp.status_code, 201)
        order_id = order_resp.json()["id"]

        # 2. Start payment
        self.zibal_state["verify_body"] = {
            "result": 100,
            "paidAt": "2026-11-14T13:00:00+00:00",
            "cardNumber": "502229******8899",
            "status": 1,
            "refNumber": 99887766,
            "amount": 250000000,
        }
        start_resp = self.client.post(f"/payments/{order_id}/start")
        self.assertEqual(start_resp.status_code, 200)
        track_id = start_resp.json()["track_id"]

        # 3. Simulate browser callback redirect from Zibal with Accept: text/html
        callback_resp = self.client.get(
            "/payments/callback",
            params={
                "trackId": track_id,
                "success": "1",
                "status": "1",
                "orderId": order_id,
            },
            headers={"Accept": "text/html"},
        )
        self.assertEqual(callback_resp.status_code, 200)
        self.assertIn("text/html", callback_resp.headers["content-type"])
        html = callback_resp.text

        # Validate success presentation
        self.assertIn("پرداخت با موفقیت انجام شد", html)
        self.assertIn("سفارش شما ثبت شد", html)
        self.assertIn(str(order_id), html)
        self.assertIn("99887766", html)
        self.assertIn("مراحل بعدی سفارش شما", html)
        self.assertIn("انبار مرکزی بنده", html)
        self.assertIn("بازگشت به فروشگاه", html)

    def test_payment_result_page_renders_failure_state(self) -> None:
        """HTML payment result page must display failure badge, order number, and next steps."""
        self.client.cookies.set(SESSION_COOKIE_NAME, self.session_cookie)

        # 1. Create order
        order_resp = self.client.post(
            "/orders",
            json={
                "items": [{"product_id": str(self.product.id), "quantity": 1}],
                "recipient_name": "مریم حسینی",
                "recipient_phone": "09124445566",
                "delivery_address": "اصفهان، خیابان چهارباغ",
            },
        )
        self.assertEqual(order_resp.status_code, 201)
        order_id = order_resp.json()["id"]

        # 2. Start payment
        start_resp = self.client.post(f"/payments/{order_id}/start")
        self.assertEqual(start_resp.status_code, 200)
        track_id = start_resp.json()["track_id"]

        # 3. Set verify to return not paid (result = -1)
        self.zibal_state["verify_body"] = {
            "result": -1,
            "message": "not paid",
            "status": -1,
        }

        # 4. Browser callback with Accept: text/html
        callback_resp = self.client.get(
            "/payments/callback",
            params={
                "trackId": track_id,
                "success": "0",
                "status": "-1",
                "orderId": order_id,
            },
            headers={"Accept": "text/html"},
        )
        self.assertEqual(callback_resp.status_code, 200)
        self.assertIn("text/html", callback_resp.headers["content-type"])
        html = callback_resp.text

        # Validate failure presentation
        self.assertIn("پرداخت ناموفق", html)
        self.assertIn("پرداخت سفارش تکمیل نشد", html)
        self.assertIn(str(order_id), html)
        self.assertIn("راهنمای مراحل بعدی", html)
        self.assertIn("۷۲ ساعت", html)
        self.assertIn("تلاش مجدد برای پرداخت", html)

    def test_json_callback_stays_functional_for_api_callers(self) -> None:
        """Calling /payments/callback without HTML accept must return JSON response model."""
        self.client.cookies.set(SESSION_COOKIE_NAME, self.session_cookie)

        order_resp = self.client.post(
            "/orders",
            json={
                "items": [{"product_id": str(self.product.id), "quantity": 1}],
            },
        )
        order_id = order_resp.json()["id"]

        start_resp = self.client.post(f"/payments/{order_id}/start")
        track_id = start_resp.json()["track_id"]

        self.zibal_state["verify_body"] = {
            "result": 100,
            "paidAt": "2026-11-14T13:30:00+00:00",
            "cardNumber": "603799******1122",
            "status": 1,
            "refNumber": 55443322,
            "amount": 250000000,
        }

        # API caller request (default Accept: */*)
        callback_resp = self.client.get(
            "/payments/callback",
            params={
                "trackId": track_id,
                "success": "1",
                "status": "1",
                "orderId": order_id,
            },
        )
        self.assertEqual(callback_resp.status_code, 200)
        self.assertIn("application/json", callback_resp.headers["content-type"])
        data = callback_resp.json()
        self.assertEqual(data["status"], "paid")
        self.assertEqual(data["ref_number"], "55443322")


if __name__ == "__main__":
    unittest.main()
