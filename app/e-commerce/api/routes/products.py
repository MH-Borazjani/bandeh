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

from pathlib import Path
from typing import Annotated
import uuid

from core.security import RequireStaffDep
from db.session import DbSessionDep
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from schema.product_api import ProductCreate, ProductRead
from services import product_service, search_service
from services.image_tasks import schedule_image_variants
from services.upload_service import UploadError, get_upload_dir, save_upload

router = APIRouter(tags=["products"])

UploadDirDep = Annotated[Path, Depends(get_upload_dir)]


@router.get("/products", response_model=list[ProductRead])
async def list_products_endpoint(
    db: DbSessionDep,
    limit: int = Query(default=search_service.DEFAULT_LIMIT, ge=1, le=search_service.MAX_LIMIT),
) -> JSONResponse:
    """Public: active products, newest first.

    Public cache header: the list is the same for every visitor, so
    browsers and CDNs may cache it for 60 seconds.
    """
    products = await search_service.list_products(db, limit)
    content = [ProductRead.model_validate(p).model_dump(mode="json") for p in products]
    return JSONResponse(content=content, headers={"Cache-Control": "public, max-age=60"})


@router.get("/search", response_model=list[ProductRead])
async def search_products_endpoint(
    db: DbSessionDep,
    q: str = Query(default="", max_length=search_service.MAX_QUERY_LENGTH),
    limit: int = Query(default=search_service.DEFAULT_LIMIT, ge=1, le=search_service.MAX_LIMIT),
) -> list[ProductRead]:
    """Public: search products by name or description.

    Empty ``q`` returns an empty list (never a full-table dump).
    Full-text on Postgres, ILIKE elsewhere — see services/search_service.
    """
    products = await search_service.search_products(db, q, limit)
    return [ProductRead.model_validate(p) for p in products]


@router.post("/products", status_code=201, response_model=ProductRead)
async def create_product_endpoint(
    data: ProductCreate,
    staff: RequireStaffDep,
    db: DbSessionDep,
) -> ProductRead:
    """Create a product (staff/admin). Photo comes later, via /photo."""
    product = await product_service.create_product(db, data)
    return ProductRead.model_validate(product)


@router.get("/products/{product_id}", response_model=ProductRead)
async def read_product(
    product_id: uuid.UUID,
    db: DbSessionDep,
) -> ProductRead:
    """Public: one product, with its image URL when it has a photo."""
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="product not found")
    return ProductRead.model_validate(product)


@router.post("/products/{product_id}/photo", response_model=dict)
async def upload_product_photo(
    product_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks,
    staff: RequireStaffDep,
    db: DbSessionDep,
    upload_dir: UploadDirDep,
) -> dict:
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
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)
    await product_service.set_product_image(db, product, filename)
    background_tasks.add_task(schedule_image_variants, filename, upload_dir)
    return {"image_url": f"/uploads/{filename}", "filename": filename}


@router.get("/uploads/{filename}")
async def serve_upload(filename: str, upload_dir: UploadDirDep) -> FileResponse:
    """Serve one uploaded file. The path check stops ``../`` escapes."""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise HTTPException(status_code=404, detail="file not found")
    upload_root = upload_dir.resolve()
    path = (upload_root / filename).resolve()
    if path.parent != upload_root or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


__all__ = ["router"]
