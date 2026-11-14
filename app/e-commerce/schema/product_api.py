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
"""Product API schemas: the DB-backed shapes the real routes use.

This file is separate from ``schema/product.py`` on purpose. That file
is the Pydantic lesson (Decimal prices, SKU, dimensions, int ids).
This file is the shop's real product shape: int Toman prices, UUID ids,
and the ``reserved`` counter the payment flow needs. Same idea, two
jobs — do not merge them.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProductCreate(BaseModel):
    """What staff send to create a product. No id, no image yet."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    price_toman: int = Field(ge=0, description="Price in Toman, integer.")
    stock: int = Field(default=0, ge=0)


class ProductRead(BaseModel):
    """What reads return: stored fields plus the public image URL."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    price_toman: int
    stock: int
    reserved: int
    image_path: str | None
    is_active: bool
    created_at: datetime.datetime
    category: str | None = None
    tag: str | None = None

    @computed_field
    @property
    def image_url(self) -> str | None:
        """Public URL of the product photo, served by GET /uploads/{name}."""
        return f"/uploads/{self.image_path}" if self.image_path else None


class ProductPhotoRead(BaseModel):
    """Response returned after uploading a product photo."""

    image_url: str
    filename: str


__all__ = ["ProductCreate", "ProductPhotoRead", "ProductRead"]
