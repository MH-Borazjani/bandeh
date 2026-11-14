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
"""Order models: an order is a FROZEN snapshot, not a live cart.

When a customer checks out, the cart becomes an order: prices and
product names are copied into ``OrderItem`` rows at that moment. Later
price changes never rewrite history — the payment, the receipt and the
refund all read the frozen numbers.

State machine (``OrderStatus``), one direction at a time:
created -> waiting_payment -> paid | failed | expired.
``failed`` covers user-cancelled and gateway-refused; ``expired``
covers the 15-minute stock hold running out.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models.payment_attempt import PaymentAttempt
    from db.models.user import User


class OrderStatus(StrEnum):
    """Where an order is in its life."""

    CREATED = "created"
    WAITING_PAYMENT = "waiting_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXPIRED = "expired"


class Order(Base, TimestampMixin):
    """One checkout: a frozen total for one user."""

    __tablename__ = "orders"

    __table_args__ = (
        CheckConstraint("total_toman >= 0", name="ck_orders_total_toman_nonnegative"),
        CheckConstraint("total_rial >= 0", name="ck_orders_total_rial_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OrderStatus.CREATED.value,
        index=True,
    )
    # Frozen totals. total_rial is always total_toman × 10 (core/money).
    total_toman: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    total_rial: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    # Delivery address details for the order
    recipient_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    recipient_phone: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    delivery_address: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    tracking_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        "User",
        lazy="joined",
    )
    items: Mapped[list[OrderItem]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    attempts: Mapped[list[PaymentAttempt]] = relationship(
        "PaymentAttempt",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status} total_toman={self.total_toman}>"


class OrderItem(Base, TimestampMixin):
    """One line of a frozen order: product, quantity, price at checkout."""

    __tablename__ = "order_items"

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price_toman >= 0", name="ck_order_items_price_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Frozen snapshot of the product name: the receipt outlives renames.
    product_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    # Frozen unit price in Toman at checkout time.
    unit_price_toman: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    order: Mapped[Order] = relationship(
        "Order",
        back_populates="items",
    )

    def __repr__(self) -> str:
        return f"<OrderItem {self.product_name!r} x{self.quantity}>"


__all__ = ["Order", "OrderItem", "OrderStatus"]
