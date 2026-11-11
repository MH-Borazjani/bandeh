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
"""Product model: what the shop sells.

Money is int Toman (never float, never Decimal in the DB — Toman has no
fractions). Stock is two numbers, not one: ``stock`` is what the shop
owns, ``reserved`` is how many of those are held for unpaid orders
(15-minute holds, payment topic). What a customer can buy is
``stock - reserved``. The CHECK constraints keep the numbers honest at
the database level, not just in Python.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """One sellable item (home appliance)."""

    __tablename__ = "products"

    __table_args__ = (
        CheckConstraint("price_toman >= 0", name="ck_products_price_toman_nonnegative"),
        CheckConstraint("stock >= 0", name="ck_products_stock_nonnegative"),
        CheckConstraint("reserved >= 0", name="ck_products_reserved_nonnegative"),
        CheckConstraint("reserved <= stock", name="ck_products_reserved_le_stock"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # Price in Toman, integer. Rial only exists at the gateway (×10).
    price_toman: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    stock: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    # Units held for unpaid orders. available = stock - reserved.
    reserved: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    # Server-made filename under uploads/ (never the client's filename).
    image_path: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    @property
    def available(self) -> int:
        """Units a customer can actually buy right now."""
        return self.stock - self.reserved

    def __repr__(self) -> str:
        return f"<Product name={self.name!r} price_toman={self.price_toman}>"


__all__ = ["Product"]
