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

In-memory SQLite only — no server, no live Postgres. Covers:

1. The Product model round-trips specs on SQLite (the JSONB column
   uses a plain-JSON variant there), defaults to {}, and in-place
   edits to the dict are tracked and saved (MutableDict).
2. The spec filter compiles against the Postgres dialect with bound
   parameters only (``specs ->> 'key' IN (...)``) — never run on
   SQLite, because SQLite's ``->>`` returns a number where Postgres
   returns text, so the same comparison would miss. Real filtering is
   verified on the laptop Postgres (see docs/product-attributes.md).
3. clean_spec_filters drops bad keys, empty lists and None values.
4. The Directus client maps specs and filter_attributes
   (mocked HTTP, no live Directus).
5. The sync logic: mocked Directus rows -> Postgres specs
   (extract_specs + upsert_specs from scripts/sync_product_specs.py).
   The script lives outside the repo (~/workspace/scripts/ on the
   dev machine), so inside a container — where that path does not
   exist — this group is SKIPPED cleanly instead of failing; the
   other four groups still run. Set SYNC_PRODUCT_SPECS_SCRIPT to
   point at the script explicitly when it lives elsewhere.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.base import Base
from db.models.product import Product
from integrations.directus_client import (
    DirectusClient,
    _clean_filter_attributes_value,
    _clean_specs_value,
    _product_row,
)
from services.search_service import (
    apply_spec_filters,
    build_spec_filter_statement,
    clean_spec_filters,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sync_script_candidates() -> list[Path]:
    """Where the sync script may live, most specific first.

    The script is an ops script outside the repo; on the dev machine
    it is ~/workspace/scripts/sync_product_specs.py, inside a
    container that path does not exist (the repo is bind-mounted but
    the host home is not). A repo-relative copy, or the
    SYNC_PRODUCT_SPECS_SCRIPT env var, wins when present.
    """
    candidates: list[Path] = [REPO_ROOT / "scripts" / "sync_product_specs.py"]
    if os.environ.get("SYNC_PRODUCT_SPECS_SCRIPT"):
        candidates.append(Path(os.environ["SYNC_PRODUCT_SPECS_SCRIPT"]))
    candidates.append(Path.home() / "workspace" / "scripts" / "sync_product_specs.py")
    return candidates


def find_sync_script() -> Path | None:
    """First candidate that exists, or None when the script is absent."""
    for candidate in _sync_script_candidates():
        if candidate.is_file():
            return candidate
    return None


def load_sync_module():
    """Load the sync script as a module (None when it is absent)."""
    script = find_sync_script()
    if script is None:
        return None
    spec = importlib.util.spec_from_file_location("sync_product_specs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pg_sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}
        )
    )


async def main_async() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # 1. Model round-trip: specs survive write + read, default is {} -----
    tv_id = uuid.uuid4()
    async with Session() as s:
        s.add_all(
            [
                Product(
                    id=tv_id,
                    name="تلویزیون ۵۵ اینچ",
                    price_toman=32_000_000,
                    stock=2,
                    specs={"screen_size_inch": 55},
                ),
                Product(
                    name="ماشین لباسشویی ۸ کیلویی",
                    price_toman=28_500_000,
                    stock=3,
                    specs={"capacity_kg": 8},
                ),
                Product(
                    name="یخچال دو درب",
                    price_toman=45_000_000,
                    stock=1,
                    specs={"capacity_liter": 450},
                ),
                Product(name="بدون مشخصات", price_toman=1_000_000, stock=1),
            ]
        )
        await s.commit()
    async with Session() as s:
        tv = await s.get(Product, tv_id)
        assert tv is not None and tv.specs == {"screen_size_inch": 55}, tv
        plain = (
            await s.execute(select(Product).where(Product.name == "بدون مشخصات"))
        ).scalar_one()
        assert plain.specs == {}, f"default specs must be {{}}, got {plain.specs!r}"
        # In-place edit is tracked (MutableDict) and saved.
        tv.specs["screen_size_inch"] = 65
        await s.commit()
    async with Session() as s:
        tv = await s.get(Product, tv_id)
        assert tv is not None and tv.specs == {"screen_size_inch": 65}, tv
    print("   [OK] model round-trips specs, defaults to {}, in-place edits saved")

    # 2. Spec filter compiles for Postgres, bound params only -------------
    stmt = build_spec_filter_statement({"screen_size_inch": [55, 65]})
    sql = _pg_sql(stmt)
    assert "->>" in sql, sql
    assert "specs" in sql, sql
    assert "55" not in sql and "screen_size_inch" not in sql, (
        "keys and values must stay bound parameters, not inline SQL: " + sql
    )
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert "screen_size_inch" in params.values(), params
    assert ["55", "65"] in params.values(), params
    assert "LIMIT" in sql, sql
    assert "is_active" in sql, sql
    # Two attributes = AND of two clauses; empty filters = no spec clause.
    sql2 = _pg_sql(
        build_spec_filter_statement(
            {"screen_size_inch": [55], "capacity_kg": [8]}
        )
    )
    assert sql2.count("->>") == 2, sql2
    assert "->>" not in _pg_sql(build_spec_filter_statement({})), (
        "no filters must add no spec clause"
    )
    assert "->>" not in _pg_sql(build_spec_filter_statement(None))
    # apply_spec_filters adds the clauses to any product statement.
    base = select(Product).where(Product.is_active == True)  # noqa: E712
    assert "->>" in _pg_sql(apply_spec_filters(base, {"capacity_kg": [8]}))
    # Injection-shaped key/value never reach the SQL text: the bad key
    # is dropped by clean_spec_filters, so no spec clause is added.
    sql3 = _pg_sql(
        build_spec_filter_statement({"x': DROP TABLE products; --": ["1' OR '1'='1"]})
    )
    assert "->>" not in sql3 and "DROP" not in sql3, sql3
    print("   [OK] spec filter compiles for Postgres with bound parameters only")

    # 3. clean_spec_filters: bad input becomes "no filter" -----------------
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

    # 4. Directus client: specs + filter_attributes -----------------------
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

    # 5. Sync logic: mocked Directus rows -> DB specs ----------------------
    # Absent script (e.g. inside the laptop container, where the host
    # ~/workspace is not mounted): skip this group cleanly — the
    # script is an ops file outside the repo, not part of the shop.
    sync = load_sync_module()
    if sync is None:
        print(
            "   [SKIP] sync logic: sync_product_specs.py not found "
            f"(looked in {[str(c) for c in _sync_script_candidates()]})"
        )
        print("All product-attribute checks passed.")
        return
    assert sync.extract_specs(
        [
            {"id": "4a9be4d9-5194-4b0d-b401-9594ebb8d030",
             "specs": {"screen_size_inch": 55}},
            {"id": "4b31ea7d-c2df-4068-877a-983cfa2734ba",
             "specs": {"capacity_kg": 8}},
            {"id": "ad7d6a7f-3d92-412c-a523-55deb334fa8c", "specs": None},
            {"id": "00000000-0000-0000-0000-000000000000",
             "specs": {"capacity_liter": 450}},
            {"specs": {"x": 1}},  # no id: skipped
            {"id": "11111111-1111-1111-1111-111111111111",
             "specs": "not a dict"},  # bad specs: {}
        ]
    ) == {
        "4a9be4d9-5194-4b0d-b401-9594ebb8d030": {"screen_size_inch": 55},
        "4b31ea7d-c2df-4068-877a-983cfa2734ba": {"capacity_kg": 8},
        "ad7d6a7f-3d92-412c-a523-55deb334fa8c": {},
        "00000000-0000-0000-0000-000000000000": {"capacity_liter": 450},
        "11111111-1111-1111-1111-111111111111": {},
    }

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession

    sync_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(sync_engine)
    tv_uuid = uuid.UUID("4a9be4d9-5194-4b0d-b401-9594ebb8d030")
    washer_uuid = uuid.UUID("4b31ea7d-c2df-4068-877a-983cfa2734ba")
    with SyncSession(sync_engine) as s:
        s.add_all(
            [
                Product(id=tv_uuid, name="TV", price_toman=1, stock=1),
                Product(id=washer_uuid, name="Washer", price_toman=1, stock=1),
            ]
        )
        s.commit()
    updated, missing = sync.upsert_specs(
        sync_engine,
        {
            "4a9be4d9-5194-4b0d-b401-9594ebb8d030": {"screen_size_inch": 55},
            "4b31ea7d-c2df-4068-877a-983cfa2734ba": {"capacity_kg": 8},
            "00000000-0000-0000-0000-000000000000": {"capacity_liter": 450},
        },
    )
    assert updated == 2, updated
    assert missing == ["00000000-0000-0000-0000-000000000000"], missing
    with SyncSession(sync_engine) as s:
        assert s.get(Product, tv_uuid).specs == {"screen_size_inch": 55}
        assert s.get(Product, washer_uuid).specs == {"capacity_kg": 8}
    print("   [OK] sync logic: mocked Directus rows -> DB specs, missing reported")

    print("All product-attribute checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
