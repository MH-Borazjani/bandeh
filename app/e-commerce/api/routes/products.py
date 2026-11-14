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
    _FA_DIGITS,
    FALLBACK_CATEGORIES,
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


@router.get("/search", response_model=None)
async def search_products_endpoint(
    request: Request,
    db: DbSessionDep,
    directus: DirectusClientDep,
    phone: CurrentUserDep,
    q: Annotated[str, Query(max_length=search_service.MAX_QUERY_LENGTH)] = "",
    category: Annotated[str | None, Query()] = None,
    limit: Annotated[
        int, Query(ge=1, le=search_service.MAX_LIMIT)
    ] = search_service.DEFAULT_LIMIT,
) -> list[ProductRead] | HTMLResponse:
    """Public: search products by name or description.

    When requested with Accept: text/html (or format=html), renders the HTML
    search and category listing page. Otherwise returns JSON list[ProductRead]
    for existing search API callers.
    """
    accept = request.headers.get("accept", "")
    fmt = request.query_params.get("format")
    is_html = ("text/html" in accept or fmt == "html") and fmt != "json"

    if not is_html:
        products = await search_service.search_products(db, q, limit)
        return [ProductRead.model_validate(p) for p in products]

    # HTML view: resolve categories and results
    try:
        categories_list = await directus.list_categories()
    except Exception:
        categories_list = [
            {"id": idx + 1, "name": c["name"], "description": c["desc"]}
            for idx, c in enumerate(FALLBACK_CATEGORIES)
        ]

    matched_products: list[dict] = []
    has_query = bool(q.strip())
    has_category = bool(category and category.strip())
    searched = has_query or has_category

    if has_query:
        db_products = await search_service.search_products(db, q, limit)
        for p in db_products:
            p_read = ProductRead.model_validate(p)
            matched_products.append(
                {
                    "id": str(p_read.id),
                    "name": p_read.name,
                    "description": p_read.description,
                    "formatted_price": format_toman_price(p_read.price_toman),
                    "category": p_read.category or "لوازم خانگی",
                    "tag": p_read.tag,
                    "stock": p_read.stock,
                    "available": max(0, p_read.stock - p_read.reserved),
                    "image_path": p_read.image_path,
                }
            )
        if not matched_products:
            try:
                cms_products = await directus.list_products(limit=50)
                q_lower = q.strip().lower()
                for cp in cms_products:
                    name_match = q_lower in cp.get("name", "").lower()
                    desc_match = q_lower in (cp.get("description") or "").lower()
                    if name_match or desc_match:
                        avail = max(0, cp.get("stock", 0) - cp.get("reserved", 0))
                        matched_products.append(
                            {
                                "id": str(cp.get("id")),
                                "name": cp.get("name"),
                                "description": cp.get("description"),
                                "formatted_price": format_toman_price(
                                    cp.get("price_toman")
                                ),
                                "category": cp.get("category") or "لوازم خانگی",
                                "tag": cp.get("tag"),
                                "stock": cp.get("stock", 0),
                                "available": avail,
                                "image_path": cp.get("image_path"),
                            }
                        )
            except Exception:
                pass

        if has_category and category:
            cat_filter = category.strip().lower()
            matched_products = [
                p
                for p in matched_products
                if cat_filter in (p.get("category") or "").lower()
            ]
    elif has_category and category:
        try:
            cms_cat_products = await directus.list_products_by_category(
                category, limit=limit
            )
            for cp in cms_cat_products:
                avail = max(0, cp.get("stock", 0) - cp.get("reserved", 0))
                matched_products.append(
                    {
                        "id": str(cp.get("id")),
                        "name": cp.get("name"),
                        "description": cp.get("description"),
                        "formatted_price": format_toman_price(
                            cp.get("price_toman")
                        ),
                        "category": cp.get("category") or category,
                        "tag": cp.get("tag"),
                        "stock": cp.get("stock", 0),
                        "available": avail,
                        "image_path": cp.get("image_path"),
                    }
                )
        except Exception:
            pass

        if not matched_products:
            db_all = await search_service.list_products(db, limit=limit)
            for p in db_all:
                p_read = ProductRead.model_validate(p)
                if category.lower() in (p_read.category or "").lower():
                    matched_products.append(
                        {
                            "id": str(p_read.id),
                            "name": p_read.name,
                            "description": p_read.description,
                            "formatted_price": format_toman_price(p_read.price_toman),
                            "category": p_read.category or category,
                            "tag": p_read.tag,
                            "stock": p_read.stock,
                            "available": max(0, p_read.stock - p_read.reserved),
                            "image_path": p_read.image_path,
                        }
                    )

    total_count_formatted = str(len(matched_products)).translate(_FA_DIGITS)

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "phone": phone,
            "q": q,
            "selected_category": category or "",
            "categories": categories_list,
            "products": matched_products,
            "searched": searched,
            "total_count_formatted": total_count_formatted,
        },
    )


@router.get("/category/{category_identifier}", response_class=HTMLResponse)
@router.get("/categories/{category_identifier}", response_class=HTMLResponse)
async def category_page_endpoint(
    request: Request,
    category_identifier: str,
    db: DbSessionDep,
    directus: DirectusClientDep,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Public: category detail page showing products from Directus."""
    category_map = await directus.get_category_map()
    cat_name = category_identifier
    cat_desc = ""
    cat_id: int | None = None

    if category_identifier.isdigit():
        cat_id = int(category_identifier)
        cat_name = (
            category_map.get(cat_id)
            or category_map.get(str(cat_id))
            or f"دسته‌بندی {cat_id}"
        )
    else:
        for cid, cname in category_map.items():
            if cname.lower() == category_identifier.lower():
                if isinstance(cid, int) and not isinstance(cid, bool):
                    cat_id = cid
                elif isinstance(cid, str) and cid.isdigit():
                    cat_id = int(cid)
                cat_name = cname
                break

    for fc in FALLBACK_CATEGORIES:
        if fc["name"] == cat_name or (cat_id and str(cat_id) in fc["name"]):
            cat_name = fc["name"]
            cat_desc = fc["desc"]
            break

    try:
        all_cats = await directus.list_categories()
        for c in all_cats:
            c_id = c.get("id")
            c_name = c.get("name")
            if (cat_id is not None and str(c_id) == str(cat_id)) or (
                c_name and str(c_name).lower() == cat_name.lower()
            ):
                cat_name = str(c_name)
                raw_desc = c.get("description")
                if raw_desc:
                    cat_desc = str(raw_desc)
                break
    except Exception:
        pass

    cat_products_raw = []
    try:
        cat_products_raw = await directus.list_products_by_category(
            cat_id if cat_id is not None else cat_name, limit=50
        )
    except Exception as exc:
        logger.warning("Failed to list products by category from Directus: %s", exc)

    if not cat_products_raw:
        db_all = await search_service.list_products(db, limit=50)
        for p in db_all:
            p_read = ProductRead.model_validate(p)
            if cat_name.lower() in (p_read.name or "").lower() or (
                p_read.category and cat_name.lower() in p_read.category.lower()
            ):
                cat_products_raw.append(p_read.model_dump())

    formatted_products = []
    for p in cat_products_raw:
        pid = p.get("id")
        pname = p.get("name")
        pdesc = p.get("description")
        price = p.get("price_toman")
        stock = p.get("stock", 0)
        reserved = p.get("reserved", 0)
        tag = p.get("tag")
        avail = max(0, stock - reserved)
        formatted_products.append(
            {
                "id": str(pid),
                "name": pname,
                "description": pdesc,
                "formatted_price": format_toman_price(price),
                "available": avail,
                "stock": stock,
                "tag": tag,
                "image_path": p.get("image_path"),
            }
        )

    total_count_formatted = str(len(formatted_products)).translate(_FA_DIGITS)

    return templates.TemplateResponse(
        request=request,
        name="category.html",
        context={
            "phone": phone,
            "category": {
                "id": cat_id,
                "name": cat_name,
                "description": cat_desc,
            },
            "products": formatted_products,
            "total_count_formatted": total_count_formatted,
        },
    )


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
