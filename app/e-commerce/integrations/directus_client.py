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

import json
import logging
import os
from typing import Annotated, TypedDict, cast

import httpx
from fastapi import Depends

from core.cache import Cache, CacheDep
from integrations.http_client import HttpClientDep

logger = logging.getLogger("bandeh.directus")

# Cache key and lifetime for the landing content (seconds).
LANDING_CONTENT_KEY = "bandeh:landing:content"
LANDING_CONTENT_TTL = 60


class CategoryContent(TypedDict):
    """Rendered category card data on the landing page."""

    name: str
    desc: str


class ProductContent(TypedDict):
    """Rendered product card data on the landing page."""

    name: str
    price: str
    tag: str | None


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
    {"name": "یخچال فریزر دوقلو", "price": "۴۵٬۰۰۰٬۰۰۰", "tag": "پرفروش"},
    {"name": "ماشین لباسشویی ۸ کیلویی", "price": "۲۸٬۵۰۰٬۰۰۰", "tag": None},
    {"name": "جاروبرقی ۲۰۰۰ وات", "price": "۹٬۸۰۰٬۰۰۰", "tag": "تخفیف‌دار"},
    {"name": "اجاق گاز ۵ شعله", "price": "۱۹٬۰۰۰٬۰۰۰", "tag": None},
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
            "filter[status][_eq]": "published",
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
        categories = await self._get_items("categories", ["name", "description"])
        products = await self._get_items("products", ["name", "price", "tag"])

        mapped_categories: list[CategoryContent] = []
        for row in categories:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus category name must be a string")
            raw_desc = row.get("description") or ""
            if not isinstance(raw_desc, str):
                raise ValueError("Directus category description must be a string")
            mapped_categories.append({"name": name, "desc": raw_desc})

        mapped_products: list[ProductContent] = []
        for row in products:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus product name must be a string")
            raw_price = row.get("price")
            price_val: int | str | None = (
                raw_price
                if isinstance(raw_price, int | str) and not isinstance(raw_price, bool)
                else None
            )
            raw_tag = row.get("tag")
            tag_val: str | None = raw_tag if isinstance(raw_tag, str) else None
            mapped_products.append(
                {
                    "name": name,
                    "price": format_toman_price(price_val),
                    "tag": tag_val,
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


async def invalidate_landing_content(cache: Cache) -> None:
    """Delete-on-change: drop the cached landing content right now.

    Call this when content changes in Directus (webhook receiver or admin
    action) instead of waiting up to 60 seconds for the TTL to expire.
    """
    await cache.delete(LANDING_CONTENT_KEY)


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
