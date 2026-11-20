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
"""Unit and integration tests for responsive desktop layout and SPA feel with HTMX."""

from __future__ import annotations

import datetime
import json
import sys
import unittest
import uuid
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from integrations.directus_client import get_directus_client  # noqa: E402
from main import app  # noqa: E402


class MockDirectusClient:
    """Mock Directus client simulating CMS responses for test catalog."""

    def __init__(self) -> None:
        self.sample_id = "123e4567-e89b-12d3-a456-426614174000"
        self.sample_prod = {
            "id": self.sample_id,
            "name": "یخچال فریزر دوقلو [TEST]",
            "description": "توضیحات تست محصول یخچال",
            "price_toman": 45000000,
            "stock": 5,
            "reserved": 0,
            "is_active": True,
            "created_at": datetime.datetime.now(datetime.UTC),
            "category": "آشپزخانه",
            "tag": "پرفروش",
            "image_path": "product-fridge.jpg",
        }

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        if str(product_id) == self.sample_id:
            return self.sample_prod
        return None

    async def list_products(self, limit: int = 20) -> list[dict]:
        return [self.sample_prod]

    async def get_landing_content(self) -> dict:
        return {
            "categories": [
                {"name": "یخچال و فریزر"},
                {"name": "ماشین لباسشویی"},
                {"name": "ماشین ظرفشویی"},
                {"name": "اجاق گاز و فر"},
                {"name": "جاروبرقی"},
            ],
            "products": [self.sample_prod],
        }


class DesktopAndSpaTests(unittest.TestCase):
    """Test suite verifying desktop responsive layout, SPA feel and new address fields."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.mock_directus = MockDirectusClient()
        app.dependency_overrides[get_directus_client] = lambda: self.mock_directus

    def tearDown(self) -> None:
        app.dependency_overrides.pop(get_directus_client, None)

    def test_desktop_container_and_padding_classes(self) -> None:
        """Main, header and footer grow to 1200px max on desktop with increased padding."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Container wide classes
        self.assertIn("lg:max-w-[1200px]", html)
        self.assertIn("max-w-md", html)
        self.assertIn("lg:px-8", html)

        # Main element has the responsive container
        self.assertIn('<main id="main" class="flex-1 max-w-md lg:max-w-[1200px]', html)

    def test_desktop_navigation_presence_and_links(self) -> None:
        """Top header navigation exists for desktop with Shop, Categories, Cart, and Account."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn('id="desktop-nav"', html)
        self.assertIn("hidden lg:flex", html)

        # Nav links
        desktop_nav = html.split('id="desktop-nav"')[1].split("</nav>")[0]
        self.assertIn('href="/"', desktop_nav)
        self.assertIn("فروشگاه", desktop_nav)
        self.assertIn('href="/#categories"', desktop_nav)
        self.assertIn("دسته‌بندی‌ها", desktop_nav)
        self.assertIn('href="/cart"', desktop_nav)
        self.assertIn("سبد خرید", desktop_nav)
        self.assertIn('id="desktop-cart-count"', desktop_nav)
        self.assertIn("حساب کاربری", desktop_nav)

    def test_bottom_nav_hidden_on_desktop(self) -> None:
        """Bottom nav stays only on mobile and is hidden on desktop via lg:hidden."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn("lg:hidden", html)
        bottom_nav_part = html.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn("فروشگاه", bottom_nav_part)
        self.assertIn("سبد خرید", bottom_nav_part)

    def test_categories_trust_badge_style_and_no_photos(self) -> None:
        """Category section uses trust-badge style cards, icon circles, and more button."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn('id="categories"', html)
        # Trust-badge card styling on categories
        self.assertIn("theme-bg-card theme-border border rounded-2xl", html)
        self.assertIn(
            "rounded-full theme-bg-card-subtle flex items-center justify-center mb-2 theme-accent",
            html,
        )

        # More button exists
        self.assertIn('id="toggle-more-categories"', html)
        self.assertIn("مشاهده بیشتر دسته‌ها", html)

    def test_best_sellers_multi_column_grid_desktop(self) -> None:
        """Best sellers section becomes multi-column grid on desktop."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn("lg:grid lg:grid-cols-2 xl:grid-cols-3", html)

    def test_product_page_two_columns_desktop(self) -> None:
        """Product page becomes two columns on desktop: info right, photo left."""
        headers = {"Accept": "text/html"}
        resp = self.client.get(f"/products/{self.mock_directus.sample_id}", headers=headers)
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("lg:grid lg:grid-cols-2", html)
        self.assertIn("order-2 lg:order-1", html)  # Info
        self.assertIn("order-1 lg:order-2", html)  # Photo

    def test_checkout_form_fields(self) -> None:
        """Checkout form has recipient name, mobile, province, city, and new fields."""
        resp = self.client.get("/checkout")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn('id="recipient-name"', html)
        self.assertIn('id="recipient-phone"', html)
        self.assertIn('id="recipient-province"', html)
        self.assertIn('id="recipient-city"', html)
        self.assertIn('id="postal-code"', html)
        self.assertIn('id="landline-phone"', html)
        self.assertIn('id="house-plaque"', html)
        self.assertIn('id="delivery-address"', html)

        # Desktop 2-column layout on checkout
        self.assertIn("lg:grid lg:grid-cols-3", html)

    def test_iran_cities_json_file(self) -> None:
        """Static JSON file with real provinces and cities of Iran exists and is valid."""
        json_path = APP_DIR / "static" / "data" / "iran_cities.json"
        self.assertTrue(json_path.exists(), f"File {json_path} must exist")

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        self.assertIsInstance(data, dict)
        self.assertIn("تهران", data)
        self.assertIn("اصفهان", data)
        self.assertIn("فارس", data)
        self.assertIn("خراسان رضوی", data)
        self.assertIn("شیراز", data["فارس"])
        self.assertIn("مشهد", data["خراسان رضوی"])

    def test_spa_htmx_fragment_swap(self) -> None:
        """When requested with HX-Request header, routes return only main content fragment."""
        # 1. Landing page with HTMX
        resp = self.client.get("/", headers={"HX-Request": "true"})
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertNotIn("<!DOCTYPE html>", html)
        self.assertNotIn('<html lang="fa"', html)
        self.assertNotIn("<header", html)
        self.assertNotIn("<footer", html)
        self.assertIn("<title>", html)
        self.assertIn("بنده", html)

        # 2. Cart page with HTMX
        resp_cart = self.client.get("/cart", headers={"HX-Request": "true"})
        self.assertEqual(resp_cart.status_code, 200)
        self.assertNotIn("<!DOCTYPE html>", resp_cart.text)
        self.assertIn("<title>", resp_cart.text)
        self.assertIn("سبد خرید", resp_cart.text)

        # 3. Checkout page with HTMX
        resp_checkout = self.client.get("/checkout", headers={"HX-Request": "true"})
        self.assertEqual(resp_checkout.status_code, 200)
        self.assertNotIn("<!DOCTYPE html>", resp_checkout.text)
        self.assertIn("<title>", resp_checkout.text)
        self.assertIn("تسویه حساب", resp_checkout.text)

    def test_live_search_endpoint(self) -> None:
        """Live search fragment returns matching products or hint."""
        resp = self.client.get("/api/search-live?q=")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("نام کالا یا برند را تایپ کنید", resp.text)

        resp_results = self.client.get("/api/search-live?q=یخچال")
        self.assertEqual(resp_results.status_code, 200)
        self.assertIn("search-result-item", resp_results.text)


if __name__ == "__main__":
    unittest.main()
