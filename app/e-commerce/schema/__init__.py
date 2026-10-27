"""Pydantic schemas for the e-commerce module.

This package is deliberately limited to data validation (Pydantic).
It contains no routes, no database models and no auth logic:
those belong to later topics, not to the Pydantic topic.

Run from ``app/e-commerce`` as::

    from schema.product import ProductCreate
    from schema.order import OrderCreate
"""

from schema.order import (
    Order,
    OrderCreate,
    OrderCustomer,
    OrderItem,
    OrderStatus,
    OrderUpdateStatus,
)
from schema.product import (
    Dimensions,
    Product,
    ProductCreate,
    ProductStatus,
    ProductUpdate,
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
]
