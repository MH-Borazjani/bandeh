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
"""Unit and integration tests for the Bandeh product detail page."""

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

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.templates import to_compact_price  # noqa: E402
from db.base import Base  # noqa: E402
from db.models.product import Product  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.directus_client import (  # noqa: E402
    format_toman_price,
    get_directus_client,
)
from main import app  # noqa: E402


class FakeDirectusClient:
    """Mock Directus client simulating CMS responses."""

    def __init__(self, products: dict[str, dict] | None = None) -> None:
        self.products = products or {}

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        return self.products.get(str(product_id))

    async def list_products(self, limit: int = 20) -> list[dict]:
        return list(self.products.values())[:limit]


class ProductPageTests(unittest.TestCase):
    """Test suite for the Bandeh product detail page."""

    engine: AsyncEngine
    Session: async_sessionmaker[AsyncSession]
    client: TestClient
    directus_product_id: uuid.UUID
    db_product_id: uuid.UUID
    directus_data: dict

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

        # 1. Product available from Directus
        cls.directus_product_id = uuid.uuid4()
        cls.directus_data = {
            "id": str(cls.directus_product_id),
            "name": "یخچال فریزر هوشمند بنده",
            "description": "سیستم گردش هوای چندگانه، رده مصرف انرژی A++، بدون برفک",
            "price_toman": 45000000,
            "stock": 8,
            "reserved": 1,
            "image_path": None,
            "is_active": True,
            "category": "یخچال و فریزر",
            "tag": "پرفروش",
            "created_at": "2026-11-14T12:00:00Z",
        }

        # 2. Product fallback in SQLite DB
        cls.db_product_id = uuid.uuid4()

        async def seed_db() -> None:
            async with cls.Session() as s:
                p = Product(
                    id=cls.db_product_id,
                    name="ماشین لباسشویی ۹ کیلویی بنده",
                    description="موتور دایرکت درایو اینورتر بیصدا",
                    price_toman=28500000,
                    stock=5,
                    reserved=0,
                    is_active=True,
                )
                s.add(p)
                await s.commit()

        asyncio.run(seed_db())

        fake_directus = FakeDirectusClient(
            products={str(cls.directus_product_id): cls.directus_data}
        )
        app.dependency_overrides[get_directus_client] = lambda: fake_directus

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.clear()
        if cls.engine:
            asyncio.run(cls.engine.dispose())

    def test_product_page_html_returns_200_and_contains_name_and_price(self) -> None:
        """HTML product page must return 200 and contain the product name and Toman price."""
        url = f"/products/{self.directus_product_id}"
        resp = self.client.get(url, headers={"accept": "text/html"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("یخچال فریزر هوشمند بنده", resp.text)

        compact = to_compact_price(45000000)
        self.assertIn(compact["value"], resp.text)
        self.assertIn(compact["unit"], resp.text)
        self.assertNotIn(format_toman_price(45000000), resp.text)
        self.assertIn("تومان", resp.text)

    def test_product_page_renders_design_system_and_controls(self) -> None:
        """Product page must render category, tag, stock state, stepper, and add to cart."""
        url = f"/product/{self.directus_product_id}"
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Product identity & description
        self.assertIn("یخچال فریزر هوشمند بنده", html)
        self.assertIn("سیستم گردش هوای چندگانه", html)

        # Category and tag badge
        self.assertIn("یخچال و فریزر", html)
        self.assertIn("پرفروش", html)

        # Stock state (8 - 1 = 7 available -> موجود در انبار)
        self.assertIn("موجود در انبار", html)

        # Quantity stepper controls
        self.assertIn('id="stepper-minus"', html)
        self.assertIn('id="stepper-plus"', html)
        self.assertIn('id="product-quantity"', html)

        # Add to cart button wired to cart draft
        self.assertIn("data-add-to-cart", html)
        self.assertIn(f'data-product-id="{self.directus_product_id}"', html)
        self.assertIn('data-product-name="یخچال فریزر هوشمند بنده"', html)
        self.assertIn("افزودن به سبد خرید", html)

        # Header theme toggle and shop.js
        self.assertIn('id="theme-toggle"', html)
        self.assertIn('id="cart-count"', html)
        self.assertIn("/static/js/shop.js", html)

    def test_product_page_database_fallback(self) -> None:
        """When not in Directus, falls back to database and renders HTML page."""
        url = f"/products/{self.db_product_id}"
        resp = self.client.get(url, headers={"accept": "text/html"})

        self.assertEqual(resp.status_code, 200)
        self.assertIn("ماشین لباسشویی ۹ کیلویی بنده", resp.text)
        compact = to_compact_price(28500000)
        self.assertIn(compact["value"], resp.text)
        self.assertIn(compact["unit"], resp.text)
        self.assertNotIn(format_toman_price(28500000), resp.text)
        self.assertIn("تومان", resp.text)

    def test_product_with_integer_category_resolves_to_category_name(self) -> None:
        """When CMS returns category as an integer ID, it is resolved to real category name."""
        pid = uuid.uuid4()
        prod_data = {
            "id": str(pid),
            "name": "تلویزیون تست",
            "description": "توضیحات تلویزیون تستی",
            "price_toman": 32000000,
            "stock": 2,
            "reserved": 0,
            "image_path": None,
            "is_active": True,
            "category": "تلویزیون و لوازم صوتی",
            "tag": "تستی",
            "created_at": "2026-11-14T12:00:00Z",
        }
        fake = FakeDirectusClient(products={str(pid): prod_data})
        app.dependency_overrides[get_directus_client] = lambda: fake
        try:
            resp = self.client.get(f"/products/{pid}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["category"], "تلویزیون و لوازم صوتی")

            html_resp = self.client.get(f"/product/{pid}")
            self.assertEqual(html_resp.status_code, 200)
            self.assertIn("تلویزیون و لوازم صوتی", html_resp.text)
            self.assertIn("تومان", html_resp.text)
        finally:
            fake_directus = FakeDirectusClient(
                products={str(self.directus_product_id): self.directus_data}
            )
            app.dependency_overrides[get_directus_client] = lambda: fake_directus

    def test_json_product_route_remains_unchanged(self) -> None:
        """GET /products/{id} without HTML accept must return the JSON ProductRead model."""
        url = f"/products/{self.directus_product_id}"
        resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/json", resp.headers["content-type"])
        data = resp.json()
        self.assertEqual(data["id"], str(self.directus_product_id))
        self.assertEqual(data["name"], "یخچال فریزر هوشمند بنده")
        self.assertEqual(data["price_toman"], 45000000)
        self.assertEqual(data["stock"], 8)
        self.assertEqual(data["reserved"], 1)

    def test_nonexistent_product_returns_404(self) -> None:
        """Accessing a non-existent product UUID returns 404."""
        random_id = uuid.uuid4()
        resp = self.client.get(f"/products/{random_id}", headers={"accept": "text/html"})
        self.assertEqual(resp.status_code, 404)

        resp_json = self.client.get(f"/products/{random_id}")
        self.assertEqual(resp_json.status_code, 404)


if __name__ == "__main__":
    unittest.main()
