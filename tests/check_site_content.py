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
"""Runnable self-check for the CMS-driven site content. Run from repo root:

    python tests/check_site_content.py

In-memory SQLite only — no server, no live Postgres, no live Directus:
the Directus HTTP calls are answered by an httpx MockTransport with
canned collection rows, so the real client, middleware, routes and
templates are exercised end to end. Covers: the HTMX ``[]`` bug
(regression: an HTMX search/product request must return HTML, never
JSON), the /categories page, the condition label + stars (used product
shows «دست دوم» + ★★★★☆, new shows «نو»), the sells_used switch
(off hides every trace of the used flow), search filters
(condition alone, category + condition, text + condition), CMS legal
pages (active renders CMS text, inactive 404s, missing falls back to
the static template), the site fallback when Directus is down, and
the /site/logo + /site/badge proxies.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.cache import reset_cache_for_tests
from db.base import Base
from db.models.product_stock import ProductStock
from db.session import get_db
from main import app

# --- Canned CMS rows (what Directus would return) ---------------------

CMS_CATEGORIES = [
    {"id": 1, "name": "آشپزخانه", "description": "یخچال و اجاق", "icon": "🍳", "sort": 1, "is_active": True},
    {"id": 2, "name": "تلویزیون و صدا", "description": "تلویزیون", "icon": None, "sort": 2, "is_active": True},
    {"id": 3, "name": "شستشو", "description": "لباسشویی", "icon": None, "sort": 3, "is_active": True},
]

CMS_PRODUCTS = [
    {
        "id": "ad7d6a7f-3d92-412c-a523-55deb334fa8c",
        "name": "یخچال دو درب",
        "description": "کم‌مصرف",
        "price_toman": 45_000_000,
        "stock": 5,
        "reserved": 0,
        "is_active": True,
        "category": {"id": 1, "name": "آشپزخانه"},
        "tag": None,
        "image_path": "product-fridge.jpg",
        "condition": "new",
        "condition_stars": None,
    },
    {
        "id": "4a9be4d9-5194-4b0d-b401-9594ebb8d030",
        "name": "تلویزیون ۵۵ اینچ",
        "description": "هوشمند",
        "price_toman": 32_000_000,
        "stock": 5,
        "reserved": 0,
        "is_active": True,
        "category": {"id": 2, "name": "تلویزیون و صدا"},
        "tag": None,
        "image_path": "product-tv.jpg",
        "condition": "new",
        "condition_stars": None,
    },
    {
        "id": "4b31ea7d-c2df-4068-877a-983cfa2734ba",
        "name": "ماشین لباسشویی ۸ کیلویی",
        "description": "کم‌صدا",
        "price_toman": 28_500_000,
        "stock": 5,
        "reserved": 0,
        "is_active": True,
        "category": {"id": 3, "name": "شستشو"},
        "tag": None,
        "image_path": "product-washer.jpg",
        "condition": "used",
        "condition_stars": 4,
    },
]

CMS_SETTINGS = {
    "site_title": "فروشگاه تست",
    "site_tagline": "شعار تست",
    "hero_title": "تیتر تست",
    "hero_subtitle": "زیرتیتر تست",
    "logo": None,
    "sells_used": True,
}

CMS_TRUST_BADGES = [
    {"title": "ضمانت اصالت", "text": "با ضمانت‌نامه", "icon": "✦", "sort": 1, "is_active": True},
    {"title": "ارسال سریع", "text": "درب منزل", "icon": "🚚", "sort": 2, "is_active": True},
]

CMS_LEGAL_PAGES = [
    {"slug": "terms", "title": "قوانین تست", "body": "<p>متن قوانین از CMS</p>", "sort": 1, "is_active": True},
    {"slug": "privacy", "title": "حریم خصوصی تست", "body": "<p>متن حریم خصوصی از CMS</p>", "sort": 2, "is_active": True},
    {"slug": "returns", "title": "بازگشت تست", "body": "<p>متن بازگشت از CMS</p>", "sort": 3, "is_active": False},
]

CMS_CERT_BADGES = [
    {"name": "اینماد تست", "status_text": "فعال", "image": None, "link": None, "sort": 1, "is_active": True},
]

CMS_GUIDE_SECTIONS = [
    {
        "slug": "claude",
        "ai_name": "Claude",
        "title": "Claude — بهترین گزینه",
        "support_note": "پشتیبانی می‌کند — رایگان",
        "body": "<p>به <b>Customize</b> و بعد <b>Connectors</b> بروید.</p>",
        "supported": True,
        "sort": 1,
        "is_active": True,
    },
    {
        "slug": "gemini",
        "ai_name": "Gemini",
        "title": "Gemini — در اپ وب، با دکمه Custom وصل می‌شود",
        "support_note": "پشتیبانی می‌کند — در اپ وب با دکمه Custom",
        "body": "<p>به <b>Customize</b> بعد <b>Connectors</b> و دکمه <b>+ Custom</b> بروید و لینک را در <b>MCP Server URL</b> بچسبانید.</p>",
        "supported": True,
        "sort": 2,
        "is_active": True,
    },
]

LOGO_BYTES = b"\x89PNG\r\n\x1a\nfake-png-bytes"


def _filter_rows(rows: list[dict], query: dict[str, list[str]]) -> list[dict]:
    """Apply the Directus filters the client actually sends."""
    out = list(rows)
    if query.get("filter[is_active][_eq]") == ["true"]:
        out = [r for r in out if r.get("is_active") is True]
    cat = query.get("filter[category][_eq]")
    if cat:
        def _cat_id(row: dict) -> str | None:
            value = row.get("category")
            if isinstance(value, dict):
                return str(value.get("id"))
            return str(value) if value is not None else None

        out = [r for r in out if _cat_id(r) == cat[0]]
    ids = query.get("filter[id][_in]")
    if ids:
        wanted = set(ids[0].split(","))
        out = [r for r in out if str(r.get("id")) in wanted]
    slug = query.get("filter[slug][_eq]")
    if slug:
        out = [r for r in out if r.get("slug") == slug[0]]
    limit = query.get("limit")
    if limit:
        try:
            out = out[: max(1, int(limit[0]))]
        except ValueError:
            pass
    return out


def _mock_handler(settings: dict, logo_bytes: bytes | None):
    """An httpx handler answering like Directus for one scenario."""
    collections: dict[str, list[dict]] = {
        "categories": CMS_CATEGORIES,
        "products": CMS_PRODUCTS,
        "site_settings": [dict(settings)],
        "trust_badges": CMS_TRUST_BADGES,
        "legal_pages": CMS_LEGAL_PAGES,
        "certification_badges": CMS_CERT_BADGES,
        "mcp_guide_sections": CMS_GUIDE_SECTIONS,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        parsed = urlparse(str(request.url))
        parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query)
        if len(parts) == 2 and parts[0] == "items" and parts[1] in collections:
            rows = _filter_rows(collections[parts[1]], query)
            return httpx.Response(200, json={"data": rows})
        if (
            len(parts) == 3
            and parts[0] == "items"
            and parts[1] in collections
        ):
            for row in collections[parts[1]]:
                if str(row.get("id")) == parts[2]:
                    return httpx.Response(200, json={"data": row})
            return httpx.Response(404, json={"errors": [{"message": "not found"}]})
        if len(parts) == 2 and parts[0] == "assets" and logo_bytes is not None:
            return httpx.Response(
                200, content=logo_bytes, headers={"Content-Type": "image/png"}
            )
        return httpx.Response(404, json={"errors": [{"message": "not found"}]})

    return handler


def _make_client(
    settings: dict | None = None, logo_bytes: bytes | None = None
) -> TestClient:
    """A TestClient whose Directus calls hit the mock transport.

    The cache is reset first so a previous scenario's cached site or
    landing content cannot leak into this one.
    """
    reset_cache_for_tests()
    settings = dict(CMS_SETTINGS if settings is None else settings)
    transport = httpx.MockTransport(_mock_handler(settings, logo_bytes))
    app.state.http_client = httpx.AsyncClient(transport=transport)
    return TestClient(app)


def _make_dead_client() -> TestClient:
    """A TestClient whose Directus is down (every request fails)."""
    reset_cache_for_tests()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Directus is down", request=request)

    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TestClient(app)


async def main_async() -> None:
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

    # Stock seed: the same three products as the mocked CMS (by their
    # Directus ids). Content (names, prices, condition, stars) comes
    # from the mock; only the stock numbers live here.
    async with Session() as s:
        s.add_all(
            [
                ProductStock(
                    directus_product_id="ad7d6a7f-3d92-412c-a523-55deb334fa8c",
                    stock=5,
                ),
                ProductStock(
                    directus_product_id="4a9be4d9-5194-4b0d-b401-9594ebb8d030",
                    stock=5,
                ),
                ProductStock(
                    directus_product_id="4b31ea7d-c2df-4068-877a-983cfa2734ba",
                    stock=5,
                ),
            ]
        )
        await s.commit()

    # --- 1. The [] bug: an HTMX search must return HTML, never JSON ---
    client = _make_client()
    r = client.get("/search", headers={"HX-Request": "true", "Accept": "*/*"})
    assert r.status_code == 200, r.status_code
    assert "جستجوی کالا" in r.text, "HTMX /search did not render the search page"
    assert r.text.strip() != "[]", "HTMX /search still returns the empty array"
    print("1. HTMX /search returns HTML (the [] bug is gone)")

    # --- 2. Plain /search still returns JSON for API callers ---
    r = client.get("/search?q=تلویزیون")
    assert r.status_code == 200, r.status_code
    assert isinstance(r.json(), list), "plain /search must stay a JSON list"
    assert len(r.json()) == 1 and r.json()[0]["name"] == "تلویزیون ۵۵ اینچ"
    print("2. plain /search still returns JSON list")

    # --- 3. HTMX product page returns HTML, never raw JSON ---
    # The TV's id is the mocked CMS product's id.
    tv_id = "4a9be4d9-5194-4b0d-b401-9594ebb8d030"
    r = client.get(
        f"/products/{tv_id}", headers={"HX-Request": "true", "Accept": "*/*"}
    )
    assert r.status_code == 200, r.status_code
    assert "تلویزیون ۵۵ اینچ" in r.text, "HTMX /products/<id> did not render HTML"
    assert not r.text.strip().startswith("{"), "HTMX /products/<id> returned raw JSON"
    print("3. HTMX /products/<id> returns HTML (same bug family, fixed)")

    # --- 4. /categories renders every category with real counts ---
    r = client.get("/categories")
    assert r.status_code == 200, r.status_code
    for name in ("آشپزخانه", "تلویزیون و صدا", "شستشو"):
        assert name in r.text, f"/categories is missing {name}"
    assert "۱ کالا" in r.text, "/categories must show the real count (1 per category)"
    assert "🍳" in r.text, "/categories must show the CMS icon"
    print("4. /categories renders all categories with real counts + CMS icons")

    # --- 5. Landing: used shows «دست دوم» + 4 stars, new shows «نو» ---
    r = client.get("/")
    assert r.status_code == 200, r.status_code
    assert "دست دوم" in r.text, "landing must label the used washer"
    assert "★★★★☆" in r.text, "landing must show 4 of 5 stars for the washer"
    assert "نو" in r.text, "landing must label new products as نو"
    assert "تیتر تست" in r.text, "landing hero must come from the CMS settings"
    assert "ضمانت اصالت" in r.text, "landing trust badges must come from the CMS"
    assert "فروشگاه تست" in r.text, "site title must come from the CMS"
    print("5. landing shows «دست دوم» + ★★★★☆ for used, «نو» for new, CMS title/hero/badges")

    # --- 6. Search filters: condition, category + condition, text + condition ---
    r = client.get("/search?condition=used")
    assert r.status_code == 200, r.status_code
    data = r.json()
    assert len(data) == 1 and data[0]["condition"] == "used", data
    assert data[0]["condition_stars"] == 4, "used product must carry its stars"
    r = client.get("/search?condition=new")
    assert len(r.json()) == 2, "condition=new must return only the two new products"
    r = client.get("/search?q=ماشین&condition=used")
    assert len(r.json()) == 1, "text + condition must combine"
    r = client.get("/search?q=ماشین&condition=new")
    assert len(r.json()) == 0, "text + wrong condition must return nothing"
    r = client.get("/search?q=ماشین&condition=bogus")
    assert len(r.json()) == 1, "an invalid condition means 'no filter', never an error"
    print("6. /search filters by condition (alone, with text, invalid = all)")

    # HTML filter page: condition chips + active filter shown.
    r = client.get(
        "/search?condition=used", headers={"HX-Request": "true", "Accept": "*/*"}
    )
    assert r.status_code == 200 and "دست دوم" in r.text
    assert "ماشین لباسشویی" in r.text and "تلویزیون ۵۵ اینچ" not in r.text
    print("6b. HTML /search?condition=used shows only the used product")

    # --- 7. CMS legal pages: active renders, inactive 404s, missing falls back ---
    r = client.get("/terms")
    assert r.status_code == 200, r.status_code
    assert "متن قوانین از CMS" in r.text, "/terms must render the CMS body"
    r = client.get("/returns")
    assert r.status_code == 404, "an inactive legal page must 404 (editor removed it)"
    r = client.get("/contact")
    assert r.status_code == 200, r.status_code
    assert "تماس با ما" in r.text, "a missing CMS page must fall back to the static template"
    print("7. legal pages: active CMS text, inactive 404, missing falls back")

    # --- 8. sells_used OFF: no trace of the used flow anywhere ---
    off_settings = dict(CMS_SETTINGS, sells_used=False)
    client_off = _make_client(settings=off_settings)
    r = client_off.get("/")
    assert "دست دوم" not in r.text, "sells_used off must hide the used label"
    assert "★" not in r.text and "☆" not in r.text, "sells_used off must hide stars"
    r = client_off.get("/search", headers={"HX-Request": "true", "Accept": "*/*"})
    assert "دست دوم" not in r.text, "sells_used off must hide the condition filter"
    print("8. sells_used off hides labels, stars and the condition filter")

    # --- 9. Directus down: the site still renders from the fallback ---
    dead = _make_dead_client()
    r = dead.get("/")
    assert r.status_code == 200, r.status_code
    assert "گنابِی" in r.text, "fallback must render the hardcoded site title"
    assert "ضمانت اصالت" in r.text, "fallback must render the hardcoded trust badges"
    r = dead.get("/terms")
    assert r.status_code == 200, r.status_code
    assert "قوانین و مقررات" in r.text, "CMS down must fall back to the static terms page"
    r = dead.get("/categories")
    assert r.status_code == 200, r.status_code
    print("9. Directus down: landing + terms + categories still render from the fallback")

    # --- 10. /site/logo + /site/badge proxy the CMS files ---
    client_logo = _make_client(
        settings=dict(CMS_SETTINGS, logo="071d7087-9407-4880-bc1a-c1bcc3e56a37"),
        logo_bytes=LOGO_BYTES,
    )
    r = client_logo.get("/site/logo")
    assert r.status_code == 200, r.status_code
    assert r.content == LOGO_BYTES, "/site/logo must serve the CMS file bytes"
    assert r.headers["content-type"].startswith("image/png")
    r = client_logo.get("/site/badge/071d7087-9407-4880-bc1a-c1bcc3e56a37")
    assert r.status_code == 200, r.status_code
    r = client_logo.get("/site/badge/../../etc/passwd")
    assert r.status_code in (404, 422), "a path-traversal file id must never be served"
    client_no_logo = _make_client()
    r = client_no_logo.get("/site/logo")
    assert r.status_code == 404, "no logo configured must 404 (header falls back to ■)"
    print("10. /site/logo + /site/badge proxy CMS files; traversal ids refused")

    # --- 12. Pre-search filters: chips in the header panel + live search ---
    # The landing page header panel must offer the filters BEFORE the
    # visitor types anything: a category chip per CMS category, the
    # condition chips (sells_used is on here), hidden inputs and a form
    # that submits to /search.
    r = client.get("/")
    assert r.status_code == 200, r.status_code
    assert 'id="pre-search-form"' in r.text, "header panel must be a form to /search"
    assert 'action="/search"' in r.text
    assert 'id="pre-search-category-inputs"' in r.text, "header panel must hold one hidden input per picked category"
    assert 'id="pre-search-condition"' in r.text
    for cat in ("آشپزخانه", "تلویزیون و صدا", "شستشو"):
        assert f'data-filter-category="{cat}"' in r.text, f"missing pre-search chip {cat}"
    assert 'data-filter-condition="used"' in r.text, "missing pre-search condition chip"
    assert 'data-filter-condition="new"' in r.text
    # Live search respects the condition filter: used washer only.
    r = client.get("/api/search-live?q=لباسشویی&condition=used")
    assert r.status_code == 200, r.status_code
    assert "ماشین لباسشویی" in r.text, "live search + used must find the washer"
    assert "condition=used" in r.text, "see-all link must carry the condition"
    # ... and the same text with condition=new must NOT find the washer.
    r = client.get("/api/search-live?q=تلویزیون&condition=used")
    assert r.status_code == 200, r.status_code
    assert "تلویزیون ۵۵ اینچ" not in r.text, "live search + used must hide the new TV"
    # Category filter: the fridge query inside the TV category finds nothing.
    r = client.get("/api/search-live?q=یخچال&category=تلویزیون و صدا")
    assert r.status_code == 200, r.status_code
    assert "یخچال دو درب" not in r.text, "live search + wrong category must hide the fridge"
    assert "یافت نشد" in r.text, "live search + wrong category must say nothing found"
    # ... and inside its own category it finds it, with the category in the link.
    r = client.get("/api/search-live?q=یخچال&category=آشپزخانه")
    assert "یخچال دو درب" in r.text, "live search + right category must find the fridge"
    assert "category=" in r.text, "see-all link must carry the category"
    print("12. pre-search filters: header chips + live search respects them")

    # --- 13. MCP + Instagram promo boxes + /mcp-guide ---
    # Landing: MCP box first (above the hero), Instagram second; both
    # from site_settings (the mock has no MCP fields, so the fallback
    # test values show). Copy button carries the MCP link; ChatGPT and
    # Gemini open in a new tab (the owner's pick); the guide link goes
    # to /mcp-guide. ChatGPT opens straight at chatgpt.com/plugins.
    r = client.get("/")
    assert r.status_code == 200, r.status_code
    mcp_pos = r.text.index("هوش مصنوعی‌ات را به فروشگاهمون وصل کن")
    insta_pos = r.text.index("ما را در اینستاگرام دنبال کنید")
    hero_pos = r.text.index("خانه‌ی مدرن، زندگی بهتر")
    assert mcp_pos < insta_pos < hero_pos, "MCP box first, Instagram second, both above the hero"
    assert 'data-copy-text="https://genabay.ir/mcp"' in r.text
    assert 'href="https://chatgpt.com/plugins"' in r.text and 'target="_blank"' in r.text
    assert 'href="https://gemini.google.com/customize/connectors"' in r.text
    assert 'href="https://claude.ai"' not in r.text, "the box buttons are ChatGPT + Gemini, not Claude"
    assert 'href="/mcp-guide"' in r.text
    assert 'href="https://instagram.com/genabay"' in r.text
    # Guide page: intro + checklist marks + TOC + sections + back-to-top.
    r = client.get("/mcp-guide")
    assert r.status_code == 200, r.status_code
    assert "هوش مصنوعی‌ات را به فروشگاه وصل کن" in r.text
    # All five AIs are supported now (Gemini was fixed), so the checklist
    # shows only ✓; the ✗ branch stays in the template for a future
    # unsupported AI.
    assert "✓" in r.text, "checklist must show supported marks"
    assert 'href="#claude"' in r.text and 'href="#gemini"' in r.text
    assert 'id="claude"' in r.text and "Customize" in r.text
    assert 'id="gemini"' in r.text
    assert "بازگشت به بالا" in r.text
    # Directus down: the guide still renders from the fallback sections.
    # (Rebuild the dead client first: app.state.http_client is global,
    # and the mock client above overwrote the failing transport.)
    dead = _make_dead_client()
    r = dead.get("/mcp-guide")
    assert r.status_code == 200, r.status_code
    assert "Claude — ساده‌ترین راه" in r.text
    assert "Gemini — در اپ وب گوگل" in r.text
    assert "chatgpt.com" in r.text
    print("13. MCP + Instagram boxes above hero; /mcp-guide with checklist, TOC, back-to-top")

    print("ALL SITE-CONTENT CHECKS PASSED")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
