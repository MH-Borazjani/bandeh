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
"""Order service: turn a cart into a frozen order.

Creating an order freezes prices (copied into OrderItem rows) and
checks availability — but it does NOT reserve stock. Reservation
happens in ``payment_service.start_payment`` under a row lock, because
that is the moment money is about to move. Between creation and start,
stock can sell out; start re-checks and refuses if it did.

The service owns its writes: it commits when the order is stored, so
the route never has to.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.money import toman_to_rial
from db.models.order import Order, OrderItem, OrderStatus
from db.models.product import Product
from db.models.user import User
from schema.order_api import OrderItemCreate


class OrderItemInput(TypedDict):
    """Dictionary representation of a cart item line for internal callers/tests."""

    product_id: uuid.UUID
    quantity: int


class OrderError(Exception):
    """An order cannot be created/used: ``detail`` is the human reason."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def load_order(db: AsyncSession, order_id: uuid.UUID) -> Order | None:
    """Load an order with its items and attempts (async has no lazy load)."""
    result = await db.execute(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.items), selectinload(Order.attempts))
    )
    return result.scalar_one_or_none()


async def load_order_for_user(
    db: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID
) -> Order | None:
    """Load an order only when it belongs to ``user_id`` (else None).

    Returning None for another user's order is deliberate: the route
    answers 404 either way, so order ids cannot be probed.
    """
    order = await load_order(db, order_id)
    if order is None or order.user_id != user_id:
        return None
    return order


async def list_orders_for_user(
    db: AsyncSession, user_id: uuid.UUID
) -> Sequence[Order]:
    """Load all orders belonging to ``user_id``, newest first, with items and attempts."""
    result = await db.execute(
        select(Order)
        .where(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .options(selectinload(Order.items), selectinload(Order.attempts))
    )
    return result.scalars().all()


async def create_order(
    db: AsyncSession,
    user: User,
    items: Sequence[OrderItemCreate] | Sequence[OrderItemInput],
    recipient_name: str | None = None,
    recipient_phone: str | None = None,
    delivery_address: str | None = None,
) -> Order:
    """Create a frozen order from cart items.

    Raises OrderError when a product is missing, inactive, or short on
    stock. Prices are frozen from the product rows at this moment.
    """
    if not items:
        raise OrderError("an order needs at least one item")

    # Aggregate quantities per product_id so duplicate lines cannot bypass stock checks
    merged_quantities: dict[uuid.UUID, int] = {}
    for item in items:
        if isinstance(item, OrderItemCreate):
            pid = item.product_id
            quantity = item.quantity
        else:
            pid = item["product_id"]
            quantity = int(item["quantity"])
        if quantity < 1:
            raise OrderError("quantity must be at least 1")
        merged_quantities[pid] = merged_quantities.get(pid, 0) + quantity

    total_toman = 0
    order_items: list[OrderItem] = []
    for pid, quantity in merged_quantities.items():
        product = await db.get(Product, pid)
        if product is None or not product.is_active:
            raise OrderError(f"product {pid} is not available")
        if quantity > product.available:
            raise OrderError(
                f"only {product.available} of {product.name} available"
            )
        total_toman += product.price_toman * quantity
        order_items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price_toman=product.price_toman,
            )
        )

    order = Order(
        user_id=user.id,
        status=OrderStatus.CREATED.value,
        total_toman=total_toman,
        total_rial=toman_to_rial(total_toman),
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        delivery_address=delivery_address,
    )
    db.add(order)
    await db.flush()
    # Do NOT append to order.items here: the collection was never
    # loaded, and appending would lazy-load it (MissingGreenlet in
    # async). Set the FK directly; the re-load below eager-loads it.
    for order_item in order_items:
        order_item.order_id = order.id
        db.add(order_item)
    await db.flush()
    await db.commit()
    # Re-load with items eager-loaded: after commit the in-memory
    # collection is not guaranteed loaded, and async code cannot lazy
    # load it (MissingGreenlet). The route validates this return value.
    reloaded = await load_order(db, order.id)
    assert reloaded is not None  # just created it
    return reloaded


__all__ = [
    "OrderError",
    "OrderItemInput",
    "create_order",
    "list_orders_for_user",
    "load_order",
    "load_order_for_user",
]
