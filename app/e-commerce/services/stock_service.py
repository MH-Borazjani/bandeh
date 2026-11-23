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
"""Product stock: the one place that reads and writes stock numbers.

Directus owns product content; this service owns the stock table
(``product_stock`` — see db/models/product_stock.py). Every read of
stock and every reserve / release / sell goes through here, so there
is exactly one place where the numbers can go wrong.

Two rules the whole shop depends on:

- A product with no stock row has ZERO stock. It cannot be sold
  until staff sets its stock — a new product in Directus is not
  sellable by accident.
- Stock is read from this table ONLY. The Directus product rows still
  carry ``stock`` / ``reserved`` columns (the shared table), but
  those are dead: ``attach_stock`` overwrites them with the real
  numbers before any page, search result, or MCP answer is built.

Write contract (same as cart_service): these functions flush, the
caller commits (routes do it via get_db; payment/order services
commit themselves).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.product_stock import ProductStock


def _stock_key(product_id: uuid.UUID | str) -> str:
    """The stock-table key for a product id: the UUID as plain text."""
    return str(product_id)


async def get_stock(
    db: AsyncSession, product_id: uuid.UUID | str
) -> ProductStock | None:
    """One product's stock row, or None when it has no row (zero stock)."""
    return await db.get(ProductStock, _stock_key(product_id))


async def get_or_create_stock(
    db: AsyncSession, product_id: uuid.UUID | str, stock: int = 0
) -> ProductStock:
    """The stock row for a product, creating it at ``stock`` if missing."""
    row = await get_stock(db, product_id)
    if row is None:
        row = ProductStock(directus_product_id=_stock_key(product_id), stock=stock)
        db.add(row)
        await db.flush()
    return row


async def set_stock(
    db: AsyncSession, product_id: uuid.UUID | str, stock: int
) -> ProductStock:
    """Staff: set how many units of a product the shop owns.

    Raises ValueError when the new stock is below what is already
    reserved (those units are held for unpaid orders — sell them
    first, or wait for the holds to expire). Creates the row when
    the product has none yet.
    """
    if stock < 0:
        raise ValueError("stock cannot be negative")
    row = await get_or_create_stock(db, product_id)
    if stock < row.reserved:
        raise ValueError(
            f"stock {stock} is below the {row.reserved} reserved units"
        )
    row.stock = stock
    await db.flush()
    return row


async def available_for(db: AsyncSession, product_id: uuid.UUID | str) -> int:
    """Units a customer can buy right now (0 when the product has no row)."""
    row = await get_stock(db, product_id)
    return row.available if row is not None else 0


async def attach_stock(
    db: AsyncSession, rows: list[dict]
) -> list[dict]:
    """Overwrite the stock numbers of Directus product dicts, in place.

    Each dict must carry its Directus ``id``. The ``stock`` and
    ``reserved`` the Directus row carries are dead — this replaces
    them with the stock table's numbers (missing row means 0 / 0),
    and adds ``available``. One query for the whole list. Returns
    the same list, so callers can chain it.
    """
    ids = [str(row["id"]) for row in rows if row.get("id") is not None]
    by_id: dict[str, ProductStock] = {}
    if ids:
        result = await db.execute(
            select(ProductStock).where(
                ProductStock.directus_product_id.in_(ids)
            )
        )
        by_id = {r.directus_product_id: r for r in result.scalars().all()}
    for row in rows:
        stock_row = by_id.get(str(row.get("id")))
        row["stock"] = stock_row.stock if stock_row is not None else 0
        row["reserved"] = stock_row.reserved if stock_row is not None else 0
        row["available"] = (
            stock_row.available if stock_row is not None else 0
        )
    return rows


async def attach_stock_one(db: AsyncSession, row: dict | None) -> dict | None:
    """The single-row version of :func:`attach_stock` (None stays None)."""
    if row is None:
        return None
    await attach_stock(db, [row])
    return row


async def lock_stock_rows(
    db: AsyncSession, product_ids: list[uuid.UUID | str]
) -> dict[str, ProductStock]:
    """Load stock rows with a row lock (FOR UPDATE), keyed by product id.

    On Postgres this serialises two checkouts racing for the same
    stock. On SQLite the clause is ignored (SQLite has no row locks),
    which is why tests use one checkout at a time. A product with no
    row is simply absent from the result — the caller treats that as
    zero stock.
    """
    keys = [_stock_key(pid) for pid in product_ids]
    if not keys:
        return {}
    result = await db.execute(
        select(ProductStock)
        .where(ProductStock.directus_product_id.in_(keys))
        .with_for_update()
    )
    return {r.directus_product_id: r for r in result.scalars().all()}


__all__ = [
    "attach_stock",
    "attach_stock_one",
    "available_for",
    "get_or_create_stock",
    "get_stock",
    "lock_stock_rows",
    "set_stock",
]
