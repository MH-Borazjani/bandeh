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
product list + its public Cache-Control header, and the Postgres
full-text SQL compiled (not run) against the Postgres dialect.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.base import Base
from db.models.product import Product
from db.session import get_db
from main import app
from services.search_service import (
    MAX_LIMIT,
    build_fulltext_statement,
    search_products_ilike,
)


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
    client = TestClient(app)

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

    print("All search checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
