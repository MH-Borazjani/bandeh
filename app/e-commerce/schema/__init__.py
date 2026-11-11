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
"""Pydantic schemas for the e-commerce module.

This package is deliberately limited to data validation (Pydantic).
It contains no routes, no database models and no auth logic:
those belong to later topics, not to the Pydantic topic.

Run from ``app/e-commerce`` as::

    from schema.product import ProductCreate
    from schema.order import OrderCreate
"""

from .order import (
    Order,
    OrderCreate,
    OrderCustomer,
    OrderItem,
    OrderStatus,
    OrderUpdateStatus,
)
from .product import (
    Dimensions,
    Product,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
)
from .user import (
    SessionRead,
    UserBase,
    UserProfileUpdate,
    UserRead,
)

__all__ = [
    "Dimensions",
    "Order",
    "OrderCreate",
    "OrderCustomer",
    "OrderItem",
    "OrderStatus",
    "OrderUpdateStatus",
    "Product",
    "ProductCreate",
    "ProductStatus",
    "ProductUpdate",
    "SessionRead",
    "UserBase",
    "UserProfileUpdate",
    "UserRead",
]
