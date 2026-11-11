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
"""Product schemas: the Pydantic topic applied to e-commerce products.

Concepts shown here, and where to find them:
- ``BaseModel`` + ``Field`` constraints: every field below.
- Optional fields vs required fields: ``discount_price``, ``tags``.
- Nested model: ``Dimensions`` inside ``ProductBase``.
- Enum field: ``ProductStatus``.
- A ``field_validator`` that normalises input: ``SKU`` is upper-cased
  before the pattern check runs (``mode="before"``).
- A ``model_validator`` that checks two fields together:
  ``discount_price`` must be smaller than ``price``.
- Create / Update / full-model split: clients send ``ProductCreate``,
  partial edits send ``ProductUpdate``, and reads return ``Product``.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductStatus(StrEnum):
    """Allowed lifecycle states for a product."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Dimensions(BaseModel):
    """Physical size of a product. A nested model, not flat fields."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    width_cm: Decimal = Field(gt=0, description="Width in centimetres.")
    height_cm: Decimal = Field(gt=0, description="Height in centimetres.")
    depth_cm: Decimal = Field(gt=0, description="Depth in centimetres.")


class ProductBase(BaseModel):
    """Fields shared by create, update and read shapes."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(gt=0, decimal_places=2)
    discount_price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    stock: int = Field(default=0, ge=0)
    sku: str = Field(pattern=r"^[A-Z0-9-]+$", max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=10)
    status: ProductStatus = ProductStatus.DRAFT
    dimensions: Dimensions | None = None

    @field_validator("sku", mode="before")
    @classmethod
    def normalise_sku(cls, value: Any) -> Any:
        """Upper-case (and strip) the SKU before pattern validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def check_discount(self) -> Self:
        """A discount only makes sense when it is below the full price."""
        if (
            self.discount_price is not None
            and self.discount_price >= self.price
        ):
            raise ValueError("discount_price must be smaller than price")
        return self


class ProductCreate(ProductBase):
    """What a client sends to create a product. No id, no timestamps."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "examples": [
                {
                    "name": "Ceramic mug",
                    "price": "12.50",
                    "stock": 20,
                    "sku": "mug-001",
                    "status": "active",
                }
            ]
        },
    )


class ProductUpdate(BaseModel):
    """Partial update: every field is optional, so only sent fields change."""

    model_config = ConfigDict(str_strip_whitespace=True, from_attributes=True)

    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, decimal_places=2)
    discount_price: Decimal | None = Field(
        default=None, gt=0, decimal_places=2
    )
    stock: int | None = Field(default=None, ge=0)
    tags: list[str] | None = Field(default=None, max_length=10)
    status: ProductStatus | None = None
    dimensions: Dimensions | None = None

    @model_validator(mode="after")
    def check_discount(self) -> Self:
        """If both price and discount_price are provided, discount must be smaller than price."""
        if (
            self.price is not None
            and self.discount_price is not None
            and self.discount_price >= self.price
        ):
            raise ValueError("discount_price must be smaller than price")
        return self


class Product(ProductBase):
    """What reads return: the stored fields plus server-assigned fields."""

    id: int
    created_at: datetime
