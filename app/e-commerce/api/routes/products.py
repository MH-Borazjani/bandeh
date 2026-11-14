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
"""Product routes: create, read, upload a photo, serve uploads.

- Creating products and uploading photos is staff work: the gate is
  ``RequireStaffDep`` (staff + admin), never an if in the route.
- Reading one product and fetching its photo is public.
- Photos are served from ``uploads/`` through this route, never through
  the static file server: the path is checked so ``../`` cannot escape
  the upload directory.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi import (
    Path as FastAPIPath,
)
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUserDep, RequireStaffDep
from core.templates import templates
from db.session import DbSessionDep
from integrations.directus_client import (
    DirectusClient,
    DirectusClientDep,
    format_toman_price,
)
from schema.product_api import ProductCreate, ProductPhotoRead, ProductRead
from services import product_service, search_service
from services.image_tasks import schedule_image_variants
from services.upload_service import UploadError, get_upload_dir, save_upload

router = APIRouter(tags=["products"])

logger = logging.getLogger("bandeh.products")

UploadDirDep = Annotated[Path, Depends(get_upload_dir)]


@router.get("/products")
async def list_products_endpoint(
    db: DbSessionDep,
    response: Response,
    directus: DirectusClientDep,
    limit: Annotated[
        int, Query(ge=1, le=search_service.MAX_LIMIT)
    ] = search_service.DEFAULT_LIMIT,
) -> list[ProductRead]:
    """Public: active products, newest first.

    Content comes from Directus (the source of truth). If Directus is
    unreachable, the same table is read through SQLAlchemy as a fallback
    — the same rows, not a second source of truth — so the shop never
    goes blank because the CMS is down.

    Public cache header: the list is the same for every visitor, so
    browsers and CDNs may cache it for 60 seconds.
    """
    try:
        rows = await directus.list_products(limit)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "Directus product list failed, falling back to database: %s", exc
        )
        products = await search_service.list_products(db, limit)
        response.headers["Cache-Control"] = "public, max-age=60"
        return [ProductRead.model_validate(p) for p in products]
    response.headers["Cache-Control"] = "public, max-age=60"
    return [ProductRead.model_validate(row) for row in rows]


@router.get("/search")
async def search_products_endpoint(
    db: DbSessionDep,
    q: Annotated[str, Query(max_length=search_service.MAX_QUERY_LENGTH)] = "",
    limit: Annotated[
        int, Query(ge=1, le=search_service.MAX_LIMIT)
    ] = search_service.DEFAULT_LIMIT,
) -> list[ProductRead]:
    """Public: search products by name or description.

    Empty ``q`` returns an empty list (never a full-table dump).
    Full-text on Postgres, ILIKE elsewhere — see services/search_service.
    """
    products = await search_service.search_products(db, q, limit)
    return [ProductRead.model_validate(p) for p in products]


@router.post("/products", status_code=201)
async def create_product_endpoint(
    data: ProductCreate,
    staff: RequireStaffDep,
    db: DbSessionDep,
) -> ProductRead:
    """Create a product (staff/admin). Photo comes later, via /photo."""
    product = await product_service.create_product(db, data)
    return ProductRead.model_validate(product)


async def _fetch_product_read(
    db: AsyncSession,
    directus: DirectusClient,
    product_id: uuid.UUID,
) -> ProductRead:
    """Read one product from Directus with database fallback."""
    try:
        row = await directus.get_product(product_id)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "Directus product read failed, falling back to database: %s", exc
        )
        product = await product_service.get_product(db, product_id)
        if product is None:
            raise HTTPException(
                status_code=404, detail="product not found"
            ) from exc
        return ProductRead.model_validate(product)
    if row is None:
        # Directus has no such product. In production the database is
        # the same table, so this is normally the same answer — but check
        # it anyway, so a product created through the API stays readable
        # even if Directus is a step behind.
        product = await product_service.get_product(db, product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        return ProductRead.model_validate(product)
    return ProductRead.model_validate(row)


def _render_product_page(
    request: Request,
    product: ProductRead,
    phone: str | None,
) -> HTMLResponse:
    """Render the HTML product detail page."""
    available = max(0, product.stock - product.reserved)
    formatted_price = format_toman_price(product.price_toman)
    return templates.TemplateResponse(
        request=request,
        name="product.html",
        context={
            "phone": phone,
            "product": product,
            "available": available,
            "formatted_price": formatted_price,
            "category": product.category or "لوازم خانگی",
            "tag": product.tag or "ضمانت اصالت",
        },
    )


@router.get("/products/{product_id}", response_model=ProductRead)
async def read_product(
    request: Request,
    product_id: Annotated[uuid.UUID, FastAPIPath()],
    db: DbSessionDep,
    directus: DirectusClientDep,
    phone: CurrentUserDep,
) -> ProductRead | HTMLResponse:
    """Public: one product, with its image URL when it has a photo.

    When requested with Accept: text/html (e.g. browser navigation) or format=html,
    renders the HTML product detail page. Otherwise returns JSON ProductRead unchanged.
    """
    product = await _fetch_product_read(db, directus, product_id)
    accept = request.headers.get("accept", "")
    if "text/html" in accept or request.query_params.get("format") == "html":
        return _render_product_page(request, product, phone)
    return product


@router.get("/product/{product_id}", response_class=HTMLResponse)
async def product_page_endpoint(
    request: Request,
    product_id: Annotated[uuid.UUID, FastAPIPath()],
    db: DbSessionDep,
    directus: DirectusClientDep,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Public: HTML product detail page for one product."""
    product = await _fetch_product_read(db, directus, product_id)
    return _render_product_page(request, product, phone)


@router.post("/products/{product_id}/photo")
async def upload_product_photo(
    product_id: Annotated[uuid.UUID, FastAPIPath()],
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks,
    staff: RequireStaffDep,
    db: DbSessionDep,
    upload_dir: UploadDirDep,
) -> ProductPhotoRead:
    """Upload a product photo (staff/admin). Returns the public image URL.

    Validation (type, content, size) lives in the upload service. The
    slow resize work is scheduled as a background task — the request
    returns at once and never waits for it.
    """
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    try:
        filename = await save_upload(file, upload_dir)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    await product_service.set_product_image(db, product, filename)
    background_tasks.add_task(schedule_image_variants, filename, upload_dir)
    return ProductPhotoRead(image_url=f"/uploads/{filename}", filename=filename)


@router.get("/uploads/{filename}")
def serve_upload(
    filename: Annotated[str, FastAPIPath()],
    upload_dir: UploadDirDep,
) -> FileResponse:
    """Serve one uploaded file. The path check stops ``../`` escapes."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="file not found")
    upload_root = upload_dir.resolve()
    path = (upload_root / filename).resolve()
    if path.parent != upload_root or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


__all__ = ["router"]
