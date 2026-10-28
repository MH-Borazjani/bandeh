"""Order schemas: nested models and computed fields.

Concepts shown here, and where to find them:
- Nested models: an ``Order`` contains a customer and a list of items.
- A list with a minimum size: ``items`` must contain at least one item.
- ``EmailStr``: validated email, no password or auth field anywhere here.
  Auth (OAuth2 / JWT / ``/token``) is a later topic and is NOT in this file.
- ``computed_field``: line totals and the order total are derived,
  never sent by the client, so they cannot disagree with the inputs.
- A small, single-purpose update model: ``OrderUpdateStatus`` only
  changes the status, nothing else.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    computed_field,
)


class OrderStatus(StrEnum):
    """Allowed lifecycle states for an order."""

    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class OrderCustomer(BaseModel):
    """Buyer contact details for one order. No account, no password."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    address: str = Field(min_length=5, max_length=300)
    city: str = Field(min_length=2, max_length=80)


class OrderItem(BaseModel):
    """One line of an order, with the product data frozen at order time."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    product_id: int = Field(gt=0)
    product_name: str = Field(min_length=2, max_length=120)
    unit_price: Decimal = Field(gt=0, decimal_places=2)
    quantity: int = Field(ge=1)

    @computed_field
    @property
    def line_total(self) -> Decimal:
        """unit_price * quantity, computed by the model itself."""
        return self.unit_price * self.quantity


class OrderCreate(BaseModel):
    """What a client sends to place an order."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "customer": {
                        "full_name": "Sara Ahmadi",
                        "email": "sara@example.com",
                        "address": "12 Main Street",
                        "city": "Tehran",
                    },
                    "items": [
                        {
                            "product_id": 1,
                            "product_name": "Ceramic mug",
                            "unit_price": "12.50",
                            "quantity": 2,
                        }
                    ],
                }
            ]
        },
    )

    customer: OrderCustomer
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = Field(default=None, max_length=500)


class OrderUpdateStatus(BaseModel):
    """Change only the status of an existing order."""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: OrderStatus


class Order(BaseModel):
    """What reads return: the placed order plus server-assigned fields."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    id: int
    customer: OrderCustomer
    items: list[OrderItem] = Field(min_length=1)
    notes: str | None = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime

    @computed_field
    @property
    def total_amount(self) -> Decimal:
        """Sum of all line totals, computed by the model itself."""
        return sum((item.line_total for item in self.items), Decimal(0))
