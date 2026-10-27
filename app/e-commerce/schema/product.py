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
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductStatus(str, Enum):
    """Allowed lifecycle states for a product."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class Dimensions(BaseModel):
    """Physical size of a product. A nested model, not flat fields."""

    model_config = ConfigDict(str_strip_whitespace=True)

    width_cm: Decimal = Field(gt=0, description="Width in centimetres.")
    height_cm: Decimal = Field(gt=0, description="Height in centimetres.")
    depth_cm: Decimal = Field(gt=0, description="Depth in centimetres.")


class ProductBase(BaseModel):
    """Fields shared by create, update and read shapes."""

    model_config = ConfigDict(str_strip_whitespace=True)

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
    def normalise_sku(cls, value: object) -> object:
        """Upper-case (and strip) the SKU before pattern validation."""
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def check_discount(self) -> "ProductBase":
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
            "example": {
                "name": "Ceramic mug",
                "price": "12.50",
                "stock": 20,
                "sku": "mug-001",
                "status": "active",
            }
        },
    )


class ProductUpdate(BaseModel):
    """Partial update: every field is optional, so only sent fields change."""

    model_config = ConfigDict(str_strip_whitespace=True)

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


class Product(ProductBase):
    """What reads return: the stored fields plus server-assigned fields."""

    id: int
    created_at: datetime
