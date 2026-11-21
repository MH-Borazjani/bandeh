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
"""Unit and integration tests for the shop PWA (manifest, service worker,
icons, and the PWA tags on every page). Run from repo root:

    uv run python -m unittest discover -s tests
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


class TestPwa(unittest.TestCase):
    """The PWA contract: manifest + worker served, icons exist, pages link it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def test_manifest_served_and_valid(self) -> None:
        r = self.client.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIn("manifest+json", r.headers["content-type"])
        manifest = json.loads(r.text)
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["short_name"], "گنابِی")
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        purposes = {icon.get("purpose", "any") for icon in manifest["icons"]}
        self.assertIn("maskable", purposes)

    def test_manifest_icons_exist(self) -> None:
        manifest = json.loads(self.client.get("/manifest.webmanifest").text)
        for icon in manifest["icons"]:
            r = self.client.get(icon["src"])
            self.assertEqual(r.status_code, 200, icon["src"])
            self.assertIn("image/png", r.headers["content-type"])

    def test_service_worker_served_from_root(self) -> None:
        # Root scope matters: a worker under /static/ could only cache /static/*.
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200, r.text[:200])
        self.assertIn("javascript", r.headers["content-type"])
        self.assertIn("no-cache", r.headers["cache-control"])
        self.assertIn("genabay-pwa-v2", r.text)
        # Money and login routes must never be cached.
        for prefix in ("/payments/", "/auth/", "/api/", "/cart", "/checkout"):
            self.assertIn(prefix, r.text)

    def test_favicon_served(self) -> None:
        r = self.client.get("/favicon.ico")
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/x-icon", r.headers["content-type"])
        self.assertGreater(len(r.content), 1000)

    def test_base_template_links_pwa(self) -> None:
        # Read the template file directly, not a live GET "/": the live
        # landing request shares the app's event loop with the other
        # check scripts, and in a combined run that loop can already be
        # closed by an earlier test's teardown (the same flake family as
        # check_search.py). Page rendering itself is check_pages.py's job.
        base = (APP_DIR / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn('rel="manifest" href="/manifest.webmanifest"', base)
        self.assertIn('name="theme-color"', base)
        self.assertIn('rel="apple-touch-icon"', base)
        self.assertIn('navigator.serviceWorker.register("/sw.js")', base)

    def test_apple_touch_icon_served(self) -> None:
        r = self.client.get("/static/img/icons/apple-touch-icon.png")
        self.assertEqual(r.status_code, 200)
        self.assertIn("image/png", r.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
