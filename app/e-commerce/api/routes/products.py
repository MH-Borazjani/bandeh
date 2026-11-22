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
from urllib.parse import quote_plus, urlencode

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


def _resolve_product_img_url(name: str, image_path: str | None) -> str:
    """Resolve an appropriate product image path."""
    if image_path:
        return f"/uploads/{image_path}"
    name_l = name.lower()
    if "fridge" in name_l or "یخچال" in name:
        return "/static/img/product-fridge.jpg"
    if "tv" in name_l or "تلویزیون" in name:
        return "/static/img/product-tv.jpg"
    if "wash" in name_l or "لباسشویی" in name:
        return "/static/img/product-washer.jpg"
    return "/static/img/product-fridge.jpg"


def _wants_html(request: Request) -> bool:
    """True when the caller wants the HTML page, not JSON.

    A browser direct load sends ``Accept: text/html``. HTMX swaps send
    ``Accept: */*`` but mark the request with ``HX-Request`` — and a
    JSON body swapped into ``<main>`` is the [] bug (Nov 2026): the
    search page answered JSON to an HTMX click, so the page showed a
    lone empty array. ``?format=json`` still forces JSON for API users.
    """
    fmt = request.query_params.get("format")
    if fmt == "json":
        return False
    if fmt == "html":
        return True
    if "text/html" in request.headers.get("accept", ""):
        return True
    return request.headers.get("hx-request") == "true"


def _product_dict_from_read(p_read: ProductRead, category: str) -> dict:
    """One search/category result row from a validated product read."""
    return {
        "id": str(p_read.id),
        "name": p_read.name,
        "description": p_read.description,
        "formatted_price": format_toman_price(p_read.price_toman),
        "price_toman": p_read.price_toman,
        "category": p_read.category or category,
        "tag": p_read.tag,
        "stock": p_read.stock,
        "available": max(0, p_read.stock - p_read.reserved),
        "image_path": p_read.image_path,
        "condition": p_read.condition,
        "condition_stars": p_read.condition_stars,
        "specs": dict(p_read.specs or {}),
    }


def _product_dict_from_cms(cp: dict, category: str) -> dict:
    """One search/category result row from a Directus product row."""
    avail = max(0, cp.get("stock", 0) - cp.get("reserved", 0))
    condition = cp.get("condition")
    condition = condition if condition in ("new", "used") else "new"
    stars = cp.get("condition_stars")
    stars = stars if isinstance(stars, int) and 1 <= stars <= 5 else None
    if condition != "used":
        stars = None
    return {
        "id": str(cp.get("id")),
        "name": cp.get("name"),
        "description": cp.get("description"),
        "formatted_price": format_toman_price(cp.get("price_toman")),
        "price_toman": cp.get("price_toman"),
        "category": cp.get("category") or category,
        "tag": cp.get("tag"),
        "stock": cp.get("stock", 0),
        "available": avail,
        "image_path": cp.get("image_path"),
        "condition": condition,
        "condition_stars": stars,
        "specs": cp.get("specs") if isinstance(cp.get("specs"), dict) else {},
    }


# --- Filter panel (Phase 4) -------------------------------------------------
# The category page and /search share one param contract:
#   spec_<key>=<value> (repeatable, one checkbox per distinct value),
#   price_min / price_max (int Toman), condition (new/used),
#   in_stock=1. The helpers below parse those params, match one
#   product dict against them, and build the panel data (definitions +
#   the distinct values actually present among the products). There is
#   NO brand filter: products carry no brand field, so no brand data
#   exists to filter by.

_SPEC_PARAM_PREFIX = "spec_"


def _parse_price_param(value: str | None) -> int | None:
    """A price bound in Toman, or None when absent / not a number."""
    if value is None:
        return None
    raw = value.strip().replace(",", "")
    if not raw or not raw.isdigit():
        return None
    return int(raw)


def _parse_in_stock_param(value: str | None) -> bool:
    """True only for an explicit in-stock ask (the checkbox sends 1)."""
    return isinstance(value, str) and value.strip().lower() in ("1", "true", "yes")


def _parse_spec_filters_from_params(params) -> dict[str, list[str]]:
    """Collect every ``spec_<key>`` param and clean it.

    Keys and values go through ``clean_spec_filters`` (Phase 3), so a
    bad key or an empty value list means "no filter for that
    attribute", never an error.
    """
    raw: dict[str, list[object]] = {}
    for key in params.keys():
        if key.startswith(_SPEC_PARAM_PREFIX):
            raw[key[len(_SPEC_PARAM_PREFIX):]] = params.getlist(key)
    return search_service.clean_spec_filters(raw)


def _parse_filter_params(params) -> dict:
    """All Phase 4 filter params from a request's query params."""
    spec_filters = _parse_spec_filters_from_params(params)
    price_min = _parse_price_param(params.get("price_min"))
    price_max = _parse_price_param(params.get("price_max"))
    condition = search_service.clean_condition(params.get("condition"))
    in_stock = _parse_in_stock_param(params.get("in_stock"))
    has_filters = bool(
        spec_filters
        or price_min is not None
        or price_max is not None
        or condition is not None
        or in_stock
    )
    return {
        "spec_filters": spec_filters,
        "price_min": price_min,
        "price_max": price_max,
        "condition": condition,
        "in_stock": in_stock,
        "has_filters": has_filters,
    }


def _matches_product_filters(
    product: dict,
    spec_filters: dict[str, list[str]],
    price_min: int | None,
    price_max: int | None,
    condition: str | None,
    in_stock: bool,
) -> bool:
    """True when one product dict passes every active filter.

    Specs: the product's value for each picked attribute must be one
    of the picked values (IN per key, AND across keys). A product
    with no specs matches nothing once a spec filter is active.
    Price: inclusive bounds on ``price_toman``. In-stock means
    ``available > 0``. Values compare as strings because Directus
    specs hold numbers (55) while the URL carries text ("55").
    """
    specs = product.get("specs")
    if not isinstance(specs, dict):
        specs = {}
    for key, values in spec_filters.items():
        value = specs.get(key)
        if value is None or str(value) not in values:
            return False
    price = product.get("price_toman")
    if price_min is not None and (
        not isinstance(price, (int, float)) or isinstance(price, bool)
        or price < price_min
    ):
        return False
    if price_max is not None and (
        not isinstance(price, (int, float)) or isinstance(price, bool)
        or price > price_max
    ):
        return False
    if condition is not None and product.get("condition") != condition:
        return False
    if in_stock and (product.get("available") or 0) <= 0:
        return False
    return True


def _apply_product_filters(products: list[dict], filters: dict) -> list[dict]:
    """Keep the products that pass every active filter (no-op if none)."""
    if not filters["has_filters"]:
        return list(products)
    return [
        p
        for p in products
        if _matches_product_filters(
            p,
            filters["spec_filters"],
            filters["price_min"],
            filters["price_max"],
            filters["condition"],
            filters["in_stock"],
        )
    ]


def _sort_spec_values(values: list[str]) -> list[str]:
    """Distinct values, numbers first in numeric order, then text."""

    def _key(value: str) -> tuple[int, object]:
        try:
            return (0, float(value))
        except ValueError:
            return (1, value)

    return sorted(values, key=_key)


def _distinct_spec_values(
    products: list[dict], definitions: list[dict]
) -> list[dict]:
    """One panel group per attribute definition.

    The values are the distinct values actually present among the
    given products — never hardcoded, so a new TV size shows up as a
    checkbox with no code change. A definition with no values among
    these products is left out (nothing to pick).
    """
    groups: list[dict] = []
    for definition in definitions:
        key = definition.get("key")
        if not isinstance(key, str) or not key:
            continue
        seen: dict[str, object] = {}
        for product in products:
            specs = product.get("specs")
            if not isinstance(specs, dict):
                continue
            value = specs.get(key)
            if value is None or value == "":
                continue
            seen.setdefault(str(value), value)
        if not seen:
            continue
        ordered = _sort_spec_values(list(seen.keys()))
        groups.append(
            {
                "key": key,
                "label": definition.get("label") or key,
                "unit": definition.get("unit") or "",
                "options": [seen[text] for text in ordered],
            }
        )
    return groups


async def _category_filter_definitions(
    directus: DirectusClient, cat_id: int | None
) -> list[dict]:
    """The attribute definitions for one category id, or [].

    Read live from Directus via ``get_category_filter_attributes``
    (Phase 3). Any failure — CMS down, client without the method —
    means "no attribute filters", never a 500.
    """
    if cat_id is None:
        return []
    try:
        all_definitions = await directus.get_category_filter_attributes()
    except Exception:
        return []
    if not isinstance(all_definitions, dict):
        return []
    found = all_definitions.get(cat_id)
    if found is None:
        found = all_definitions.get(str(cat_id))
    return list(found) if isinstance(found, list) else []


def _resolve_category_names(
    categories: list[str] | None, categories_list: list[dict]
) -> list[str]:
    """Resolve category filter values (id or name) to display names.

    Each value is resolved on its own: a numeric value that matches a
    known category id becomes that category's name, anything else passes
    through unchanged (unknown values simply match nothing). Empty
    values are dropped, so ``?category=`` behaves like no filter.
    """
    resolved: list[str] = []
    for category in categories or []:
        if not category or not category.strip():
            continue
        raw = category.strip()
        if raw.isdigit():
            for c in categories_list:
                if str(c.get("id")) == raw and isinstance(c.get("name"), str):
                    raw = str(c.get("name"))
                    break
        if raw not in resolved:
            resolved.append(raw)
    return resolved


@router.get("/api/search-live", response_class=HTMLResponse)
async def live_search_endpoint(
    request: Request,
    db: DbSessionDep,
    directus: DirectusClientDep,
    q: Annotated[str, Query(max_length=search_service.MAX_QUERY_LENGTH)] = "",
    category: Annotated[list[str] | None, Query()] = None,
    condition: Annotated[str, Query()] = "",
) -> HTMLResponse:
    """Live search dropdown panel fragment for HTMX.

    The header panel sends the pre-search filters (categories +
    condition) with every keystroke, so the dropdown only shows
    products that match the filters the visitor picked before typing.
    ``category`` is repeatable: ``?category=A&category=B``.
    """
    query = q.strip()
    if not query:
        empty_hint = (
            '<div class="py-4 text-center text-xs theme-text-muted">'
            "نام کالا یا برند را تایپ کنید...</div>"
        )
        return HTMLResponse(empty_hint)

    # The pre-search filters: an unknown condition means "all".
    cond = search_service.clean_condition(condition)
    try:
        categories_list = await directus.list_categories()
    except Exception:
        categories_list = [
            {"id": idx + 1, "name": c["name"], "description": c["desc"]}
            for idx, c in enumerate(FALLBACK_CATEGORIES)
        ]
    cat_names = _resolve_category_names(category, categories_list)
    cat_lower = {name.lower() for name in cat_names}

    matched: list[dict[str, object]] = []
    # A category filter needs the CMS: the SQLAlchemy Product model does
    # not map the category column, so the database cannot tell which
    # category a row belongs to. With a category picked, skip the DB
    # path and read from Directus (which does know the category).
    if not cat_names:
        try:
            db_products = await search_service.search_products(
                db, query, limit=6, condition=cond
            )
            for p in db_products:
                p_read = ProductRead.model_validate(p)
                matched.append(
                    {
                        "id": str(p_read.id),
                        "name": p_read.name,
                        "formatted_price": format_toman_price(p_read.price_toman),
                        "category": p_read.category or "لوازم خانگی",
                        "image_url": _resolve_product_img_url(
                            p_read.name, p_read.image_path
                        ),
                    }
                )
        except Exception:
            pass

    if not matched:
        try:
            cms_products = await directus.list_products(limit=30)
            q_lower = query.lower()
            for cp in cms_products:
                if cond is not None and cp.get("condition") != cond:
                    continue
                if (
                    cat_names
                    and isinstance(cp.get("category"), str)
                    and cp["category"].strip().lower() not in cat_lower
                ):
                    continue
                name_match = q_lower in cp.get("name", "").lower()
                desc_match = q_lower in (cp.get("description") or "").lower()
                if name_match or desc_match:
                    matched.append(
                        {
                            "id": str(cp.get("id")),
                            "name": cp.get("name"),
                            "formatted_price": format_toman_price(
                                cp.get("price_toman")
                            ),
                            "category": cp.get("category") or "لوازم خانگی",
                            "image_url": _resolve_product_img_url(
                                cp.get("name", ""), cp.get("image_path")
                            ),
                        }
                    )
                    if len(matched) >= 6:
                        break
        except Exception:
            pass

    # The hardcoded fallback products have no category at all, so they
    # must never answer a category-filtered search — that would show a
    # fridge inside the TV category, with a broken /products/None link.
    if not matched and not cat_names:
        from integrations.directus_client import FALLBACK_PRODUCTS

        q_lower = query.lower()
        for fp in FALLBACK_PRODUCTS:
            if cond is not None and fp.get("condition") != cond:
                continue
            name_val = str(fp.get("name", ""))
            desc_val = str(fp.get("description", ""))
            if q_lower in name_val.lower() or q_lower in desc_val.lower():
                matched.append(
                    {
                        "id": str(fp.get("id")),
                        "name": name_val,
                        "formatted_price": format_toman_price(
                            fp.get("price_toman")
                        ),
                        "category": str(fp.get("category") or "لوازم خانگی"),
                        "image_url": _resolve_product_img_url(
                            name_val, fp.get("image_path")
                        ),
                    }
                )
                if len(matched) >= 6:
                    break

    # The "see all results" link carries the same pre-search filters,
    # with every picked category as its own repeated param.
    search_params: list[tuple[str, str]] = [("q", query)]
    for cat in cat_names:
        search_params.append(("category", cat))
    if cond is not None:
        search_params.append(("condition", cond))
    return templates.TemplateResponse(
        request=request,
        name="_search_live.html",
        context={
            "q": query,
            "results": matched,
            "search_url": "/search?" + urlencode(search_params),
        },
    )


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
    category: Annotated[list[str] | None, Query()] = None,
    condition: Annotated[str | None, Query()] = None,
    limit: Annotated[
        int, Query(ge=1, le=search_service.MAX_LIMIT)
    ] = search_service.DEFAULT_LIMIT,
) -> list[ProductRead] | HTMLResponse:
    """Public: search products by name or description, with filters.

    Filters combine: a text query, one or more categories (repeatable
    ``category`` param, e.g. ``?category=A&category=B``), and the
    condition (``new`` / ``used`` — only offered when the site sells
    used goods). When requested with Accept: text/html, an HTMX request, or
    format=html, renders the HTML search page; otherwise returns JSON
    list[ProductRead] for existing search API callers.
    """
    if not _wants_html(request):
        cond = search_service.clean_condition(condition)
        filters = _parse_filter_params(request.query_params)
        if q.strip():
            products = await search_service.search_products(
                db, q, limit, condition=cond
            )
        elif cond is not None:
            # Filter only, no text: list instead of search (an empty
            # query searches nothing by design — see check_search).
            products = await search_service.list_products(
                db, limit, condition=cond
            )
        else:
            products = await search_service.search_products(db, q, limit)
        reads = [ProductRead.model_validate(p) for p in products]
        if not filters["has_filters"]:
            return reads
        return [
            product
            for product in reads
            if _matches_product_filters(
                _product_dict_from_read(product, "لوازم خانگی"),
                filters["spec_filters"],
                filters["price_min"],
                filters["price_max"],
                filters["condition"],
                filters["in_stock"],
            )
        ]

    # HTML view: resolve categories and results
    try:
        categories_list = await directus.list_categories()
    except Exception:
        categories_list = [
            {"id": idx + 1, "name": c["name"], "description": c["desc"]}
            for idx, c in enumerate(FALLBACK_CATEGORIES)
        ]

    cond = search_service.clean_condition(condition)
    filters = _parse_filter_params(request.query_params)
    cat_names = _resolve_category_names(category, categories_list)
    cat_lower = {name.lower() for name in cat_names}
    # The DB rows carry no category (the Product model does not map
    # that column), so a DB result is labelled with the first picked
    # category — the same fallback the single-category path used.
    db_fallback_category = cat_names[0] if cat_names else "لوازم خانگی"
    matched_products: list[dict] = []
    has_query = bool(q.strip())
    has_category = bool(cat_names)
    has_condition = cond is not None
    searched = (
        has_query or has_category or has_condition or filters["has_filters"]
    )

    def _matches_filters(p: dict) -> bool:
        """Category (membership in the picked set) + condition.

        An empty picked set means "no category filter". When more than
        one category is picked, only filters shared by all of them
        apply (price, condition, brand); per-category attribute
        filters show only when exactly one category is picked — see
        ``single_category`` in the template context below.
        """
        if cat_names and (p.get("category") or "").strip().lower() not in cat_lower:
            return False
        if cond is not None and p.get("condition") != cond:
            return False
        return True

    if has_query:
        # A category filter needs the CMS: the DB rows carry no
        # category (the Product model does not map that column), so
        # with categories picked the DB path is skipped entirely —
        # otherwise an unknown category would "match" DB rows that
        # were merely labelled with the picked name. Same rule as the
        # live search endpoint.
        if not cat_names:
            db_products = await search_service.search_products(
                db, q, limit, condition=cond
            )
            for p in db_products:
                p_read = ProductRead.model_validate(p)
                matched_products.append(
                    _product_dict_from_read(p_read, db_fallback_category)
                )
        if not matched_products:
            try:
                cms_products = await directus.list_products(limit=50)
                q_lower = q.strip().lower()
                for cp in cms_products:
                    name_match = q_lower in cp.get("name", "").lower()
                    desc_match = q_lower in (cp.get("description") or "").lower()
                    if name_match or desc_match:
                        matched_products.append(
                            _product_dict_from_cms(cp, db_fallback_category)
                        )
            except Exception:
                pass
        matched_products = [p for p in matched_products if _matches_filters(p)]
    elif has_category or has_condition or filters["has_filters"]:
        try:
            if len(cat_names) == 1:
                cms_products = await directus.list_products_by_category(
                    cat_names[0], limit=limit
                )
            else:
                # Several categories (or none, condition-only): fetch
                # the list and keep the rows whose category is in the
                # picked set — Directus has no multi-category filter.
                cms_products = await directus.list_products(limit=limit)
            for cp in cms_products:
                matched_products.append(
                    _product_dict_from_cms(cp, db_fallback_category)
                )
        except Exception:
            pass

        # No CMS rows: fall back to the DB only when no category was
        # picked. DB rows carry no category, so they must never answer
        # a category-filtered search — an unknown category matches
        # nothing instead of "matching" every DB row.
        if not matched_products and not cat_names:
            try:
                db_all = await search_service.list_products(
                    db, limit=limit, condition=cond
                )
            except Exception:
                # Both sources failed: show an empty result, not a 500.
                db_all = []
            for p in db_all:
                p_read = ProductRead.model_validate(p)
                matched_products.append(
                    _product_dict_from_read(p_read, db_fallback_category)
                )
        matched_products = [p for p in matched_products if _matches_filters(p)]

    # Phase 4 filter panel. Attribute filters show only when exactly
    # one category is picked (the shared-vs-attribute rule): the
    # definitions come from that category, and the checkbox values are
    # the distinct values among ALL of the category's products — not
    # just the ones matching the text query — so typing a query never
    # shrinks the checkboxes. With several categories, or none, only
    # the shared filters (price, condition, in-stock) apply.
    filter_definitions: list[dict] = []
    if len(cat_names) == 1:
        single_cat_id: int | None = None
        for c in categories_list:
            if isinstance(c.get("name"), str) and c["name"] == cat_names[0]:
                cid = c.get("id")
                if isinstance(cid, int) and not isinstance(cid, bool):
                    single_cat_id = cid
                elif isinstance(cid, str) and cid.isdigit():
                    single_cat_id = int(cid)
                break
        definitions = await _category_filter_definitions(
            directus, single_cat_id
        )
        value_source: list[dict] = []
        try:
            cat_rows = await directus.list_products_by_category(
                cat_names[0], limit=limit
            )
            value_source = [
                _product_dict_from_cms(cp, db_fallback_category)
                for cp in cat_rows
            ]
        except Exception:
            value_source = []
        if not value_source:
            value_source = matched_products
        filter_definitions = _distinct_spec_values(value_source, definitions)

    matched_products = _apply_product_filters(matched_products, filters)
    total_count_formatted = str(len(matched_products)).translate(_FA_DIGITS)

    # The panel form is separate from the search form, so it carries
    # the query and the picked categories (and the condition, which on
    # /search lives in the chip row above) as hidden inputs — changing
    # a filter never drops them.
    filter_extra_hidden: list[tuple[str, str]] = []
    if q:
        filter_extra_hidden.append(("q", q))
    for c in cat_names:
        filter_extra_hidden.append(("category", c))
    if cond is not None:
        filter_extra_hidden.append(("condition", cond))
    filter_clear_url = "/search" + (("?q=" + quote_plus(q)) if q else "")

    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "phone": phone,
            "q": q,
            "selected_categories": cat_names,
            "single_category": len(cat_names) == 1,
            "selected_condition": cond or "",
            "categories": categories_list,
            "products": matched_products,
            "searched": searched,
            "total_count_formatted": total_count_formatted,
            "filter_definitions": filter_definitions,
            "selected_specs": filters["spec_filters"],
            "price_min": (
                str(filters["price_min"])
                if filters["price_min"] is not None
                else ""
            ),
            "price_max": (
                str(filters["price_max"])
                if filters["price_max"] is not None
                else ""
            ),
            "in_stock": filters["in_stock"],
            "has_filters": filters["has_filters"],
            "filter_clear_url": filter_clear_url,
            "filter_extra_hidden": filter_extra_hidden,
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
    """Public: category detail page showing products from Directus.

    Phase 4 filters (``spec_<key>``, ``price_min`` / ``price_max``,
    ``condition``, ``in_stock``) narrow the list; the count reflects
    the filtered list. The panel state comes from the URL params, so
    an HTMX swap re-renders it identically to a full load.
    """
    filters = _parse_filter_params(request.query_params)
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
        try:
            db_all = await search_service.list_products(db, limit=50)
        except Exception as exc:
            # Both sources failed: show the empty state, not a 500.
            logger.warning("Category DB fallback failed: %s", exc)
            db_all = []
        # DB fallback, Postgres: narrow by specs in the database
        # itself (filter_products_by_specs, Phase 3) before the
        # name/category match below; price and in-stock are applied
        # with the other filters afterwards, on the same dicts as the
        # Directus path. On SQLite the spec filter cannot run (see
        # docs/product-attributes.md), so the Python matcher does it.
        if filters["spec_filters"] and db_all:
            dialect = getattr(getattr(db, "bind", None), "dialect", None)
            if dialect is not None and dialect.name == "postgresql":
                try:
                    db_all = await search_service.filter_products_by_specs(
                        db,
                        filters["spec_filters"],
                        limit=50,
                        condition=filters["condition"],
                    )
                except Exception as exc:
                    logger.warning(
                        "Category DB spec filter failed, using list: %s", exc
                    )
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
        condition = p.get("condition")
        condition = condition if condition in ("new", "used") else "new"
        stars = p.get("condition_stars")
        stars = stars if isinstance(stars, int) and 1 <= stars <= 5 else None
        if condition != "used":
            stars = None
        formatted_products.append(
            {
                "id": str(pid),
                "name": pname,
                "description": pdesc,
                "formatted_price": format_toman_price(price),
                "price_toman": price,
                "available": avail,
                "stock": stock,
                "tag": tag,
                "image_path": p.get("image_path"),
                "condition": condition,
                "condition_stars": stars,
                "specs": p.get("specs") if isinstance(p.get("specs"), dict) else {},
            }
        )

    # Attribute definitions for this category, with the distinct
    # values actually present among its (unfiltered) products.
    definitions = await _category_filter_definitions(directus, cat_id)
    filter_definitions = _distinct_spec_values(formatted_products, definitions)
    filtered_products = _apply_product_filters(formatted_products, filters)
    total_count_formatted = str(len(filtered_products)).translate(_FA_DIGITS)

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
            "products": filtered_products,
            "total_count_formatted": total_count_formatted,
            "filter_definitions": filter_definitions,
            "selected_specs": filters["spec_filters"],
            "selected_condition": filters["condition"] or "",
            "price_min": (
                str(filters["price_min"])
                if filters["price_min"] is not None
                else ""
            ),
            "price_max": (
                str(filters["price_max"])
                if filters["price_max"] is not None
                else ""
            ),
            "in_stock": filters["in_stock"],
            "has_filters": filters["has_filters"],
            # The bare category URL (this request's path, no query):
            # clear-all goes here and resets the panel.
            "filter_clear_url": str(request.url.path),
            "filter_extra_hidden": [],
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
    if _wants_html(request):
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
