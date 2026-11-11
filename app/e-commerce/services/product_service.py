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
"""Product service: create, read, attach a photo. Thin, no HTTP here."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from db.models.product import Product
from schema.product_api import ProductCreate


async def create_product(db: AsyncSession, data: ProductCreate) -> Product:
    """Insert one product. Money is int Toman; stock starts unreserved."""
    product = Product(
        name=data.name,
        description=data.description,
        price_toman=data.price_toman,
        stock=data.stock,
    )
    db.add(product)
    await db.flush()
    return product


async def get_product(db: AsyncSession, product_id: uuid.UUID) -> Product | None:
    """Find a product by id, or None."""
    return await db.get(Product, product_id)


async def set_product_image(db: AsyncSession, product: Product, filename: str) -> Product:
    """Attach a server-made upload filename to a product."""
    product.image_path = filename
    await db.flush()
    return product


__all__ = ["create_product", "get_product", "set_product_image"]
