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
"""Meilisearch client: the shop's search index.

Meilisearch is ONLY a search index — a derived copy of the Directus
catalogue, never a source of truth. Directus owns the products; this
client only reads them (via the sync service) and answers text
searches with typo tolerance and Persian-aware tokenisation
(Meilisearch v1.21+ has a dedicated Persian pipeline).

Why httpx and not the official ``meilisearch`` SDK: the official SDK
is synchronous, and this shop is fully async — a sync call would
block the event loop on every search. The community async SDK is not
official. The Meilisearch REST API is only a handful of calls, and
the shop already wraps every server-to-server call in an httpx
client (Directus, Zibal, Meli Payamak), so httpx is the right fit.

Auth: the master key goes in the ``Authorization: Bearer`` header.
It is never logged. Without a key the server runs open, so the
compose file keeps port 7700 internal-only (no published port) and
the key must be set in .env (``MEILI_MASTER_KEY``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends

from integrations.http_client import HttpClientDep

logger = logging.getLogger("bandeh.meilisearch")

# The one index the shop uses. Its documents are Directus product
# dicts (see services/search_index_service.index_document).
INDEX_UID = "products"

# Index settings, applied by ensure_index(). Only the fields listed
# here are managed; everything else keeps its Meilisearch default.
# - searchableAttributes, in order of importance: name first, then
#   description, category, tag.
# - filterableAttributes: what filters may use. The spec keys are
#   nested (``specs.screen_size_inch``) — Meilisearch filters nested
#   attributes with dot notation.
# - sortableAttributes: price, for a future cheapest-first sort.
# - typoTolerance: on (the default). A query with a small typo still
#   finds the product.
INDEX_SETTINGS: dict[str, object] = {
    "searchableAttributes": ["name", "description", "category", "tag"],
    "filterableAttributes": [
        "category",
        "condition",
        "specs.screen_size_inch",
        "specs.capacity_kg",
        "specs.capacity_liter",
    ],
    "sortableAttributes": ["price_toman"],
    "typoTolerance": {"enabled": True},
}

# Meilisearch answers writes with a task id; the work itself is
# async, so mutating calls wait for the task to succeed.
_TASK_POLL_SECONDS = 0.2
_TASK_TIMEOUT_SECONDS = 30.0


class MeilisearchError(Exception):
    """Meilisearch is down, misconfigured, or refused a request."""


def _task_uid(task: dict) -> int:
    """The task id from a task object (``taskUid`` on create, ``uid`` on read)."""
    uid = task.get("taskUid", task.get("uid"))
    if not isinstance(uid, int) or isinstance(uid, bool):
        raise MeilisearchError(f"Meilisearch returned no task id: {task}")
    return uid


class MeilisearchClient:
    """Async client for one Meilisearch instance (the products index).

    Pass an ``httpx.AsyncClient`` (e.g. one built on a MockTransport)
    for tests; otherwise a short-lived client is created per call with
    timeout 10.0 and trust_env=False — the same precedent as the SMS
    client (see integrations/sms.py).
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._http = http
        self._base_url = (
            base_url or os.environ.get("MEILI_URL", "http://localhost:7700")
        ).rstrip("/")
        self._api_key = (
            api_key if api_key is not None else os.environ.get("MEILI_MASTER_KEY")
        )

    @property
    def is_configured(self) -> bool:
        """True when a URL and a master key are available."""
        return bool(
            self._base_url
            and self._api_key
            and self._api_key.strip()
        )

    def _headers(self) -> dict[str, str]:
        """Authorization header (the master key, never logged)."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        """The HTTP client for one call: shared when injected, else fresh."""
        if self._http is not None:
            yield self._http
        else:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                yield client

    async def health(self) -> bool:
        """True when Meilisearch answers /health (no auth needed)."""
        try:
            async with self._client() as http:
                response = await http.get(f"{self._base_url}/health")
            return (
                response.status_code == 200
                and response.json().get("status") == "available"
            )
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return False

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        """Text search on the products index; returns the hit documents.

        Raises ``httpx.HTTPError`` when Meilisearch is unreachable and
        ``MeilisearchError`` when the answer is not the expected shape.
        The caller (search_service) catches both and falls back.
        """
        async with self._client() as http:
            response = await http.post(
                f"{self._base_url}/indexes/{INDEX_UID}/search",
                headers=self._headers(),
                json={"q": query, "limit": max(1, limit)},
            )
            response.raise_for_status()
            data = response.json()
        hits = data.get("hits")
        if not isinstance(hits, list):
            raise MeilisearchError("Meilisearch search returned no 'hits' list")
        return [hit for hit in hits if isinstance(hit, dict)]

    async def get_settings(self) -> dict:
        """Current settings of the products index ({} when it has none)."""
        async with self._client() as http:
            response = await http.get(
                f"{self._base_url}/indexes/{INDEX_UID}/settings",
                headers=self._headers(),
            )
            if response.status_code == 404:
                return {}
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict):
            raise MeilisearchError("Meilisearch settings returned non-object JSON")
        return data

    async def update_settings(self, settings: dict) -> dict:
        """Apply settings (PATCH) and wait until the task succeeds."""
        async with self._client() as http:
            response = await http.patch(
                f"{self._base_url}/indexes/{INDEX_UID}/settings",
                headers=self._headers(),
                json=settings,
            )
            response.raise_for_status()
            task = response.json()
        return await self.wait_for_task(_task_uid(task))

    async def wait_for_task(
        self, task_uid: int, timeout: float = _TASK_TIMEOUT_SECONDS
    ) -> dict:
        """Poll one task until it succeeds, fails, or the timeout hits."""
        deadline = time.monotonic() + timeout
        while True:
            async with self._client() as http:
                response = await http.get(
                    f"{self._base_url}/tasks/{task_uid}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                task = response.json()
            status = task.get("status")
            if status == "succeeded":
                return task
            if status == "failed":
                error = task.get("error") or {}
                message = error.get("message", error) if isinstance(error, dict) else error
                raise MeilisearchError(
                    f"Meilisearch task {task_uid} failed: {message}"
                )
            if status == "canceled":
                raise MeilisearchError(f"Meilisearch task {task_uid} was canceled")
            if time.monotonic() > deadline:
                raise MeilisearchError(
                    f"Meilisearch task {task_uid} did not finish in {timeout}s "
                    f"(status {status})"
                )
            await asyncio.sleep(_TASK_POLL_SECONDS)

    def _settings_match(self, current: dict) -> bool:
        """True when the live settings already hold every managed value.

        Only the keys INDEX_SETTINGS manages are compared; the rest
        keeps whatever Meilisearch defaults to. A dict value (typo
        tolerance) matches when the live dict holds the same keys.
        """
        for key, wanted in INDEX_SETTINGS.items():
            got = current.get(key)
            if isinstance(wanted, dict):
                if not isinstance(got, dict):
                    return False
                for sub_key, sub_wanted in wanted.items():
                    if got.get(sub_key) != sub_wanted:
                        return False
            elif got != wanted:
                return False
        return True

    async def ensure_index(self) -> None:
        """Create the products index (primaryKey ``id``) and its settings.

        Creates the index when it does not exist yet, then applies
        INDEX_SETTINGS — but only when they differ, so a routine sync
        does not trigger a pointless reindex every 5 minutes.
        """
        async with self._client() as http:
            response = await http.get(
                f"{self._base_url}/indexes/{INDEX_UID}",
                headers=self._headers(),
            )
        if response.status_code == 404:
            async with self._client() as http:
                response = await http.post(
                    f"{self._base_url}/indexes",
                    headers=self._headers(),
                    json={"uid": INDEX_UID, "primaryKey": "id"},
                )
                response.raise_for_status()
                task = response.json()
            await self.wait_for_task(_task_uid(task))
        elif response.status_code != 200:
            response.raise_for_status()
        if not self._settings_match(await self.get_settings()):
            await self.update_settings(INDEX_SETTINGS)

    async def replace_documents(self, documents: list[dict]) -> dict:
        """Add or replace documents (full replace per document).

        POST /documents is the replace variant: a document that is
        already in the index is replaced wholesale, so a field removed
        in Directus does not linger. An empty list is a no-op.
        """
        if not documents:
            return {}
        async with self._client() as http:
            response = await http.post(
                f"{self._base_url}/indexes/{INDEX_UID}/documents",
                headers=self._headers(),
                params={"primaryKey": "id"},
                json=documents,
            )
            response.raise_for_status()
            task = response.json()
        return await self.wait_for_task(_task_uid(task))

    async def delete_documents(self, document_ids: list[str]) -> dict:
        """Delete documents by id. An empty list is a no-op."""
        if not document_ids:
            return {}
        async with self._client() as http:
            # httpx delete() takes no body kwargs: use request() for the
            # JSON array of ids.
            response = await http.request(
                "DELETE",
                f"{self._base_url}/indexes/{INDEX_UID}/documents",
                headers=self._headers(),
                json=list(document_ids),
            )
            response.raise_for_status()
            task = response.json()
        return await self.wait_for_task(_task_uid(task))

    async def list_document_ids(self, limit: int = 1000) -> list[str]:
        """All document ids in the index ([] when it does not exist yet)."""
        async with self._client() as http:
            response = await http.post(
                f"{self._base_url}/indexes/{INDEX_UID}/documents/fetch",
                headers=self._headers(),
                json={"limit": max(1, limit), "fields": ["id"]},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            data = response.json()
        results = data.get("results")
        if not isinstance(results, list):
            raise MeilisearchError(
                "Meilisearch documents fetch returned no 'results' list"
            )
        return [
            str(row["id"])
            for row in results
            if isinstance(row, dict) and row.get("id") is not None
        ]


def get_meilisearch_client(
    http: httpx.AsyncClient | None = None,
) -> MeilisearchClient | None:
    """Build a client from settings; None when Meilisearch is not configured.

    Settings win (``MEILI_URL`` / ``MEILI_MASTER_KEY`` in env_settings);
    when settings cannot be built, the plain environment variables are
    tried. Pass the shared httpx client when one exists; otherwise
    each call opens its own short-lived client.
    """
    url: str | None = None
    key: str | None = None
    try:
        from env_settings import get_settings

        settings = get_settings()
        url = settings.meili_url
        key = (
            settings.meili_master_key.get_secret_value()
            if settings.meili_master_key is not None
            else None
        )
    except Exception:  # noqa: BLE001 - settings failure means "try env"
        url = None
        key = None
    client = MeilisearchClient(http=http, base_url=url, api_key=key)
    return client if client.is_configured else None


def get_meilisearch_client_dep(
    http: HttpClientDep,
) -> MeilisearchClient | None:
    """Dependency: a client on the shared HTTP client (None when unconfigured)."""
    return get_meilisearch_client(http)


# Reusable FastAPI dependency alias for the Meilisearch client
MeilisearchClientDep = Annotated[
    MeilisearchClient | None, Depends(get_meilisearch_client_dep)
]


__all__ = [
    "INDEX_SETTINGS",
    "INDEX_UID",
    "MeilisearchClient",
    "MeilisearchClientDep",
    "MeilisearchError",
    "get_meilisearch_client",
    "get_meilisearch_client_dep",
]
