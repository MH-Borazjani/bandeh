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
"""Directus client for shop content (categories, products).

Directus is the headless CMS that holds the shop's content, so editors
can change categories and products from a dashboard instead of code.
Directus OWNS this content: the app reads product and category content
through this client, never through SQLAlchemy models. The one exception
is stock: order creation and payment reserve and release stock inside
real database transactions, so stock/reserved stay in SQLAlchemy — the
same ``products`` table Directus manages, so the dashboard and the app
always see the same rows.
This client reads that content — and if Directus is down, slow, or
returns something broken, it falls back to the hardcoded content below,
so the landing page NEVER goes blank because the CMS is unreachable.

The fallback content is the same data the landing page used before
Directus existed. It lives here (not in ``pages.py``) so the client owns
both the live path and the fallback path.

Caching: successful reads are cached for 60 seconds (``LANDING_CONTENT_TTL``).
When content changes in Directus, call ``invalidate_landing_content``
(delete-on-change) instead of waiting for the TTL — a stale price is
worse than a slow page. Failures are never cached: the next request
retries Directus right away.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import uuid
from typing import Annotated, TypedDict, cast

import httpx
from fastapi import Depends

from core.cache import Cache, CacheDep
from integrations.http_client import HttpClientDep

logger = logging.getLogger("bandeh.directus")

# Cache key and lifetime for the landing content (seconds).
LANDING_CONTENT_KEY = "bandeh:landing:content"
LANDING_CONTENT_TTL = 60
CATEGORIES_MAP_KEY = "bandeh:categories:map"
CATEGORIES_MAP_TTL = 300


class CategoryContent(TypedDict):
    """Rendered category card data on the landing page."""

    name: str
    desc: str


class ProductContent(TypedDict):
    """Rendered product card data on the landing page."""

    name: str
    price: str
    # Raw Toman integer, for the compact price display (۳۲ میلیون).
    # None when Directus did not send a price.
    price_toman: int | None
    # Directus product id (UUID string). None for rows without one, so the
    # landing page falls back to a plain name (no product link).
    id: str | None
    tag: str | None
    # Category name, for the per-category item count on the landing page.
    # Photo file name, for the product thumbnail. Both None when Directus
    # did not send them (for example in the hardcoded fallback).
    category: str | None
    image_path: str | None


class LandingContent(TypedDict):
    """Combined landing page categories and products payload."""

    categories: list[CategoryContent]
    products: list[ProductContent]


# Hardcoded landing content, used ONLY when Directus cannot answer.
# Same data the shop showed before the Directus integration.
FALLBACK_CATEGORIES: list[CategoryContent] = [
    {"name": "یخچال و فریزر", "desc": "ساید، دوقلو، معمولی"},
    {"name": "ماشین لباسشویی", "desc": "۷ تا ۱۰ کیلویی"},
    {"name": "ماشین ظرفشویی", "desc": "رومیزی و ایستاده"},
    {"name": "اجاق گاز و فر", "desc": "صفحه‌ای و مبله"},
    {"name": "جاروبرقی", "desc": "کیسه‌ای و مخزن‌دار"},
    {"name": "لوازم کوچک", "desc": "آشپزخانه و خانه"},
]

FALLBACK_PRODUCTS: list[ProductContent] = [
    {
        "name": "یخچال فریزر دوقلو",
        "price_toman": 45000000,
        "price": "۴۵٬۰۰۰٬۰۰۰",
        "tag": "پرفروش",
        "category": None,
        "image_path": None,
        "id": None,
    },
    {
        "name": "ماشین لباسشویی ۸ کیلویی",
        "price_toman": 28500000,
        "price": "۲۸٬۵۰۰٬۰۰۰",
        "tag": None,
        "category": None,
        "image_path": None,
        "id": None,
    },
    {
        "name": "جاروبرقی ۲۰۰۰ وات",
        "price_toman": 9800000,
        "price": "۹٬۸۰۰٬۰۰۰",
        "tag": "تخفیف‌دار",
        "category": None,
        "image_path": None,
        "id": None,
    },
    {
        "name": "اجاق گاز ۵ شعله",
        "price_toman": 19000000,
        "price": "۱۹٬۰۰۰٬۰۰۰",
        "tag": None,
        "category": None,
        "image_path": None,
        "id": None,
    },
]

# Persian digits and the Persian thousands separator, for prices.
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def format_toman_price(value: int | str | None) -> str:
    """Format a price for the landing page (Persian digits, e.g. ۴۵٬۰۰۰٬۰۰۰).

    Directus stores a price as a Toman integer; the template prints the
    string as-is, so integers are formatted here. Strings pass through
    unchanged (they are already formatted).
    """
    if isinstance(value, int) and not isinstance(value, bool):
        grouped = f"{value:,}".replace(",", "٬")
        return grouped.translate(_FA_DIGITS)
    if isinstance(value, str):
        return value
    return ""


class DirectusClient:
    """Read-only client for the Directus collections the shop needs."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str | None = None,
        token: str | None = None,
        cache: Cache | None = None,
    ) -> None:
        self._http = http
        # Default is the Directus service from compose.yaml.
        self._base_url = (base_url or os.environ.get("DIRECTUS_URL", "http://localhost:8055")).rstrip("/")
        self._token = token or os.environ.get("DIRECTUS_TOKEN")
        # None means "no caching" (callers that do not want it).
        self._cache = cache
        self._category_map: dict[int | str, str] | None = None

    def _headers(self) -> dict[str, str]:
        """Authorization header when a token is configured (public reads need none)."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _get_items(
        self, collection: str, fields: list[str]
    ) -> list[dict[str, object]]:
        """Fetch one published collection from Directus, or raise.

        Raises ``httpx.HTTPError`` on network/HTTP failure and ``ValueError``
        when the response body is not the expected shape. The caller decides
        what a failure means (for landing content: fall back).
        """
        url = f"{self._base_url}/items/{collection}"
        params: dict[str, str | int] = {
            "fields": ",".join(fields),
            "filter[is_active][_eq]": "true",
            "limit": 50,
        }
        response = await self._http.get(url, headers=self._headers(), params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Directus {collection} returned non-object JSON")
        rows = data.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Directus {collection} returned no 'data' list")
        return rows

    async def _fetch_landing_content(self) -> LandingContent:
        """Fetch and map the landing content from Directus (no cache, no fallback)."""
        categories = await self._get_items("categories", ["id", "name", "description"])
        products = await self._get_items(
            "products",
            ["id", "name", "price_toman", "tag", "category", "image_path"],
        )

        # Category id -> name, so each product can carry its category name
        # for the per-category item count. Tolerates rows without an id.
        category_names: dict[int | str, str] = {}
        mapped_categories: list[CategoryContent] = []
        for row in categories:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus category name must be a string")
            raw_desc = row.get("description") or ""
            if not isinstance(raw_desc, str):
                raise ValueError("Directus category description must be a string")
            cat_id = row.get("id")
            if isinstance(cat_id, int | str) and not isinstance(cat_id, bool):
                category_names[cat_id] = name
                category_names[str(cat_id)] = name
            mapped_categories.append({"name": name, "desc": raw_desc})

        mapped_products: list[ProductContent] = []
        for row in products:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus product name must be a string")
            raw_price = row.get("price_toman")
            price_val: int | str | None = (
                raw_price
                if isinstance(raw_price, int | str) and not isinstance(raw_price, bool)
                else None
            )
            raw_tag = row.get("tag")
            tag_val: str | None = raw_tag if isinstance(raw_tag, str) else None
            raw_category = row.get("category")
            category_val: str | None = None
            if isinstance(raw_category, dict):
                cname = raw_category.get("name")
                category_val = str(cname) if isinstance(cname, str) else None
            elif isinstance(raw_category, int | str) and not isinstance(
                raw_category, bool
            ):
                category_val = category_names.get(raw_category) or category_names.get(
                    str(raw_category)
                )
            raw_image = row.get("image_path")
            image_val: str | None = raw_image if isinstance(raw_image, str) else None
            raw_id = row.get("id")
            id_val: str | None = raw_id if isinstance(raw_id, str) else None
            price_toman_val: int | None = (
                price_val
                if isinstance(price_val, int) and not isinstance(price_val, bool)
                else None
            )
            mapped_products.append(
                {
                    "name": name,
                    "price": format_toman_price(price_val),
                    "price_toman": price_toman_val,
                    "id": id_val,
                    "tag": tag_val,
                    "category": category_val,
                    "image_path": image_val,
                }
            )

        return {"categories": mapped_categories, "products": mapped_products}

    async def get_landing_content(self) -> LandingContent:
        """Return landing content: cache first, then Directus, then fallback.

        Any failure — Directus down, timeout, HTTP error, unexpected body —
        means the same thing: serve the fallback and log a warning. The
        shop page must never fail because the CMS is unreachable. Failures
        are not cached, so the next request retries Directus right away.
        """
        if self._cache is not None:
            try:
                cached = await self._cache.get(LANDING_CONTENT_KEY)
                if cached is not None:
                    parsed = json.loads(cached)
                    if (
                        isinstance(parsed, dict)
                        and isinstance(parsed.get("categories"), list)
                        and isinstance(parsed.get("products"), list)
                    ):
                        return cast(LandingContent, parsed)
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks landing
                logger.warning("Landing cache read failed, bypassing cache: %s", exc)
        try:
            content = await self._fetch_landing_content()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Directus unavailable, using hardcoded landing content: %s", exc
            )
            return {
                "categories": list(FALLBACK_CATEGORIES),
                "products": list(FALLBACK_PRODUCTS),
            }
        if self._cache is not None:
            try:
                await self._cache.set(
                    LANDING_CONTENT_KEY, json.dumps(content), LANDING_CONTENT_TTL
                )
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks landing
                logger.warning("Landing cache write failed: %s", exc)
        return content

    async def get_category_map(self) -> dict[int | str, str]:
        """Fetch categories collection from Directus and return {id: name} mapping.

        Cached in memory and in Redis to avoid redundant network calls.
        """
        if self._category_map is not None:
            return self._category_map

        if self._cache is not None:
            try:
                cached = await self._cache.get(CATEGORIES_MAP_KEY)
                if cached is not None:
                    parsed = json.loads(cached)
                    if isinstance(parsed, dict):
                        mapping: dict[int | str, str] = {}
                        for k, v in parsed.items():
                            mapping[k] = str(v)
                            if k.isdigit():
                                mapping[int(k)] = str(v)
                        self._category_map = mapping
                        return self._category_map
            except Exception as exc:  # noqa: BLE001
                logger.warning("Category map cache read failed: %s", exc)

        mapping: dict[int | str, str] = {}
        try:
            items = await self._get_items("categories", ["id", "name"])
            for item in items:
                cid = item.get("id")
                cname = item.get("name")
                if isinstance(cname, str):
                    if isinstance(cid, int) and not isinstance(cid, bool):
                        mapping[cid] = cname
                        mapping[str(cid)] = cname
                    elif isinstance(cid, str):
                        mapping[cid] = cname
                        if cid.isdigit():
                            mapping[int(cid)] = cname
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch categories map from Directus: %s", exc)

        if mapping:
            self._category_map = mapping
            if self._cache is not None:
                try:
                    to_cache = {str(k): v for k, v in mapping.items()}
                    await self._cache.set(
                        CATEGORIES_MAP_KEY, json.dumps(to_cache), CATEGORIES_MAP_TTL
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Category map cache write failed: %s", exc)

        return mapping

    async def list_products(self, limit: int = 20) -> list[dict]:
        """Active products, newest first, from Directus.

        This is the product list the shop serves (``GET /products``).
        Raises ``httpx.HTTPError`` or ``ValueError`` on any failure, so
        the caller can fall back to the same table through SQLAlchemy.
        """
        params: dict[str, str | int] = {
            "fields": ",".join(PRODUCT_READ_FIELDS),
            "filter[is_active][_eq]": "true",
            "sort": "-created_at",
            "limit": max(1, limit),
        }
        response = await self._http.get(
            f"{self._base_url}/items/products",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Directus products returned non-object JSON")
        rows = data.get("data")
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise ValueError("Directus products returned no 'data' list")
        category_map = await self.get_category_map()
        return [_product_row(row, category_map=category_map) for row in rows]

    async def list_categories(self) -> list[dict[str, object]]:
        """Fetch categories collection from Directus, or return fallback."""
        try:
            return await self._get_items("categories", ["id", "name", "description"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch categories list from Directus: %s", exc)
            return [
                {"id": idx + 1, "name": c["name"], "description": c["desc"]}
                for idx, c in enumerate(FALLBACK_CATEGORIES)
            ]

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        """Fetch active products belonging to a category from Directus."""
        category_map = await self.get_category_map()
        target_id: int | str | None = None
        target_name: str | None = None

        if isinstance(category_identifier, int) or (
            isinstance(category_identifier, str) and category_identifier.isdigit()
        ):
            target_id = int(category_identifier)
            target_name = category_map.get(target_id) or category_map.get(str(target_id))
        else:
            target_name = str(category_identifier).strip()
            for cid, cname in category_map.items():
                if cname.lower() == target_name.lower():
                    target_id = cid
                    break

        params: dict[str, str | int] = {
            "fields": ",".join(PRODUCT_READ_FIELDS),
            "filter[is_active][_eq]": "true",
            "sort": "-created_at",
            "limit": max(1, limit),
        }
        if target_id is not None:
            params["filter[category][_eq]"] = str(target_id)

        try:
            response = await self._http.get(
                f"{self._base_url}/items/products",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get("data")
            if isinstance(rows, list):
                result = [
                    _product_row(row, category_map=category_map)
                    for row in rows
                    if isinstance(row, dict)
                ]
                if target_name and target_id is None:
                    result = [p for p in result if p.get("category") == target_name]
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list products by category from Directus: %s", exc)

        # Fallback: filter from list_products
        try:
            all_products = await self.list_products(limit)
            matched = []
            for p in all_products:
                p_cat = p.get("category")
                if target_name and p_cat and target_name.lower() in p_cat.lower():
                    matched.append(p)
                elif target_id and p_cat and (
                    str(target_id) == str(p_cat) or category_map.get(target_id) == p_cat
                ):
                    matched.append(p)
            return matched
        except Exception:  # noqa: BLE001
            return []

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        """One product by id from Directus. None when it does not exist.

        No ``is_active`` filter here: a single product read matches the
        old database read, which never filtered on active either.
        Directus answers 403 (not 404) for a missing single item under
        the public role, so both 403 and 404 mean "no such product".
        Any other failure raises, so the caller can fall back.
        """
        response = await self._http.get(
            f"{self._base_url}/items/products/{product_id}",
            headers=self._headers(),
            params={"fields": ",".join(PRODUCT_READ_FIELDS)},
        )
        if response.status_code in (404, 403):
            return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Directus product returned non-object JSON")
        row = data.get("data")
        if not isinstance(row, dict):
            raise ValueError("Directus product returned no 'data' object")
        category_map = await self.get_category_map()
        return _product_row(row, category_map=category_map)


def _product_row(
    row: dict,
    category_map: dict[int | str, str] | None = None,
) -> dict:
    """Validate one Directus product row into the ProductRead shape.

    Only the fields ``ProductRead`` needs are kept, so the route can
    build the response with ``ProductRead.model_validate``. Defaults
    gracefully for fields that Directus editors may omit (stock, reserved, is_active).
    """
    pid = row.get("id")
    if pid is None:
        raise ValueError("Directus product id is missing")
    pid_str = str(pid)

    name = row.get("name")
    if not isinstance(name, str):
        raise ValueError("Directus product name must be a string")

    price = row.get("price_toman")
    if not isinstance(price, int) or isinstance(price, bool):
        raise ValueError("Directus product price_toman must be an integer")

    stock = row.get("stock")
    if stock is None:
        stock = 0
    elif not isinstance(stock, int) or isinstance(stock, bool):
        raise ValueError("Directus product stock must be an integer")

    reserved = row.get("reserved")
    if reserved is None:
        reserved = 0
    elif not isinstance(reserved, int) or isinstance(reserved, bool):
        raise ValueError("Directus product reserved must be an integer")

    is_active = row.get("is_active")
    if is_active is None:
        is_active = True
    elif not isinstance(is_active, bool):
        raise ValueError("Directus product is_active must be a boolean")

    description = row.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("Directus product description must be a string or null")

    image_path = row.get("image_path")
    if image_path is not None and not isinstance(image_path, str):
        raise ValueError("Directus product image_path must be a string or null")

    created_at = row.get("created_at")
    if created_at is None:
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
    elif not isinstance(created_at, str):
        raise ValueError("Directus product created_at must be a string")

    category_val = row.get("category")
    category: str | None = None
    if isinstance(category_val, dict):
        cname = category_val.get("name")
        category = str(cname) if cname is not None else None
    elif category_val is not None:
        if category_map and category_val in category_map:
            category = category_map[category_val]
        elif category_map and str(category_val) in category_map:
            category = category_map[str(category_val)]
        elif isinstance(category_val, str) and not category_val.isdigit():
            category = category_val
        elif category_map and isinstance(category_val, str) and category_val.isdigit():
            cid = int(category_val)
            category = category_map.get(cid, category_val)
        else:
            category = str(category_val)

    tag = row.get("tag")
    if tag is not None and not isinstance(tag, str):
        tag = str(tag)

    return {
        "id": pid_str,
        "name": name,
        "description": description,
        "price_toman": price,
        "stock": stock,
        "reserved": reserved,
        "image_path": image_path,
        "is_active": is_active,
        "created_at": created_at,
        "category": category,
        "tag": tag,
    }


# Fields the product list and single-product reads ask Directus for.
# Same columns the ``products`` table has, so no extra field is needed.
PRODUCT_READ_FIELDS = [
    "id",
    "name",
    "description",
    "price_toman",
    "stock",
    "reserved",
    "image_path",
    "is_active",
    "category",
    "tag",
    "created_at",
]


async def invalidate_landing_content(cache: Cache) -> None:
    """Delete-on-change: drop the cached landing content right now.

    Call this when content changes in Directus (webhook receiver or admin
    action) instead of waiting up to 60 seconds for the TTL to expire.
    """
    await cache.delete(LANDING_CONTENT_KEY)
    await cache.delete(CATEGORIES_MAP_KEY)


def get_directus_client(client: HttpClientDep, cache: CacheDep) -> DirectusClient:
    """Dependency: build a DirectusClient on the shared HTTP client and cache."""
    return DirectusClient(client, cache=cache)


# Reusable FastAPI dependency alias for the Directus client
DirectusClientDep = Annotated[DirectusClient, Depends(get_directus_client)]


__all__ = [
    "CategoryContent",
    "DirectusClient",
    "DirectusClientDep",
    "FALLBACK_CATEGORIES",
    "FALLBACK_PRODUCTS",
    "LANDING_CONTENT_KEY",
    "LANDING_CONTENT_TTL",
    "LandingContent",
    "ProductContent",
    "format_toman_price",
    "get_directus_client",
    "invalidate_landing_content",
]
