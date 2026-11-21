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
"""The MCP server's cart: one server-side cart per customer.

The website's cart lives only in the browser (a localStorage draft),
which no AI app can see. These functions are the cart the MCP write
tools use instead. Adding checks the product exists, is active, and has
enough stock free (``stock - reserved``); the order draft later freezes
prices and the payment step reserves stock, so the cart itself holds no
money. Callers commit the session (routes do it via get_db; MCP tools
commit after each call).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.cart_item import CartItem
from db.models.product import Product
from db.models.user import User


class CartError(ValueError):
    """The cart cannot do what was asked (bad product, no stock, empty)."""


async def get_cart_items(db: AsyncSession, user: User) -> list[CartItem]:
    """All cart lines of one customer, oldest first."""
    stmt = (
        select(CartItem)
        .where(CartItem.user_id == user.id)
        .order_by(CartItem.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def add_to_cart(
    db: AsyncSession, user: User, product_id: uuid.UUID, quantity: int = 1
) -> CartItem:
    """Add a product to the cart (raises quantity when already there)."""
    if quantity < 1:
        raise CartError("quantity must be at least 1")
    product = await db.get(Product, product_id)
    if product is None or not product.is_active:
        raise CartError("no such product")
    stmt = select(CartItem).where(
        CartItem.user_id == user.id, CartItem.product_id == product_id
    )
    line = (await db.execute(stmt)).scalar_one_or_none()
    new_quantity = quantity + (line.quantity if line is not None else 0)
    if product.available < new_quantity:
        raise CartError("not enough stock")
    if line is None:
        line = CartItem(user_id=user.id, product_id=product_id, quantity=new_quantity)
        db.add(line)
    else:
        line.quantity = new_quantity
    await db.flush()
    return line


async def remove_from_cart(
    db: AsyncSession, user: User, product_id: uuid.UUID, quantity: int | None = None
) -> bool:
    """Remove a line, or lower its quantity. False when the line is missing.

    ``quantity=None`` removes the whole line; otherwise the line's
    quantity drops by that much (and the line disappears at zero).
    """
    stmt = select(CartItem).where(
        CartItem.user_id == user.id, CartItem.product_id == product_id
    )
    line = (await db.execute(stmt)).scalar_one_or_none()
    if line is None:
        return False
    if quantity is None or quantity >= line.quantity:
        await db.delete(line)
    else:
        if quantity < 1:
            raise CartError("quantity must be at least 1")
        line.quantity -= quantity
    await db.flush()
    return True


async def clear_cart(db: AsyncSession, user: User) -> None:
    """Empty one customer's cart (used after a successful order draft)."""
    await db.execute(delete(CartItem).where(CartItem.user_id == user.id))
    await db.flush()
