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
"""Sync the Meilisearch products index from Directus.

Meilisearch is ONLY a search index — a derived copy of the Directus
catalogue, never a source of truth. Directus owns the products; this
service copies them into the index, whole, on every run (the
catalogue is tiny), and deletes index documents whose product no
longer exists in Directus. Two triggers run it: the 5-minute
``SearchIndexTask`` in the scheduler and the staff-only
``POST /admin/search/reindex`` endpoint. A Directus webhook is the
future upgrade (faster than polling, but more moving parts).

Stock is NOT indexed: the search path attaches fresh stock from the
stock table after every search (see services.stock_service), so the
index never carries a stale count.
"""

from __future__ import annotations

import logging

import httpx

from integrations.directus_client import DirectusClient
from integrations.meilisearch_client import (
    MeilisearchClient,
    MeilisearchError,
    get_meilisearch_client,
)

logger = logging.getLogger("bandeh.search_index")

# Fields a Directus product row carries into the index. This is the
# _product_row shape minus stock/reserved (dead zeros — the stock
# table owns those numbers, not Directus and not the index).
INDEX_DOCUMENT_FIELDS = (
    "id",
    "name",
    "description",
    "price_toman",
    "image_path",
    "is_active",
    "created_at",
    "category",
    "tag",
    "condition",
    "condition_stars",
    "specs",
)


def index_document(row: dict) -> dict:
    """One Directus product row as a Meilisearch document.

    ``id`` is always a string (the primary key). ``specs`` is always
    a dict (a non-object value means "no attributes"). Everything
    else passes through as Directus sent it.
    """
    doc = {key: row.get(key) for key in INDEX_DOCUMENT_FIELDS}
    doc["id"] = str(row["id"])
    doc["specs"] = row.get("specs") if isinstance(row.get("specs"), dict) else {}
    doc["is_active"] = row.get("is_active") is True
    return doc


async def sync_products_index(
    directus: DirectusClient,
    meilisearch: MeilisearchClient | None = None,
    http: httpx.AsyncClient | None = None,
) -> dict[str, int]:
    """Full re-index: Directus -> Meilisearch. Returns counts.

    Raises ``MeilisearchError`` when Meilisearch is not configured or
    refuses a step, and ``httpx.HTTPError`` / ``ValueError`` when
    Directus cannot be read. Callers decide what a failure means (the
    scheduler logs it; the admin endpoint answers 503). Search itself
    never breaks: it falls back to the Directus substring search.
    """
    client = meilisearch or get_meilisearch_client(http)
    if client is None:
        raise MeilisearchError(
            "Meilisearch is not configured (MEILI_URL / MEILI_MASTER_KEY missing)"
        )
    await client.ensure_index()
    rows = await directus.list_products(limit=50)
    docs = [index_document(row) for row in rows]
    await client.replace_documents(docs)
    wanted = {doc["id"] for doc in docs}
    current = await client.list_document_ids()
    stale = [doc_id for doc_id in current if doc_id not in wanted]
    if stale:
        await client.delete_documents(stale)
    result = {"indexed": len(docs), "deleted": len(stale)}
    logger.info(
        "Search index synced: %d indexed, %d deleted",
        result["indexed"],
        result["deleted"],
    )
    return result


__all__ = ["INDEX_DOCUMENT_FIELDS", "index_document", "sync_products_index"]
