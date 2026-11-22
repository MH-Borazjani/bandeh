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

In-memory SQLite only — no server, no live Postgres. Covers: ILIKE
substring search (name + description), inactive products excluded,
wildcards escaped (``%`` matches literally), empty query, limit + the
50 cap, an SQL-injection attempt that must not break anything, the
product list + its public Cache-Control header, the Postgres
full-text SQL compiled (not run) against the Postgres dialect, and
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
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.base import Base
from db.models.product import Product
from db.session import get_db
from integrations.directus_client import get_directus_client
from main import app
from services.search_service import (
    MAX_LIMIT,
    build_fulltext_statement,
    search_products_ilike,
)


class _FakeDirectusClient:
    """CMS stand-in: three categories, one Samsung product each."""

    CATEGORIES = [
        {"id": 1, "name": "یخچال و فریزر", "description": "یخچال", "icon": None, "sort": 1},
        {"id": 2, "name": "تلویزیون و صدا", "description": "تلویزیون", "icon": None, "sort": 2},
        {"id": 3, "name": "شستشو", "description": "لباسشویی", "icon": None, "sort": 3},
    ]
    PRODUCTS = [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "یخچال سامسونگ",
            "description": "دو درب",
            "price_toman": 45_000_000,
            "stock": 5,
            "reserved": 0,
            "category": "یخچال و فریزر",
            "tag": None,
            "image_path": None,
            "condition": "new",
            "condition_stars": None,
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "name": "تلویزیون سامسونگ",
            "description": "۵۵ اینچ هوشمند",
            "price_toman": 32_000_000,
            "stock": 5,
            "reserved": 0,
            "category": "تلویزیون و صدا",
            "tag": None,
            "image_path": None,
            "condition": "new",
            "condition_stars": None,
        },
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "name": "ماشین لباسشویی سامسونگ",
            "description": "۸ کیلوگرم",
            "price_toman": 28_500_000,
            "stock": 5,
            "reserved": 0,
            "category": "شستشو",
            "tag": None,
            "image_path": None,
            "condition": "new",
            "condition_stars": None,
        },
    ]

    async def list_categories(self) -> list[dict]:
        return list(self.CATEGORIES)

    async def list_products(self, limit: int = 20) -> list[dict]:
        return list(self.PRODUCTS)[:limit]

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        name = str(category_identifier).strip()
        for c in self.CATEGORIES:
            if str(c["id"]) == name:
                name = str(c["name"])
        return [p for p in self.PRODUCTS if p.get("category") == name][:limit]

    async def get_landing_content(self) -> dict:
        # The header chips come from the site-content middleware
        # (fallback: 6 categories), not from here — this only feeds
        # the landing grid.
        return {"categories": list(self.CATEGORIES), "products": list(self.PRODUCTS)}


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
    with TestClient(app) as client:

        async with Session() as s:
            s.add_all(
                [
                    Product(name="یخچال دو درب", description="کم‌مصرف، 400 لیتر", price_toman=45_000_000, stock=5),
                    Product(name="یخچال فریزر", description="فریزر بالا", price_toman=38_000_000, stock=3),
                    Product(name="ماشین لباسشویی", description="8 کیلوگرم", price_toman=22_000_000, stock=4),
                    Product(name="تلویزیون", description="کم‌مصرف 55 اینچ", price_toman=30_000_000, stock=2),
                    Product(name="تخفیف 100% واقعی", description="فروش ویژه", price_toman=1_000_000, stock=1),
                    Product(name="یخچال قدیمی", description="از رده خارج", price_toman=5_000_000, stock=0, is_active=False),
                ]
            )
            await s.commit()

        def names(resp) -> list[str]:
            return [p["name"] for p in resp.json()]

        # 1. Substring search: name match, inactive excluded --------------------
        resp = client.get("/search", params={"q": "یخچال"})
        assert resp.status_code == 200, resp.status_code
        # Same-second created_at for both rows, so order between them is free.
        assert set(names(resp)) == {"یخچال فریزر", "یخچال دو درب"}, names(resp)
        assert "یخچال قدیمی" not in names(resp), "inactive products must not appear"
        print("   [OK] search by name, inactive excluded")

        # 2. Description-only match ---------------------------------------------
        resp = client.get("/search", params={"q": "کم‌مصرف"})
        assert set(names(resp)) == {"یخچال دو درب", "تلویزیون"}, names(resp)
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
        async with Session() as s:
            for i in range(55):
                s.add(Product(name=f"کالا {i}", price_toman=1000, stock=1))
            await s.commit()
        async with Session() as s:
            found = await search_products_ilike(s, "کالا", limit=500)
        assert len(found) == MAX_LIMIT, f"limit must cap at {MAX_LIMIT}, got {len(found)}"
        print(f"   [OK] limit respected, capped at {MAX_LIMIT}")

        # 6. SQL injection attempt: 200, no crash, table intact ------------------
        resp = client.get("/search", params={"q": "'; DROP TABLE products; --"})
        assert resp.status_code == 200, resp.status_code
        assert resp.json() == []
        resp = client.get("/products")
        assert resp.status_code == 200 and len(resp.json()) > 0, "products table must survive"
        print("   [OK] injection attempt harmless, table intact")

        # 7. Product list: active only, public cache header -----------------------
        resp = client.get("/products")
        assert resp.status_code == 200
        assert "یخچال قدیمی" not in names(resp)
        assert resp.headers["cache-control"] == "public, max-age=60", resp.headers.get("cache-control")
        print("   [OK] product list: active only, Cache-Control public, max-age=60")

        # 8. Full-text SQL: compiled for Postgres, never run here -----------------
        stmt = build_fulltext_statement("یخچال دو درب", limit=20)
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
        assert "to_tsvector" in sql, sql
        assert "plainto_tsquery" in sql, sql
        assert "ts_rank" in sql, sql
        assert "LIMIT" in sql, sql
        assert "یخچال" not in sql, "the query text must stay a bound parameter, not inline SQL"
        assert "%(param_1)s" in sql or ":param_1" in sql, sql
        print("   [OK] full-text SQL compiles for Postgres with bound parameters")

        # 9. Multi-select categories (Phases 1-2) ---------------------------
        # From here the CMS is the fake above: three categories, one
        # Samsung product each. Category filtering is Directus-side
        # (the Product model maps no category column).
        app.dependency_overrides[get_directus_client] = lambda: _FakeDirectusClient()
        html_headers = {"accept": "text/html"}
        cat_a, cat_b, cat_c = "یخچال و فریزر", "تلویزیون و صدا", "شستشو"

        # 9a. Two categories, filter-only: both products, none of third.
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

        # 9b. Text + two categories: the query runs, the set still filters.
        resp = client.get(
            "/search",
            params=[("q", "سامسونگ"), ("category", cat_a), ("category", cat_b)],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html
        assert "ماشین لباسشویی سامسونگ" not in html
        print("   [OK] text query + two categories still respects the set")

        # 9c. Single category still works, and flags single_category.
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

        # 9d. Category ids resolve to names, like the old single links.
        resp = client.get(
            "/search",
            params=[("category", "1"), ("category", "2")],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "یخچال سامسونگ" in html and "تلویزیون سامسونگ" in html
        assert "ماشین لباسشویی سامسونگ" not in html
        print("   [OK] category ids resolve to names in a multi-select")

        # 9e. Unknown category matches nothing.
        resp = client.get(
            "/search",
            params=[("category", "دسته‌ی ناموجود")],
            headers=html_headers,
        )
        assert "کالایی یافت نشد" in resp.text, "unknown category must show the empty state"
        assert "یخچال سامسونگ" not in resp.text
        print("   [OK] unknown category matches nothing")

        # 9f. Live search with two categories respects both; the
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

        # 9g. Live search, single category: only that category's match.
        resp = client.get(
            "/api/search-live",
            params=[("q", "سامسونگ"), ("category", cat_c)],
        )
        assert "ماشین لباسشویی سامسونگ" in resp.text
        assert "یخچال سامسونگ" not in resp.text
        print("   [OK] live search with one category still works")

        # 10. Home search box (Phase 1): the جستجو button sits above
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
