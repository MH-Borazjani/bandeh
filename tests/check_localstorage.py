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
"""Runnable self-check for localStorage (templates only). Run from repo root:

    python tests/check_localstorage.py

No browser here, so this checks what CAN be checked without one:
the landing page wires the toggle/badge/buttons, the JS file is
served, it is valid JavaScript (node --check), it only ever stores
the two allowed keys, and it never mentions anything login-related.
The behaviour itself (click -> draft grows) is for a real browser.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
sys.path.insert(0, str(APP_DIR))

from fastapi.testclient import TestClient

from main import app

JS_PATH = APP_DIR / "static" / "js" / "shop.js"
BASE_PATH = APP_DIR / "templates" / "base.html"

# Keys that may ever be written to browser storage. Nothing else.
ALLOWED_KEYS = ["bandeh-cart-draft", "bandeh-theme"]

# Words that must never appear in the shop script: login-related state
# is server-owned and must never be stored in the browser.
FORBIDDEN_WORDS = ["bandeh_session", "bandeh_refresh", "password", "token", "secret", "cookie"]


def main() -> None:
    client = TestClient(app)

    # 1. Landing page wires the toggle, the badge, the buttons ------------
    resp = client.get("/")
    assert resp.status_code == 200, resp.status_code
    html = resp.text
    assert "/static/js/shop.js" in html, "shop.js is not referenced"
    assert 'id="theme-toggle"' in html, "theme toggle button missing"
    assert 'id="cart-count"' in html, "cart badge missing"
    assert "data-add-to-cart" in html, "add-to-cart buttons missing"
    assert 'data-product-id="sample-' in html, "sample product ids missing"
    print("   [OK] landing wires toggle, badge, add-to-cart buttons")

    # 2. The JS file is served ----------------------------------------------
    resp = client.get("/static/js/shop.js")
    assert resp.status_code == 200, resp.status_code
    js = resp.text
    assert "localStorage" in js
    for name in (
        "getCartDraft",
        "addToCartDraft",
        "removeFromCartDraft",
        "clearCartDraft",
        "cartDraftCount",
        "getTheme",
        "setTheme",
        "toggleTheme",
    ):
        assert name in js, f"{name} missing from shop.js"
    print("   [OK] shop.js served with the draft + theme functions")

    # 3. Only the two allowed keys, enforced by an allowlist ----------------
    assert "ALLOWED_KEYS" in js, "no allowlist in shop.js"
    for key in ALLOWED_KEYS:
        assert f'"{key}"' in js, f"allowed key {key} missing"
    assert "refused to store key" in js, "safeSet does not refuse unknown keys"
    print("   [OK] allowlist: only bandeh-cart-draft and bandeh-theme")

    # 4. Nothing login-related is ever mentioned ------------------------------
    lowered = js.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, f"forbidden word {word!r} in shop.js"
    print("   [OK] no login-related words in shop.js")

    # 5. Valid JavaScript ------------------------------------------------------
    proc = subprocess.run(
        ["node", "--check", str(JS_PATH)], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    print("   [OK] node --check: valid JavaScript")

    # 6. Warm theme: still dark, never white ----------------------------------
    base = BASE_PATH.read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", base, re.DOTALL)
    assert style is not None, "no <style> block in base.html"
    style_text = style.group(1).lower()
    assert '[data-theme="warm"]' in style_text
    for bad in ("#ffffff", "#fff", "white"):
        assert bad not in style_text, f"warm theme contains {bad!r}"
    assert "bandeh-theme" in base, "theme bootstrap script missing"
    print("   [OK] warm theme is dark-only (no white anywhere)")

    print("All localStorage checks passed.")


if __name__ == "__main__":
    main()
