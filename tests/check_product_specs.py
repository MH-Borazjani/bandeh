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
"""Runnable self-check for product attributes (specs). Run from repo root:

    python tests/check_product_specs.py

Covers:

1. clean_spec_filters drops bad keys, empty lists and None values.
2. The Directus client maps specs and filter_attributes
   (mocked HTTP, no live Directus).

Specs live in Directus now (products.specs) and are filtered in
Python by the routes — there is no Product model, no Postgres spec
statement, and no sync script anymore: the old sync script
(sync_product_specs.py) mirrored Directus specs into the database
products table, which the shop no longer reads.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx

from integrations.directus_client import (
    DirectusClient,
    _clean_filter_attributes_value,
    _clean_specs_value,
    _product_row,
)
from services.search_service import clean_spec_filters


async def main_async() -> None:
    # 1. clean_spec_filters: bad input becomes "no filter" -----------------
    assert clean_spec_filters(None) == {}
    assert clean_spec_filters({}) == {}
    assert clean_spec_filters({"screen_size_inch": [55, 65]}) == {
        "screen_size_inch": ["55", "65"]
    }
    assert clean_spec_filters({"screen-size": [55]}) == {}, "hyphen key dropped"
    assert clean_spec_filters({"a;b": [1]}) == {}, "semicolon key dropped"
    assert clean_spec_filters({"capacity_kg": []}) == {}, "empty list dropped"
    assert clean_spec_filters({"capacity_kg": [None, 8]}) == {"capacity_kg": ["8"]}
    assert clean_spec_filters({"capacity_kg": "8"}) == {}, "non-list dropped"
    assert clean_spec_filters({"capacity_kg": [8], "bad key": [1]}) == {
        "capacity_kg": ["8"]
    }
    print("   [OK] clean_spec_filters drops bad keys, empty lists, None values")

    # 2. Directus client: specs + filter_attributes -----------------------
    assert _clean_specs_value({"screen_size_inch": 55}) == {"screen_size_inch": 55}
    assert _clean_specs_value(None) == {}
    assert _clean_specs_value("not a dict") == {}
    assert _clean_specs_value([1, 2]) == {}
    assert _clean_filter_attributes_value(
        [{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ",
          "type": "number"}]
    ) == [{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ",
           "type": "number"}]
    assert _clean_filter_attributes_value(None) == []
    assert _clean_filter_attributes_value("nope") == []
    assert _clean_filter_attributes_value([{"label": "no key"}, "x"]) == []
    row = _product_row(
        {
            "id": "4a9be4d9-5194-4b0d-b401-9594ebb8d030",
            "name": "تلویزیون ۵۵ اینچ",
            "price_toman": 32_000_000,
            "specs": {"screen_size_inch": 55},
        }
    )
    assert row["specs"] == {"screen_size_inch": 55}, row
    row_no_specs = _product_row(
        {
            "id": "4a9be4d9-5194-4b0d-b401-9594ebb8d030",
            "name": "تلویزیون ۵۵ اینچ",
            "price_toman": 32_000_000,
        }
    )
    assert row_no_specs["specs"] == {}, row_no_specs

    async def _mock_categories(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/categories"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 2,
                        "name": "تلویزیون و صدا [TEST]",
                        "description": "",
                        "icon": None,
                        "sort": 2,
                        "filter_attributes": [
                            {"key": "screen_size_inch", "label": "اندازه صفحه",
                             "unit": "اینچ", "type": "number"}
                        ],
                    },
                    {
                        "id": 3,
                        "name": "شستشو [TEST]",
                        "description": "",
                        "icon": None,
                        "sort": 3,
                        "filter_attributes": [
                            {"key": "capacity_kg", "label": "ظرفیت",
                             "unit": "کیلوگرم", "type": "number"}
                        ],
                    },
                ]
            },
        )

    transport = httpx.MockTransport(_mock_categories)
    async with httpx.AsyncClient(transport=transport, base_url="http://directus") as http:
        client = DirectusClient(http, base_url="http://directus")
        attrs = await client.get_category_filter_attributes()
    assert attrs[2] == [
        {"key": "screen_size_inch", "label": "اندازه صفحه",
         "unit": "اینچ", "type": "number"}
    ], attrs
    assert attrs["2"] == attrs[2], "string id must map too"
    assert attrs[3][0]["key"] == "capacity_kg", attrs
    print("   [OK] Directus client maps specs and category filter_attributes")

    print("All product-attribute checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
