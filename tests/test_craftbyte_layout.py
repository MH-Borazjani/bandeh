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
"""Unit and integration tests for the Persian RTL storefront redesign.

Verifies the 8 owner fixes:
1. Full Persian strings: hero label, headline, subtext, button, headings, variants, item counts.
2. Complete RTL layout: html lang fa dir rtl, no dir ltr overrides, text-right mirroring.
3. Photos fill whole box: object-cover with no inner padding everywhere.
4. Hero dots removed completely.
5. Hamburger menu and drawer markup removed.
6. Cart in bottom nav with cart count badge; Wishlist removed.
7. Bag icon removed from top bar.
8. Bottom nav section highlighting in amber based on route.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


def _mock_directus_fa(request: httpx.Request) -> httpx.Response:
    """Directus with the real Persian test catalog (ids + prices)."""
    if request.url.path.endswith("/items/categories"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": 1, "name": "آشپزخانه [TEST]", "description": ""},
                    {"id": 2, "name": "تلویزیون و صدا [TEST]", "description": ""},
                    {"id": 3, "name": "شستشو [TEST]", "description": ""},
                ]
            },
        )
    if request.url.path.endswith("/items/products"):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "123e4567-e89b-12d3-a456-426614174000",
                        "name": "یخچال دو درب [TEST]",
                        "price_toman": 45000000,
                        "tag": "پرفروش",
                        "category": 1,
                        "image_path": "product-fridge.jpg",
                    },
                    {
                        "id": "223e4567-e89b-12d3-a456-426614174001",
                        "name": "تلویزیون ۵۵ اینچ [TEST]",
                        "price_toman": 32000000,
                        "tag": None,
                        "category": 2,
                        "image_path": "product-tv.jpg",
                    },
                    {
                        "id": "323e4567-e89b-12d3-a456-426614174002",
                        "name": "ماشین لباسشویی ۸ کیلویی [TEST]",
                        "price_toman": 28500000,
                        "tag": None,
                        "category": 3,
                        "image_path": "product-washer.jpg",
                    },
                ]
            },
        )
    return httpx.Response(404, json={"errors": [{"message": "not found"}]})


class CraftbyteLayoutTests(unittest.TestCase):
    """Test suite validating the 8 owner fixes."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_top_bar_minimal_structure(self) -> None:
        """Top bar has Brand and search & theme toggle; menu and bag icon removed."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Menu icon and drawer removed
        self.assertNotIn('id="mobile-menu-btn"', html)
        self.assertNotIn('id="mobile-menu-drawer"', html)
        self.assertNotIn("منوی اصلی", html)

        # Search icon and Theme toggle present
        self.assertIn('href="/search"', html)
        self.assertIn('id="theme-toggle"', html)

        # Top bar header does NOT contain bag icon / cart link
        header_html = html.split("<header")[1].split("</header>")[0]
        self.assertNotIn('href="/cart"', header_html)
        self.assertNotIn('id="cart-count"', header_html)

    def test_hero_card_structure(self) -> None:
        """Hero card contains product photo, glow, exact Persian text, and NO dots."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Photo & glow
        self.assertIn("/static/img/hero-fridge.jpg", html)
        self.assertIn("object-cover", html)
        self.assertIn("blur-3xl", html)

        # Exact Persian strings
        self.assertIn("تازه‌ها", html)
        self.assertIn("خانه‌ی مدرن، زندگی بهتر", html)
        self.assertIn("وسایل ضروری مدرن برای هر لحظه از روز", html)
        self.assertIn("مشاهده محصولات", html)
        self.assertIn("rounded-full", html)

        # Carousel dots removed completely
        self.assertNotIn("rounded-full bg-neutral-600", html)

    def test_category_tiles_in_a_row(self) -> None:
        """Category tiles render in a row with photo filling tile, exact Persian heading."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Exact Persian headings
        self.assertIn("دسته‌بندی‌ها", html)
        self.assertIn("مشاهده همه", html)

        # Grid row
        self.assertIn("grid-cols-3", html)

        # Category photos with object-cover (fallback data: fridge + washer)
        self.assertIn("/static/img/product-fridge.jpg", html)
        self.assertIn("/static/img/product-washer.jpg", html)
        self.assertIn("object-cover", html)

        # Item count in Persian digits with the word کالا
        self.assertIn("کالا", html)

    def test_best_seller_rows(self) -> None:
        """Best seller rows: thumbnail on right, exact heading, Persian colour variants."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Exact heading
        self.assertIn("پرفروش‌ترین‌ها", html)

        # Persian colour variants
        self.assertIn("مشکی", html)
        self.assertIn("سفید", html)
        self.assertIn("تومان", html)

        # Round plus button with data-add-to-cart
        self.assertIn("data-add-to-cart", html)
        self.assertIn("rounded-full", html)

    def test_fixed_bottom_navigation_bar(self) -> None:
        """Bottom nav has فروشگاه, دسته‌بندی‌ها, سبد خرید with cart-count badge, حساب کاربری."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn("<nav", html)
        self.assertIn("fixed bottom-0", html)
        self.assertIn("فروشگاه", html)
        self.assertIn("دسته‌بندی‌ها", html)
        self.assertIn("سبد خرید", html)
        self.assertIn("حساب کاربری", html)

        # Wishlist removed
        self.assertNotIn("Wishlist", html)

        # Cart count badge is in bottom nav
        nav_html = html.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn('id="cart-count"', nav_html)
        self.assertIn('href="/cart"', nav_html)

    def test_bottom_navigation_active_highlighting(self) -> None:
        """Bottom nav highlights active section in amber based on route."""
        amber_cls = "theme-accent font-semibold text-[#d97706]"

        # 1. Home page: Shop is active
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn('href="/"', nav)
        self.assertIn(amber_cls, nav)
        self.assertIn("فروشگاه", nav)

        # 2. Search page: NO section is active (search has no own button)
        resp = self.client.get("/search", headers={"Accept": "text/html"})
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertNotIn(amber_cls, nav)

        # 2b. Category page: Categories is active
        resp = self.client.get("/category/1", headers={"Accept": "text/html"})
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn(amber_cls, nav)
        self.assertIn("دسته‌بندی‌ها", nav)

        # 3. Cart page: Cart is active
        resp = self.client.get("/cart")
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn('href="/cart"', nav)
        self.assertIn(amber_cls, nav)
        self.assertIn("سبد خرید", nav)

        # 4. Login page: Account is active
        resp = self.client.get("/auth/login")
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn('href="/auth/login"', nav)
        self.assertIn(amber_cls, nav)
        self.assertIn("حساب کاربری", nav)

        # 5. Orders page: Account is active
        resp = self.client.get("/orders")
        self.assertEqual(resp.status_code, 200)
        nav = resp.text.split('aria-label="ناوبری اصلی فروشگاه"')[1].split("</nav>")[0]
        self.assertIn(amber_cls, nav)
        self.assertIn("حساب کاربری", nav)

    def test_rtl_attributes_and_no_ltr_overrides(self) -> None:
        """HTML tag has lang fa and dir rtl, and no dir ltr overrides exist."""
        pages = ["/", "/search", "/cart", "/checkout", "/terms", "/privacy", "/returns", "/contact"]
        for path in pages:
            with self.subTest(page=path):
                resp = self.client.get(path, headers={"Accept": "text/html"})
                self.assertEqual(resp.status_code, 200)
                html = resp.text
                self.assertIn('lang="fa"', html)
                self.assertIn('dir="rtl"', html)
                self.assertNotIn('dir="ltr"', html)
                self.assertNotIn('text-left', html)

    def test_real_photos_served_200(self) -> None:
        """All 4 real product/hero photos are served with HTTP 200."""
        photos = [
            "hero-fridge.jpg",
            "product-fridge.jpg",
            "product-tv.jpg",
            "product-washer.jpg",
        ]
        for name in photos:
            with self.subTest(photo=name):
                resp = self.client.get(f"/static/img/{name}")
                self.assertEqual(resp.status_code, 200, f"/static/img/{name} failed")
                self.assertEqual(resp.headers.get("content-type"), "image/jpeg")

    def test_no_star_ratings(self) -> None:
        """Star ratings are removed from the products (fake demo values)."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("★", resp.text)

    def test_search_category_chips(self) -> None:
        """Search page has tappable category chips, no dropdown select."""
        resp = self.client.get("/search", headers={"Accept": "text/html"})
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertNotIn("<select", html)
        self.assertNotIn("category-select", html)
        self.assertIn("همه دسته‌ها", html)
        self.assertIn("/category/", html)

    def test_compact_prices_on_landing(self) -> None:
        """Landing shows compact prices (۳۲ میلیون), never the full zeros."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("میلیون", html)
        self.assertNotIn("۴۵٬۰۰۰٬۰۰۰", html)
        self.assertNotIn("۲۸٬۵۰۰٬۰۰۰", html)

    def test_product_photo_links_with_mocked_directus(self) -> None:
        """With Directus rows (id + price), photos and names link to the
        product page, prices are compact, and there are no star ratings."""
        from core.cache import reset_cache_for_tests
        from integrations.http_client import create_http_client, get_http_client

        reset_cache_for_tests()

        def _mock_http_client() -> httpx.AsyncClient:
            return create_http_client(
                transport=httpx.MockTransport(_mock_directus_fa)
            )

        app.dependency_overrides[get_http_client] = _mock_http_client
        try:
            with TestClient(app) as client:
                resp = client.get("/")
                self.assertEqual(resp.status_code, 200)
                html = resp.text
                self.assertIn(
                    'href="/products/123e4567-e89b-12d3-a456-426614174000"', html
                )
                self.assertIn(
                    'href="/products/223e4567-e89b-12d3-a456-426614174001"', html
                )
                self.assertIn(
                    'href="/products/323e4567-e89b-12d3-a456-426614174002"', html
                )
                # All three category tile photos (Persian names resolve)
                self.assertIn("/static/img/product-fridge.jpg", html)
                self.assertIn("/static/img/product-tv.jpg", html)
                self.assertIn("/static/img/product-washer.jpg", html)
                # Compact prices
                self.assertIn("میلیون", html)
                self.assertIn("۴۵", html)
                self.assertNotIn("۴۵٬۰۰۰٬۰۰۰", html)
                self.assertNotIn("★", html)
        finally:
            app.dependency_overrides.pop(get_http_client, None)
            reset_cache_for_tests()

    def test_stepper_in_shop_js(self) -> None:
        """shop.js builds a quantity stepper (- qty +) for add-to-cart
        buttons on listing pages, restoring the + button at qty 0."""
        shop_js = (APP_DIR / "static" / "js" / "shop.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("showStepper", shop_js)
        self.assertIn("data-stepper-for", shop_js)
        self.assertIn("getDraftQty", shop_js)

    def test_price_padding_never_touches_card_edge(self) -> None:
        """Price containers have explicit padding so numerals never touch card edge."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Card container padding
        self.assertIn("p-3.5", html)
        # Price container padding
        self.assertIn("gap-2.5", html)


if __name__ == "__main__":
    unittest.main()

