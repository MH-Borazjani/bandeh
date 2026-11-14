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
"""Unit and integration tests for the Bandeh frontend (Stage 3).

Covers:
- My orders page returning 200 for signed-in buyer with status, total in Toman, and date.
- My orders page redirecting to /auth/login for guest.
- Order detail page returning 200 with items, delivery address, and payment state.
- Order detail page redirecting for guest.
- Search page returning 200 HTML with search box, category filter, and results.
- Category listing page returning 200 HTML showing products of one category from Directus.
- JSON search API backwards compatibility.
- Trust pages (terms, privacy, returns, contact) each returning 200 HTML and linked from footer.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

# Ensure app/e-commerce is in sys.path
APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.security import SESSION_COOKIE_NAME, create_session  # noqa: E402
from core.templates import to_compact_price  # noqa: E402
from db.base import Base  # noqa: E402
from db.models.order import Order, OrderItem, OrderStatus  # noqa: E402
from db.models.payment_attempt import PaymentAttempt, PaymentStatus  # noqa: E402
from db.models.product import Product  # noqa: E402
from db.models.user import User, UserRole  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.directus_client import (  # noqa: E402
    format_toman_price,
    get_directus_client,
)
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
    """Mock Directus client simulating CMS categories and products."""

    def __init__(self, products: dict[str, dict] | None = None) -> None:
        self.products = products or {}
        self.categories = [
            {"id": 1, "name": "یخچال و فریزر", "description": "ساید و دوقلو"},
            {"id": 2, "name": "ماشین لباسشویی", "description": "تمام اتوماتیک"},
        ]

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        return self.products.get(str(product_id))

    async def list_products(self, limit: int = 20) -> list[dict]:
        return list(self.products.values())[:limit]

    async def get_landing_content(self) -> dict:
        return {
            "categories": [{"name": c["name"], "desc": c["description"]} for c in self.categories],
            "products": list(self.products.values()),
        }

    async def get_category_map(self) -> dict[int | str, str]:
        mapping: dict[int | str, str] = {}
        for c in self.categories:
            mapping[c["id"]] = str(c["name"])
            mapping[str(c["id"])] = str(c["name"])
        return mapping

    async def list_categories(self) -> list[dict]:
        return list(self.categories)

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        cat_str = str(category_identifier).strip()
        matched: list[dict] = []
        for p in self.products.values():
            p_cat = str(p.get("category", ""))
            if p_cat == cat_str or (cat_str == "1" and "یخچال" in p.get("name", "")):
                matched.append(p)
            elif cat_str in p.get("name", "") or cat_str in p_cat:
                matched.append(p)
        return matched[:limit]


class FrontendStage3Tests(unittest.TestCase):
    """Test suite for the Stage 3 frontend requirements."""

    engine: AsyncEngine
    Session: async_sessionmaker[AsyncSession]
    client: TestClient
    user: User
    order: Order
    product: Product
    session_cookie: str

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

        async def seed() -> None:
            async with cls.Session() as s:
                u = User(phone="09121112233", role=UserRole.CUSTOMER, is_active=True)
                p = Product(
                    name="یخچال فریزر هوشمند بنده",
                    description="سیستم نوفراست و مصرف انرژی کم",
                    price_toman=42000000,
                    stock=5,
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

                # Create an order with items and payment attempt
                order = Order(
                    user_id=u.id,
                    status=OrderStatus.PAID.value,
                    total_toman=42000000,
                    total_rial=420000000,
                    recipient_name="احمد رضایی",
                    recipient_phone="09129998877",
                    delivery_address="تهران، خیابان ولیعصر، کوچه بهار، پلاک ۱۰",
                )
                s.add(order)
                await s.flush()

                item = OrderItem(
                    order_id=order.id,
                    product_id=p.id,
                    product_name=p.name,
                    quantity=1,
                    unit_price_toman=p.price_toman,
                )
                attempt = PaymentAttempt(
                    order_id=order.id,
                    gateway="zibal",
                    track_id=102030,
                    amount_rial=420000000,
                    status=PaymentStatus.PAID.value,
                )
                s.add_all([item, attempt])
                await s.commit()
                await s.refresh(order)
                cls.order = order

        asyncio.run(seed())

        fake_cms = FakeDirectusClient(
            products={
                str(cls.product.id): {
                    "id": str(cls.product.id),
                    "name": cls.product.name,
                    "description": cls.product.description,
                    "price_toman": cls.product.price_toman,
                    "stock": cls.product.stock,
                    "reserved": cls.product.reserved,
                    "is_active": True,
                    "category": 1,
                    "tag": "پیشنهاد ویژه",
                    "created_at": "2026-11-14T10:00:00Z",
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

    # --- 1. My Orders Page Tests ---

    def test_my_orders_page_for_signed_in_buyer_returns_200(self) -> None:
        """My orders page must return 200 for signed-in buyer and render orders list."""
        auth_client = TestClient(app, cookies={SESSION_COOKIE_NAME: self.session_cookie})
        resp = auth_client.get(
            "/orders",
            headers={"accept": "text/html"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        # Verify page title and header
        self.assertIn("سفارش‌های من", html)

        # Verify order details present
        self.assertIn(str(self.order.id), html)
        self.assertIn("پرداخت شده", html)
        expected_price = format_toman_price(42000000)
        self.assertIn(expected_price, html)
        self.assertIn("تومان", html)

        # Verify link to detail view
        self.assertIn(f"/orders/{self.order.id}", html)

    def test_my_orders_page_for_guest_redirects(self) -> None:
        """My orders page must redirect unauthenticated visitors to /auth/login."""
        guest_client = TestClient(app)
        resp = guest_client.get("/orders", follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers.get("location"), "/auth/login")

    def test_order_detail_view_for_signed_in_buyer_returns_200(self) -> None:
        """Order detail view must return 200 for signed-in buyer with items, address, payment."""
        auth_client = TestClient(app, cookies={SESSION_COOKIE_NAME: self.session_cookie})
        resp = auth_client.get(
            f"/orders/{self.order.id}",
            headers={"accept": "text/html"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        # Order ID & Status
        self.assertIn(str(self.order.id), html)
        self.assertIn("پرداخت شده", html)

        # Items details
        self.assertIn(self.product.name, html)
        self.assertIn(format_toman_price(self.product.price_toman), html)

        # Delivery address details
        self.assertIn("احمد رضایی", html)
        self.assertIn("09129998877", html)
        self.assertIn("تهران، خیابان ولیعصر", html)

        # Payment state
        self.assertIn("پرداخت", html)
        self.assertIn("102030", html)

    def test_order_detail_view_for_guest_redirects(self) -> None:
        """Order detail view must redirect guest to /auth/login."""
        guest_client = TestClient(app)
        resp = guest_client.get(
            f"/orders/{self.order.id}",
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303)
        self.assertEqual(resp.headers.get("location"), "/auth/login")

    # --- 2. Search & Category Listing Page Tests ---

    def test_search_page_returns_200(self) -> None:
        """GET /search must return 200 HTML with search box and category filter."""
        resp = self.client.get("/search", headers={"accept": "text/html"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        # Search box form and category chips (no dropdown anymore)
        self.assertIn('name="q"', html)
        self.assertIn("همه دسته‌ها", html)
        self.assertIn("/category/", html)
        self.assertIn("جستجو", html)

    def test_search_page_with_query_returns_results(self) -> None:
        """GET /search?q=یخچال must return 200 HTML with matching product cards."""
        resp = self.client.get(
            "/search",
            params={"q": "یخچال"},
            headers={"accept": "text/html"},
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("یخچال فریزر هوشمند بنده", html)
        compact = to_compact_price(42000000)
        self.assertIn(compact["value"], html)
        self.assertIn(compact["unit"], html)
        self.assertNotIn(format_toman_price(42000000), html)

    def test_search_json_api_kept_working(self) -> None:
        """GET /search must continue returning JSON list of ProductRead for API callers."""
        resp = self.client.get("/search", params={"q": "یخچال"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertEqual(data[0]["name"], "یخچال فریزر هوشمند بنده")

    def test_category_page_returns_200(self) -> None:
        """GET /category/{id} must return 200 HTML showing products of that category."""
        resp = self.client.get("/category/1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        html = resp.text

        # Category title and products
        self.assertIn("یخچال و فریزر", html)
        self.assertIn("یخچال فریزر هوشمند بنده", html)
        compact = to_compact_price(42000000)
        self.assertIn(compact["value"], html)
        self.assertIn(compact["unit"], html)
        self.assertNotIn(format_toman_price(42000000), html)

    # --- 3. Trust Pages Tests ---

    def test_trust_pages_return_200(self) -> None:
        """Terms, privacy, returns, and contact trust pages must each return 200 HTML."""
        pages = [
            ("/terms", "قوانین و مقررات"),
            ("/privacy", "حریم خصوصی"),
            ("/returns", "بازگشت"),
            ("/contact", "تماس با ما"),
        ]
        for path, keyword in pages:
            with self.subTest(page=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, f"{path} did not return 200")
                self.assertIn("text/html", resp.headers["content-type"])
                self.assertIn(keyword, resp.text)

    def test_footer_contains_trust_links(self) -> None:
        """Shared footer must link to all 4 trust pages."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn('href="/terms"', html)
        self.assertIn('href="/privacy"', html)
        self.assertIn('href="/returns"', html)
        self.assertIn('href="/contact"', html)


if __name__ == "__main__":
    unittest.main()
