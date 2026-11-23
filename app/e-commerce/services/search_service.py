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
"""Product search: Directus is the catalogue now.

Directus owns ALL product content, so search reads Directus — there
is no Postgres products table in the shop's SQLAlchemy anymore.
Matching is a plain substring search over name + description, in
Python: it catches partial words (what Persian shoppers type), works
the same on every database, and the catalogue is small (the list
call fetches at most 50 rows). The query text is never pasted into
SQL — there is no SQL here at all.

Results are Directus product dicts (the ``_product_row`` shape:
id, name, description, price_toman, category, tag, condition,
condition_stars, specs, plus stock/reserved set to 0 — the real
stock numbers come from the stock table, and callers that show stock
must run ``services.stock_service.attach_stock`` on the rows first).

Spec filtering (attributes like TV screen size) does NOT live here:
the routes filter the returned dicts in Python
(``_matches_product_filters`` in api/routes/products.py), because
the attribute values live in the same Directus rows.

Both paths are always limited (default 20, max 50).
"""

from __future__ import annotations

import re

from integrations.directus_client import DirectusClient

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_QUERY_LENGTH = 200

# Product condition values (see the Directus products collection). A
# filter outside this set means "no filter" — never an error, never
# an empty result.
CONDITIONS = ("new", "used")


def clean_condition(condition: str | None) -> str | None:
    """Keep only a real condition value; anything else means 'all'."""
    if isinstance(condition, str) and condition.strip().lower() in CONDITIONS:
        return condition.strip().lower()
    return None


def _normalise_limit(limit: int) -> int:
    """Clamp the limit: at least 1, at most MAX_LIMIT."""
    return min(max(limit, 1), MAX_LIMIT)


def _clean_query(query: str) -> str:
    """Strip and cap the query. Empty string means 'no search'."""
    return query.strip()[:MAX_QUERY_LENGTH]


def _matches_query(row: dict, needle: str) -> bool:
    """True when the needle is a substring of the name or description."""
    if not needle:
        return True
    lowered = needle.lower()
    name = str(row.get("name") or "").lower()
    if lowered in name:
        return True
    description = str(row.get("description") or "").lower()
    return lowered in description


def _filter_condition(rows: list[dict], condition: str | None) -> list[dict]:
    """Keep only rows with the picked condition (None means all)."""
    cond = clean_condition(condition)
    if cond is None:
        return list(rows)
    return [row for row in rows if row.get("condition", "new") == cond]


async def search_products(
    directus: DirectusClient,
    query: str,
    limit: int = DEFAULT_LIMIT,
    condition: str | None = None,
) -> list[dict]:
    """Substring search (name + description), active products only.

    An empty query searches nothing, by design — callers that want a
    plain list use :func:`list_products` instead.
    """
    clean = _clean_query(query)
    if not clean:
        return []
    rows = await directus.list_products(limit=50)
    matched = [row for row in rows if _matches_query(row, clean)]
    matched = _filter_condition(matched, condition)
    return matched[: _normalise_limit(limit)]


async def list_products(
    directus: DirectusClient,
    limit: int = DEFAULT_LIMIT,
    condition: str | None = None,
) -> list[dict]:
    """Active products, newest first, always limited."""
    rows = await directus.list_products(limit=_normalise_limit(limit))
    return _filter_condition(rows, condition)


async def list_products_by_category(
    directus: DirectusClient,
    category: str | int,
    limit: int = 50,
) -> list[dict]:
    """Active products of one category, from Directus."""
    return await directus.list_products_by_category(
        category, limit=_normalise_limit(limit)
    )


# Attribute keys are snake_case identifiers from the category's
# definitions in Directus (e.g. screen_size_inch, capacity_kg). A key
# outside this shape is dropped — it can never be a real attribute.
_SPEC_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def clean_spec_filters(
    filters: dict[str, list[object]] | None,
) -> dict[str, list[str]]:
    """Keep only usable spec filters: {key: [values]}.

    A key must be a plain identifier; a value list must be non-empty.
    Values are stringified because specs hold numbers (55) while the
    URL carries text ("55"). ``None`` values are dropped; anything
    else (a bad key, an empty list) means "no filter for that
    attribute", never an error.
    """
    cleaned: dict[str, list[str]] = {}
    if not isinstance(filters, dict):
        return cleaned
    for key, values in filters.items():
        if not isinstance(key, str) or not _SPEC_KEY_RE.match(key):
            continue
        if not isinstance(values, list):
            continue
        kept = [str(v) for v in values if v is not None and str(v) != ""]
        if kept:
            cleaned[key] = kept
    return cleaned


__all__ = [
    "CONDITIONS",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_QUERY_LENGTH",
    "clean_condition",
    "clean_spec_filters",
    "list_products",
    "list_products_by_category",
    "search_products",
]
