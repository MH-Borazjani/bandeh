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
"""Product stock: the only product numbers SQLAlchemy still owns.

Directus owns ALL product content — name, description, price, photo,
condition, attributes. This table holds only the two numbers that
need a real database transaction: ``stock`` is what the shop owns,
``reserved`` is how many of those are held for unpaid orders
(15-minute holds, payment topic). What a customer can buy is
``stock - reserved``.

Why a separate table, keyed by the Directus product id? When two
people buy the last fridge at the same time, the database must lock
the row (SELECT ... FOR UPDATE) so only one of them gets it. The
Directus API has no lock, so stock cannot live in Directus. The key
is the Directus product UUID as plain text: the shop never creates
products anymore, it only tracks stock for products Directus already
has. A product with no row here has zero stock — it cannot be sold
until staff sets its stock (see services/stock_service.set_stock).

The CHECK constraints keep the numbers honest at the database level,
not just in Python — the same rules the old products table had.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class ProductStock(Base, TimestampMixin):
    """Stock for one Directus product, by its Directus id."""

    __tablename__ = "product_stock"

    __table_args__ = (
        CheckConstraint("stock >= 0", name="ck_product_stock_stock_nonnegative"),
        CheckConstraint(
            "reserved >= 0", name="ck_product_stock_reserved_nonnegative"
        ),
        CheckConstraint(
            "reserved <= stock", name="ck_product_stock_reserved_le_stock"
        ),
    )

    # The Directus product UUID, as text (e.g. "ad7d6a7f-..."). The
    # shop never invents product ids — it only stocks products that
    # already exist in Directus.
    directus_product_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
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

    @property
    def available(self) -> int:
        """Units a customer can actually buy right now."""
        return self.stock - self.reserved

    def __repr__(self) -> str:
        return (
            f"<ProductStock product={self.directus_product_id!r} "
            f"stock={self.stock} reserved={self.reserved}>"
        )


__all__ = ["ProductStock"]
