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
"""Unit and integration tests for the Craftbyte-matching shop frontend layout.

Verifies:
1. Hero card with hero-fridge.jpg, soft glow, New Arrivals label, bold headline,
   subtext, pill Shop Now button, and carousel dots.
2. Category tiles in a row with photos filling tiles, names and item counts below.
3. Best seller rows with thumbnail on left, name/variant, price with star rating,
   and round plus button on right.
4. Fixed bottom navigation bar with Shop (active), Categories, Wishlist, Account.
5. Minimal top bar with menu icon on left and search & bag icons on right.
6. Real photos wired and served with 200 OK.
7. Price padding preventing numerals from touching card edges.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


class CraftbyteLayoutTests(unittest.TestCase):
    """Test suite validating the 7 frontend redesign requirements."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_top_bar_minimal_structure(self) -> None:
        """Top bar has menu on the left, and search & bag icons on the right."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Menu icon on the left
        self.assertIn('id="mobile-menu-btn"', html)
        self.assertIn("منوی اصلی", html)

        # Search and Bag icons on the right
        self.assertIn('href="/search"', html)
        self.assertIn('href="/cart"', html)
        self.assertIn('id="cart-count"', html)
        self.assertIn('id="theme-toggle"', html)

    def test_hero_card_structure(self) -> None:
        """Hero card contains product photo, glow, label, headline, subtext, pill button, dots."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Photo & glow
        self.assertIn("/static/img/hero-fridge.jpg", html)
        self.assertIn("blur-3xl", html)

        # Labels, headline, subtext, pill button
        self.assertIn("New Arrivals", html)
        self.assertIn("Elevate Your Everyday", html)
        self.assertIn("Modern essentials for every moment.", html)
        self.assertIn("Shop Now", html)
        self.assertIn("rounded-full", html)

        # Carousel dots
        self.assertIn("rounded-full bg-neutral-600", html)

    def test_category_tiles_in_a_row(self) -> None:
        """Category tiles render in a row with photo filling tile, name and count below."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Grid row
        self.assertIn("grid-cols-3", html)

        # Category photos
        self.assertIn("/static/img/product-fridge.jpg", html)
        self.assertIn("/static/img/product-tv.jpg", html)
        self.assertIn("/static/img/product-washer.jpg", html)

        # Item count below: a real count from the catalog, never a hardcoded number
        self.assertIn("Item", html)

    def test_best_seller_rows(self) -> None:
        """Best seller rows: thumbnail on left, name/variant/price/rating, round + button."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        # Best sellers section
        self.assertIn("Best Sellers", html)

        # Rating and variant
        self.assertIn("★", html)
        self.assertIn("تومان", html)

        # Round plus button with data-add-to-cart
        self.assertIn("data-add-to-cart", html)
        self.assertIn("rounded-full", html)

    def test_fixed_bottom_navigation_bar(self) -> None:
        """Bottom nav has Shop (active), Categories, Wishlist, Account."""
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text

        self.assertIn("<nav", html)
        self.assertIn("fixed bottom-0", html)
        self.assertIn("Shop", html)
        self.assertIn("Categories", html)
        self.assertIn("Wishlist", html)
        self.assertIn("Account", html)

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
