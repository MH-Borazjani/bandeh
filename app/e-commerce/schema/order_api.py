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
"""Order API schemas: the DB-backed shapes the real routes use.

Separate from ``schema/order.py`` on purpose: that file is the
teaching schema for the REST lesson. This file is the shop's real
order shape — frozen totals in int Toman/Rial, UUID ids, and the
frozen item lines (name + unit price copied at checkout).
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class OrderItemCreate(BaseModel):
    """One cart line: which product, how many."""

    model_config = ConfigDict(str_strip_whitespace=True)

    product_id: uuid.UUID
    quantity: int = Field(ge=1)


class OrderCreate(BaseModel):
    """What the checkout sends: the cart lines. At least one."""

    items: list[OrderItemCreate] = Field(min_length=1)


class OrderItemRead(BaseModel):
    """One frozen order line."""

    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    product_name: str
    quantity: int
    unit_price_toman: int


class OrderRead(BaseModel):
    """What reads return: the frozen order with its lines."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    total_toman: int
    total_rial: int
    items: list[OrderItemRead]
    created_at: datetime.datetime


__all__ = ["OrderCreate", "OrderItemCreate", "OrderItemRead", "OrderRead"]
