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
"""Admin API schemas."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AdminOverviewRead(BaseModel):
    """Admin dashboard summary counts."""

    admin: str
    total_users: int
    live_sessions: int


class AdminOrderUpdate(BaseModel):
    """Admin order fulfillment status and tracking code update."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["shipped", "delivered"] | None = None
    tracking_code: str | None = Field(default=None, max_length=100)


class AdminOrderItemRead(BaseModel):
    """Order line item snapshot for admin inspection."""

    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    product_name: str
    quantity: int
    unit_price_toman: int


class AdminOrderRead(BaseModel):
    """Full order detail for admin fulfillment view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    total_toman: int
    total_rial: int
    recipient_name: str | None = None
    recipient_phone: str | None = None
    delivery_address: str | None = None
    tracking_code: str | None = None
    created_at: datetime.datetime
    items: list[AdminOrderItemRead] = Field(default_factory=list)


class AdminOrderListItem(BaseModel):
    """Compact order row for admin orders list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    total_toman: int
    recipient_name: str | None = None
    recipient_phone: str | None = None
    tracking_code: str | None = None
    items_count: int
    created_at: datetime.datetime


__all__ = [
    "AdminOrderItemRead",
    "AdminOrderListItem",
    "AdminOrderRead",
    "AdminOrderUpdate",
    "AdminOverviewRead",
]
