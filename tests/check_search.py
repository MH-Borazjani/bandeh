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
"""Runnable self-check for product search. Run from repo root:

    python tests/check_search.py

In-memory SQLite only — no server, no live Postgres. Search reads
Directus (the catalogue), so the CMS here is a fake client and the
database holds only the stock rows. Covers: substring search (name +
description), inactive products excluded, wildcards are literal
(``%`` matches only the product that has one), empty query, limit +
the 50 cap, an SQL-injection-shaped query that must not break
anything, the product list + its public Cache-Control header, the
stock numbers coming from the stock table (not the CMS rows), and
multi-select categories (Phases 1–2): repeated ``category`` params on
/search and /api/search-live, toggle chip links + hidden inputs, the
home search box (button above the chips, first-3 + "more" toggle,
advanced-search link).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.base import Base
from db.models.product_stock import ProductStock
from db.session import get_db
from integrations.directus_client import get_directus_client
from main import app
from services.search_service import MAX_LIMIT, search_products

FRIDGE_ID = "11111111-1111-4111-8111-111111111111"
FRIDGE2_ID = "12121212-1212-4121-8121-121212121212"
TV_ID = "22222222-2222-4222-8222-222222222222"
WASHER_ID = "33333333-3333-4333-8333-333333333333"
DEAL_ID = "44444444-4444-4444-8444-444444444444"
OLD_FRIDGE_ID = "55555555-5555-4555-8555-555555555555"


def _product(
    pid: str,
    name: str,
    description: str,
    price_toman: int,
    category: str,
    stock: int = 5,
    is_active: bool = True,
    condition: str = "new",
) -> dict:
    """One fake CMS product row (ProductRead shape)."""
    return {
        "id": pid,
        "name": name,
        "description": description,
        "price_toman": price_toman,
        "stock": stock,
        "reserved": 0,
        "category": category,
        "tag": None,
        "image_path": None,
        "is_active": is_active,
        "created_at": "2026-11-24T00:00:00+00:00",
        "condition": condition,
        "condition_stars": None,
        "specs": {},
    }


class _FakeDirectusClient:
    """CMS stand-in: three categories, five active products + one inactive.

    list_products / list_products_by_category return active products
    only, like the real client's ``filter[is_active][_eq]=true``.
    get_product returns any row (the real client does not filter
    single reads either). Rows are copied on every call so the stock
    numbers the routes attach never leak back into the fixture.
    """

    CATEGORIES = [
        {"id": 1, "name": "یخچال و فریزر", "description": "یخچال", "icon": None, "sort": 1},
        {"id": 2, "name": "تلویزیون و صدا", "description": "تلویزیون", "icon": None, "sort": 2},
        {"id": 3, "name": "شستشو", "description": "لباسشویی", "icon": None, "sort": 3},
    ]
    PRODUCTS = [
        # The fridge's CMS stock is deliberately wrong (99): the real
        # number comes from the stock table (5, seeded below), so the
        # list test proves which source the shop trusts.
        _product(FRIDGE_ID, "یخچال سامسونگ", "کم‌مصرف، 400 لیتر", 45_000_000,
                 "یخچال و فریزر", stock=99),
        _product(FRIDGE2_ID, "یخچال فریزر سامسونگ", "فریزر بالا", 38_000_000,
                 "یخچال و فریزر"),
        _product(TV_ID, "تلویزیون سامسونگ", "کم‌مصرف 55 اینچ", 32_000_000,
                 "تلویزیون و صدا"),
        _product(WASHER_ID, "ماشین لباسشویی سامسونگ", "۸ کیلوگرم", 28_500_000,
                 "شستشو"),
        _product(DEAL_ID, "تخفیف 100% واقعی", "فروش ویژه", 1_000_000, "شستشو"),
        _product(OLD_FRIDGE_ID, "یخچال قدیمی", "از رده خارج", 5_000_000,
                 "یخچال و فریزر", stock=0, is_active=False),
    ]

    def _active(self) -> list[dict]:
        return [dict(p) for p in self.PRODUCTS if p.get("is_active", True)]

    async def get_product(self, product_id) -> dict | None:
        for p in self.PRODUCTS:
            if p["id"] == str(product_id):
                return dict(p)
        return None

    async def get_products_by_ids(self, product_ids: list) -> list[dict]:
        wanted = {str(pid) for pid in product_ids}
        return [dict(p) for p in self.PRODUCTS if p["id"] in wanted]

    async def list_categories(self) -> list[dict]:
        return list(self.CATEGORIES)

    async def list_products(self, limit: int = 20) -> list[dict]:
        return self._active()[:limit]

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        name = str(category_identifier).strip()
        for c in self.CATEGORIES:
            if str(c["id"]) == name:
                name = str(c["name"])
        return [p for p in self._active() if p.get("category") == name][:limit]

    async def get_landing_content(self) -> dict:
        # The header chips come from the site-content middleware
        # (fallback: 6 categories), not from here — this only feeds
        # the landing grid.
        return {"categories": list(self.CATEGORIES), "products": self._active()}


class _ManyProductsFake:
    """Sixty products named کالا, for the 50 cap (service-level check)."""

    async def list_products(self, limit: int = 20) -> list[dict]:
        return [
            {"id": f"60000000-0000-4000-8000-{i:012d}",
             "name": f"کالا {i}", "description": "", "price_toman": 1000,
             "condition": "new"}
            for i in range(60)
        ][:limit]


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
    # Search reads Directus from the first check on, so the fake is
    # installed before any request.
    app.dependency_overrides[get_directus_client] = lambda: _FakeDirectusClient()
    with TestClient(app) as client:

        # Stock rows for the fake's active products (the fridge's real
        # stock is 5 — the fake's 99 must never show up).
        async with Session() as s:
            s.add_all(
                [
                    ProductStock(directus_product_id=FRIDGE_ID, stock=5),
                    ProductStock(directus_product_id=FRIDGE2_ID, stock=3),
                    ProductStock(directus_product_id=TV_ID, stock=2),
                    ProductStock(directus_product_id=WASHER_ID, stock=4),
                    ProductStock(directus_product_id=DEAL_ID, stock=1),
                ]
            )
            await s.commit()

        def names(resp) -> list[str]:
            return [p["name"] for p in resp.json()]

        # 1. Substring search: name match, inactive excluded --------------------
        resp = client.get("/search", params={"q": "یخچال"})
        assert resp.status_code == 200, resp.status_code
        assert set(names(resp)) == {"یخچال فریزر سامسونگ", "یخچال سامسونگ"}, names(resp)
        assert "یخچال قدیمی" not in names(resp), "inactive products must not appear"
        print("   [OK] search by name, inactive excluded")

        # 2. Description-only match ---------------------------------------------
        resp = client.get("/search", params={"q": "کم‌مصرف"})
        assert set(names(resp)) == {"یخچال سامسونگ", "تلویزیون سامسونگ"}, names(resp)
        print("   [OK] search matches description too")

        # 3. Wildcards are literal: % matches only the product that has % ------
        resp = client.get("/search", params={"q": "%"})
        assert names(resp) == ["تخفیف 100% واقعی"], names(resp)
        resp = client.get("/search", params={"q": "_"})
        assert names(resp) == [], names(resp)
        print("   [OK] % and _ in the query match literally")

        # 4. Empty query returns [] (never a full-table dump) --------------------
        assert client.get("/search", params={"q": ""}).json() == []
        assert client.get("/search", params={"q": "   "}).json() == []
        assert client.get("/search").json() == []
        print("   [OK] empty query returns []")

        # 5. Limit respected, and capped at MAX_LIMIT -----------------------------
        resp = client.get("/search", params={"q": "ی", "limit": 2})
        assert len(resp.json()) == 2, resp.json()
        found = await search_products(_ManyProductsFake(), "کالا", limit=500)
        assert len(found) == MAX_LIMIT, f"limit must cap at {MAX_LIMIT}, got {len(found)}"
        print(f"   [OK] limit respected, capped at {MAX_LIMIT}")

        # 6. SQL injection attempt: 200, no crash, catalogue intact --------------
        resp = client.get("/search", params={"q": "'; DROP TABLE products; --"})
        assert resp.status_code == 200, resp.status_code
        assert resp.json() == []
        resp = client.get("/products")
        assert resp.status_code == 200 and len(resp.json()) > 0, "catalogue must survive"
        print("   [OK] injection attempt harmless, catalogue intact")

        # 7. Product list: active only, stock from the stock table, cache header --
        resp = client.get("/products")
        assert resp.status_code == 200
        assert "یخچال قدیمی" not in names(resp)
        fridge = next(p for p in resp.json() if p["name"] == "یخچال سامسونگ")
        assert fridge["stock"] == 5, fridge
        assert resp.headers["cache-control"] == "public, max-age=60", resp.headers.get("cache-control")
        print("   [OK] product list: active only, stock from the stock table, Cache-Control public, max-age=60")

        # 8. Multi-select categories (Phases 1-2) ---------------------------
        html_headers = {"accept": "text/html"}
        cat_a, cat_b, cat_c = "یخچال و فریزر", "تلویزیون و صدا", "شستشو"

        # 8a. Two categories, filter-only: both products, none of third.
        resp = client.get(
            "/search",
            params=[("category", cat_a), ("category", cat_b)],
            headers=html_headers,
        )
        assert resp.status_code == 200, resp.status_code
        assert "text/html" in resp.headers["content-type"]
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html, html
        assert "ماشین لباسشویی سامسونگ" not in html, "third category must not leak in"
        assert 'data-single-category="false"' in resp.text, "two picked means not single"
        # One badge per picked category, one hidden input per picked one
        # in EACH form (the search form and the Phase 4 filter panel
        # form both carry the picked categories) — so 2 x 2 here.
        assert resp.text.count('name="category"') == 4, resp.text
        assert (
            '<span class="text-xs theme-badge rounded-lg px-2 py-0.5">یخچال و فریزر</span>'
            in resp.text
        )
        assert (
            '<span class="text-xs theme-badge rounded-lg px-2 py-0.5">تلویزیون و صدا</span>'
            in resp.text
        )
        # The third chip toggles ADD: its link carries all three.
        assert (
            f"category={cat_a}&category={cat_b}&category={cat_c}" in html
        ), "unpicked chip link must add its category to the picked set"
        # "همه دسته‌ها" clears all: a bare /search link.
        assert 'href="/search"' in resp.text, "clear-all chip must link to bare /search"
        print("   [OK] /search with two categories returns both, none of the third")

        # 8b. Text + two categories: the query runs, the set still filters.
        resp = client.get(
            "/search",
            params=[("q", "سامسونگ"), ("category", cat_a), ("category", cat_b)],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html
        assert "ماشین لباسشویی سامسونگ" not in html
        print("   [OK] text query + two categories still respects the set")

        # 8c. Single category still works, and flags single_category.
        resp = client.get(
            "/search", params=[("category", cat_a)], headers=html_headers
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html
        assert "تلویزیون سامسونگ" not in html and "ماشین لباسشویی سامسونگ" not in html
        assert 'data-single-category="true"' in resp.text
        # One hidden input per picked category in each of the two forms.
        assert resp.text.count('name="category"') == 2
        print("   [OK] single category still works, single_category=true")

        # 8d. Category ids resolve to names, like the old single links.
        resp = client.get(
            "/search",
            params=[("category", "1"), ("category", "2")],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html
        assert "ماشین لباسشویی سامسونگ" not in html
        print("   [OK] category ids resolve to names in a multi-select")

        # 8e. Unknown category matches nothing.
        resp = client.get(
            "/search",
            params=[("category", "دسته‌ی ناموجود")],
            headers=html_headers,
        )
        assert "کالایی یافت نشد" in resp.text, "unknown category must show the empty state"
        assert "یخچال سامسونگ" not in resp.text
        print("   [OK] unknown category matches nothing")

        # 8f. Live search with two categories respects both; the
        # "see all results" link carries both as repeated params.
        resp = client.get(
            "/api/search-live",
            params=[("q", "سامسونگ"), ("category", cat_a), ("category", cat_b)],
        )
        assert resp.status_code == 200, resp.status_code
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html
        assert "ماشین لباسشویی سامسونگ" not in html
        assert f"category={cat_a}&category={cat_b}" in html, "see-all link must carry both"
        print("   [OK] live search with two categories respects both + see-all link")

        # 8g. Live search, single category: only that category's match.
        resp = client.get(
            "/api/search-live",
            params=[("q", "سامسونگ"), ("category", cat_c)],
        )
        assert "ماشین لباسشویی سامسونگ" in resp.text
        assert "یخچال سامسونگ" not in resp.text
        print("   [OK] live search with one category still works")

        # 9. Home search box (Phase 1): the جستجو button sits above
        # the chips, the advanced-search link lands on /search, and
        # only the first 3 categories show until "بیشتر" is pressed
        # (the fallback site content has 6 categories).
        resp = client.get("/", headers=html_headers)
        assert resp.status_code == 200, resp.status_code
        assert 'id="advanced-search-link"' in resp.text
        assert 'href="/search"' in resp.text
        submit_pos = resp.text.index('type="submit"')
        filters_pos = resp.text.index('id="pre-search-filters"')
        assert submit_pos < filters_pos, "submit button must sit above the chips"
        # The "more" toggle only exists when there are more than 3
        # categories to hide: the fallback site content has 6, but a
        # live CMS with 3 categories (the test catalog) has none.
        # (One chip carries an empty data-filter-category — the "all"
        # chip — so only non-empty values count.)
        chip_count = resp.text.count('data-filter-category="') - resp.text.count(
            'data-filter-category=""'
        )
        if chip_count > 3:
            assert 'id="toggle-more-pre-search-categories"' in resp.text
            assert "بیشتر" in resp.text
            assert "pre-search-category-extra hidden" in resp.text, "categories past the first 3 must start hidden"
            if "اجاق گاز و فر" in resp.text:
                assert 'data-filter-category="اجاق گاز و فر"' in resp.text
        else:
            assert 'id="toggle-more-pre-search-categories"' not in resp.text, (
                "with 3 or fewer categories there is nothing to hide"
            )
        # The advanced-search link carries state: its static href is
        # plain /search (the no-JS fallback), but shop.js intercepts
        # the click and navigates with the typed query, the picked
        # categories (repeated params) and the condition.
        shop_js = (
            Path(__file__).resolve().parent.parent
            / "app" / "e-commerce" / "static" / "js" / "shop.js"
        ).read_text(encoding="utf-8")
        assert 'getElementById("advanced-search-link")' in shop_js
        assert 'params.set("q"' in shop_js, "advanced link must carry the query"
        assert 'params.append("category"' in shop_js, (
            "advanced link must carry each picked category"
        )
        assert 'params.set("condition"' in shop_js, (
            "advanced link must carry the condition"
        )
        print("   [OK] home search box: button above chips, 3 + more toggle, advanced link")
        print("   [OK] advanced-search link carries query + categories + condition")

        print("All search checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
