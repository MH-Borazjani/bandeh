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
"""Runnable self-check for the filter panel (Phase 4). Run from repo root:

    python tests/check_filters.py

In-memory SQLite only — no server, no live Postgres. Covers the
category-page filter panel and the same panel on /search:

1. The category page shows the panel with that category's attributes
   only (TV: screen size; washer: capacity; fridge: 450 L), one
   checkbox per distinct value actually present among the category's
   products.
2. Spec, price-range, in-stock and condition filters narrow the
   category list, and the result count reflects the filtered list.
3. The clear-all link goes to the bare category URL and returns the
   unfiltered list (on /search it keeps only the text query); a spec
   value with no match shows the empty state.
4. HTMX behaviour: the panel form submits into <main> with hx-push-url
   (the URL reflects the filters), and the panel re-renders its state
   from the URL params (the picked checkbox stays checked).
5. /search with exactly one category shows the attribute panel and
   filters by it; with two categories, or none, only the shared
   filters (price, condition, in-stock) show — no attribute inputs.
6. The JSON /search branch applies the same filters. Spec filtering
   is Python-side now (Directus owns search); the matcher is unit
   checked in section 0.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import unquote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.routes.products import (
    _apply_product_filters,
    _distinct_spec_values,
    _matches_product_filters,
    _parse_filter_params,
    _parse_price_param,
)
from db.base import Base
from db.models.product_stock import ProductStock
from db.session import get_db
from integrations.directus_client import get_directus_client
from main import app


class _FakeDirectusClient:
    """CMS stand-in: three categories, five products with specs."""

    CATEGORIES = [
        {
            "id": 1,
            "name": "آشپزخانه",
            "description": "یخچال",
            "icon": None,
            "sort": 1,
            "filter_attributes": [
                {"key": "capacity_liter", "label": "ظرفیت", "unit": "لیتر",
                 "type": "number"}
            ],
        },
        {
            "id": 2,
            "name": "تلویزیون و صدا",
            "description": "تلویزیون",
            "icon": None,
            "sort": 2,
            "filter_attributes": [
                {"key": "screen_size_inch", "label": "اندازه صفحه",
                 "unit": "اینچ", "type": "number"}
            ],
        },
        {
            "id": 3,
            "name": "شستشو",
            "description": "لباسشویی",
            "icon": None,
            "sort": 3,
            "filter_attributes": [
                {"key": "capacity_kg", "label": "ظرفیت", "unit": "کیلوگرم",
                 "type": "number"}
            ],
        },
    ]
    PRODUCTS = [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "name": "تلویزیون ۵۵ اینچ",
            "description": "هوشمند",
            "price_toman": 32_000_000,
            "stock": 5,
            "reserved": 0,
            "category": "تلویزیون و صدا",
            "tag": None,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "condition": "new",
            "condition_stars": None,
            "specs": {"screen_size_inch": 55},
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "name": "تلویزیون ۶۵ اینچ",
            "description": "هوشمند بزرگ",
            "price_toman": 45_000_000,
            "stock": 0,
            "reserved": 0,
            "category": "تلویزیون و صدا",
            "tag": None,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "condition": "new",
            "condition_stars": None,
            "specs": {"screen_size_inch": 65},
        },
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "name": "ماشین لباسشویی ۸ کیلویی",
            "description": "کم‌مصرف",
            "price_toman": 28_500_000,
            "stock": 3,
            "reserved": 0,
            "category": "شستشو",
            "tag": None,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "condition": "used",
            "condition_stars": 4,
            "specs": {"capacity_kg": 8},
        },
        {
            "id": "44444444-4444-4444-8444-444444444444",
            "name": "ماشین لباسشویی ۱۰ کیلویی",
            "description": "کم‌مصرف بزرگ",
            "price_toman": 35_000_000,
            "stock": 2,
            "reserved": 0,
            "category": "شستشو",
            "tag": None,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "condition": "new",
            "condition_stars": None,
            "specs": {"capacity_kg": 10},
        },
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "name": "یخچال دو درب",
            "description": "جایخچالی",
            "price_toman": 50_000_000,
            "stock": 4,
            "reserved": 0,
            "category": "آشپزخانه",
            "tag": None,
            "image_path": None,
            "is_active": True,
            "created_at": "2026-11-24T00:00:00+00:00",
            "condition": "new",
            "condition_stars": None,
            "specs": {"capacity_liter": 450},
        },
    ]

    async def get_category_map(self) -> dict:
        return {c["id"]: c["name"] for c in self.CATEGORIES}

    async def list_categories(self) -> list[dict]:
        return list(self.CATEGORIES)

    async def get_category_filter_attributes(self) -> dict:
        out: dict = {}
        for c in self.CATEGORIES:
            out[c["id"]] = list(c["filter_attributes"])
            out[str(c["id"])] = list(c["filter_attributes"])
        return out

    async def list_products(self, limit: int = 20) -> list[dict]:
        return [dict(p) for p in self.PRODUCTS][:limit]

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        name = str(category_identifier).strip()
        for c in self.CATEGORIES:
            if str(c["id"]) == name:
                name = str(c["name"])
        return [
            dict(p) for p in self.PRODUCTS if p.get("category") == name
        ][:limit]

    async def get_landing_content(self) -> dict:
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
    app.dependency_overrides[get_directus_client] = lambda: _FakeDirectusClient()

    async with Session() as s:
        # Stock rows for the fake's products (the in-stock filter
        # reads these, not the fake's stock numbers): the 55-inch TV
        # has 5, the 65-inch has 0, the washer 3, the 10-kg washer 2,
        # the fridge 4.
        s.add_all(
            [
                ProductStock(
                    directus_product_id="11111111-1111-4111-8111-111111111111",
                    stock=5,
                ),
                ProductStock(
                    directus_product_id="22222222-2222-4222-8222-222222222222",
                    stock=0,
                ),
                ProductStock(
                    directus_product_id="33333333-3333-4333-8333-333333333333",
                    stock=3,
                ),
                ProductStock(
                    directus_product_id="44444444-4444-4444-8444-444444444444",
                    stock=2,
                ),
                ProductStock(
                    directus_product_id="55555555-5555-4555-8555-555555555555",
                    stock=4,
                ),
            ]
        )
        await s.commit()

    html_headers = {"accept": "text/html"}

    # 0. Param parsing + the Python matcher, direct unit checks --------
    assert _parse_price_param(None) is None
    assert _parse_price_param("32000000") == 32_000_000
    assert _parse_price_param(" 32,000,000 ") == 32_000_000
    assert _parse_price_param("abc") is None
    assert _parse_price_param("") is None
    assert _parse_price_param("-5") is None
    from starlette.datastructures import QueryParams

    parsed = _parse_filter_params(
        QueryParams("spec_screen_size_inch=55&price_min=1000&in_stock=1")
    )
    assert parsed["spec_filters"] == {"screen_size_inch": ["55"]}, parsed
    assert parsed["price_min"] == 1000 and parsed["in_stock"] is True
    assert parsed["has_filters"] is True
    assert _parse_filter_params(QueryParams(""))["has_filters"] is False
    # Bad spec key is dropped, never an error.
    assert _parse_filter_params(
        QueryParams("spec_bad-key=1&spec_capacity_kg=8")
    )["spec_filters"] == {"capacity_kg": ["8"]}

    tv55 = {
        "specs": {"screen_size_inch": 55},
        "price_toman": 32_000_000,
        "condition": "new",
        "available": 5,
    }
    assert _matches_product_filters(tv55, {"screen_size_inch": ["55"]}, None, None, None, False)
    assert not _matches_product_filters(tv55, {"screen_size_inch": ["65"]}, None, None, None, False)
    assert not _matches_product_filters(tv55, {}, 33_000_000, None, None, False)
    assert not _matches_product_filters(tv55, {}, None, 30_000_000, None, False)
    assert not _matches_product_filters(tv55, {}, None, None, "used", False)
    assert not _matches_product_filters(
        {**tv55, "available": 0}, {}, None, None, None, True
    )
    assert _matches_product_filters(tv55, {}, None, None, None, True)
    # No specs + a spec filter = no match; no filter = match.
    assert not _matches_product_filters(
        {"specs": {}, "price_toman": 1, "available": 1},
        {"screen_size_inch": ["55"]}, None, None, None, False,
    )
    # Distinct values come from the products, numbers first.
    groups = _distinct_spec_values(
        [
            {"specs": {"screen_size_inch": 65}},
            {"specs": {"screen_size_inch": 55}},
            {"specs": {}},
        ],
        [{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ"}],
    )
    assert groups == [
        {"key": "screen_size_inch", "label": "اندازه صفحه",
         "unit": "اینچ", "options": [55, 65]}
    ], groups
    # A definition with no values among the products is left out.
    assert _distinct_spec_values(
        [{"specs": {}}],
        [{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ"}],
    ) == []
    assert _apply_product_filters([tv55], {"spec_filters": {}, "price_min": None,
        "price_max": None, "condition": None, "in_stock": False,
        "has_filters": False}) == [tv55]
    print("   [OK] filter param parsing, matcher, distinct values")

    with TestClient(app) as client:
        # 1. Category page: the TV panel shows screen size only --------
        resp = client.get("/category/2", headers=html_headers)
        assert resp.status_code == 200, resp.status_code
        html = resp.text
        assert "تلویزیون ۵۵ اینچ" in html and "تلویزیون ۶۵ اینچ" in html
        assert "اندازه صفحه" in html, "TV panel must show the screen-size filter"
        assert "55 اینچ" in html and "65 اینچ" in html
        assert "کیلوگرم" not in html, "washer capacity must not show on the TV page"
        assert "لیتر" not in html, "fridge capacity must not show on the TV page"
        assert "فیلترها" in html
        assert "قیمت (تومان)" in html, "shared price filter must show"
        assert "فقط کالاهای موجود" in html
        assert 'data-filter-panel' in html
        assert "۲ کالا" in html, "count must show the unfiltered total"
        print("   [OK] TV category page shows only its own attribute filter")

        # Washer page: capacity in kg, no screen size.
        resp = client.get("/category/3", headers=html_headers)
        html = resp.text
        assert "ظرفیت" in html and "8 کیلوگرم" in html and "10 کیلوگرم" in html
        assert "اندازه صفحه" not in html
        print("   [OK] washer category page shows only its capacity filter")

        # Fridge page: capacity in litres (450 L placeholder — see
        # docs/product-attributes.md), no TV or washer attribute.
        resp = client.get("/category/1", headers=html_headers)
        html = resp.text
        assert resp.status_code == 200, resp.status_code
        assert "یخچال دو درب" in html
        assert "ظرفیت" in html and "450 لیتر" in html
        assert "اندازه صفحه" not in html
        assert "کیلوگرم" not in html
        assert "۱ کالا" in html
        print("   [OK] fridge category page shows only its 450 L capacity filter")

        # 2. Spec filter: only the matching TV, count updates ----------
        resp = client.get(
            "/category/2",
            params={"spec_screen_size_inch": "55"},
            headers=html_headers,
        )
        html = resp.text
        assert "تلویزیون ۵۵ اینچ" in html
        assert "تلویزیون ۶۵ اینچ" not in html
        assert "۱ کالا" in html, "count must reflect the filtered list"
        # The panel re-renders its state from the URL: 55 stays checked.
        assert 'name="spec_screen_size_inch"' in html
        assert 'value="55"' in html and "checked" in html
        # HTMX: the panel submits into <main> and pushes the URL.
        assert 'hx-get="/category/2"' in html
        assert 'hx-target="main"' in html
        assert 'hx-push-url="true"' in html
        assert 'hx-trigger="change, submit"' in html
        print("   [OK] spec filter narrows the grid, count updates, panel keeps state")

        # 3. Price range filters ---------------------------------------
        resp = client.get(
            "/category/3",
            params={"price_min": "30000000"},
            headers=html_headers,
        )
        assert "ماشین لباسشویی ۱۰ کیلویی" in resp.text
        assert "ماشین لباسشویی ۸ کیلویی" not in resp.text
        resp = client.get(
            "/category/3",
            params={"price_max": "30000000"},
            headers=html_headers,
        )
        assert "ماشین لباسشویی ۸ کیلویی" in resp.text
        assert "ماشین لباسشویی ۱۰ کیلویی" not in resp.text
        # The price inputs keep their values after the swap.
        assert 'name="price_max" value="30000000"' in resp.text
        print("   [OK] price range filters (min and max)")

        # 4. In-stock only: the 65-inch TV has stock 0 ---------------
        resp = client.get(
            "/category/2", params={"in_stock": "1"}, headers=html_headers
        )
        assert "تلویزیون ۵۵ اینچ" in resp.text
        assert "تلویزیون ۶۵ اینچ" not in resp.text
        print("   [OK] in-stock filter hides the out-of-stock TV")

        # 5. Condition filter on the washer page -----------------------
        resp = client.get(
            "/category/3", params={"condition": "used"}, headers=html_headers
        )
        assert "ماشین لباسشویی ۸ کیلویی" in resp.text
        assert "ماشین لباسشویی ۱۰ کیلویی" not in resp.text
        resp = client.get(
            "/category/3", params={"condition": "new"}, headers=html_headers
        )
        assert "ماشین لباسشویی ۱۰ کیلویی" in resp.text
        assert "ماشین لباسشویی ۸ کیلویی" not in resp.text
        # An unknown condition means "all", never empty.
        resp = client.get(
            "/category/3", params={"condition": "broken"}, headers=html_headers
        )
        assert "ماشین لباسشویی ۸ کیلویی" in resp.text
        assert "ماشین لباسشویی ۱۰ کیلویی" in resp.text
        print("   [OK] condition filter (used / new / unknown means all)")

        # 6. Clear-all: the bare URL returns the unfiltered list ------
        resp = client.get("/category/2", headers=html_headers)
        assert "تلویزیون ۵۵ اینچ" in resp.text
        assert "تلویزیون ۶۵ اینچ" in resp.text
        assert "۲ کالا" in resp.text
        # The clear-all link in a filtered view points at the bare URL.
        resp = client.get(
            "/category/2",
            params={"spec_screen_size_inch": "55"},
            headers=html_headers,
        )
        assert 'href="/category/2"' in resp.text, "clear-all must go to the bare category URL"
        assert "پاک کردن فیلترها" in resp.text
        print("   [OK] clear-all URL returns the unfiltered list")

        # 7. A spec value with no match shows the empty state ---------
        resp = client.get(
            "/category/2",
            params={"spec_screen_size_inch": "75"},
            headers=html_headers,
        )
        assert "تلویزیون ۵۵ اینچ" not in resp.text
        assert "کالایی با این فیلترها یافت نشد" in resp.text
        assert "پاک کردن فیلترها" in resp.text
        print("   [OK] spec value with no match returns the empty state")

        # 8. /search, one category: attribute panel + spec filter -----
        resp = client.get(
            "/search",
            params=[("category", "تلویزیون و صدا")],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "اندازه صفحه" in html, "single category must show its attribute panel"
        assert 'name="spec_screen_size_inch"' in html
        assert "55 اینچ" in html and "65 اینچ" in html
        resp = client.get(
            "/search",
            params=[
                ("q", "تلویزیون"),
                ("category", "تلویزیون و صدا"),
                ("spec_screen_size_inch", "55"),
            ],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "تلویزیون ۵۵ اینچ" in html
        assert "تلویزیون ۶۵ اینچ" not in html
        assert "۱ کالا" in html
        # The panel's clear-all link keeps only the text query: the
        # picked category and the spec pick are dropped. (The link
        # sits just before the "پاک کردن فیلترها" label.)
        idx = html.index("پاک کردن فیلترها")
        window = html[max(0, idx - 400):idx]
        assert 'href="/search?q=تلویزیون"' in window, window
        assert "category=" not in window, "clear-all must drop the category"
        assert "spec_" not in window, "clear-all must drop the spec pick"
        print("   [OK] /search with one category shows the attribute panel and filters by it")
        print("   [OK] /search clear-all keeps only the text query")

        # 9. /search, two categories: shared filters only -------------
        resp = client.get(
            "/search",
            params=[("category", "تلویزیون و صدا"), ("category", "شستشو")],
            headers=html_headers,
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "تلویزیون ۵۵ اینچ" in html and "ماشین لباسشویی ۸ کیلویی" in html
        assert "اندازه صفحه" not in html, "two categories must hide attribute filters"
        assert "کیلوگرم" not in html
        assert 'name="spec_' not in html, "no spec inputs with two categories"
        assert "قیمت (تومان)" in html, "shared price filter must still show"
        assert "فقط کالاهای موجود" in html
        print("   [OK] /search with two categories shows only shared filters")

        # 10. /search, no category: shared filters only ---------------
        resp = client.get(
            "/search", params=[("q", "سامسونگ")], headers=html_headers
        )
        assert 'name="spec_' not in resp.text
        assert "قیمت (تومان)" in resp.text
        # ... and a price filter alone (no q, no category) still searches.
        resp = client.get(
            "/search", params=[("price_max", "30000000")], headers=html_headers
        )
        html = unquote_plus(resp.text.replace("&amp;", "&"))
        assert "ماشین لباسشویی ۸ کیلویی" in html
        assert "یخچال دو درب" not in html
        assert "تلویزیون ۵۵ اینچ" not in html
        print("   [OK] /search with no category shows only shared filters; price-only search works")

        # 11. JSON /search applies the same filters --------------------
        resp = client.get(
            "/search",
            params={"q": "تلویزیون", "spec_screen_size_inch": "55"},
        )
        assert resp.status_code == 200
        assert [p["name"] for p in resp.json()] == ["تلویزیون ۵۵ اینچ"], resp.json()
        resp = client.get(
            "/search",
            params={"q": "تلویزیون", "in_stock": "1"},
        )
        assert [p["name"] for p in resp.json()] == ["تلویزیون ۵۵ اینچ"], resp.json()
        print("   [OK] JSON /search applies spec and in-stock filters")

    print("All filter checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
