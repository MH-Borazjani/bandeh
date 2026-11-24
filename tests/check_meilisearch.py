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
"""Runnable self-check for Meilisearch search + index sync. Run from repo root:

    python tests/check_meilisearch.py

In-memory SQLite only — no server, no live Postgres, no live
Meilisearch. The Meilisearch client is exercised against a fake
in-memory index and against httpx.MockTransport; the real binary is
checked separately (see docs/search-meilisearch.md). Covers: the
index document shape (no stock in the index), full sync (index all
Directus products, delete stale documents), the Meilisearch search
path, the fallback to the Directus substring search when Meilisearch
fails or is unconfigured, the client against a mock transport (task
waiting, index creation, settings), the scheduler no-op when
Meilisearch is not configured, and the staff-only reindex endpoint
(401 guest, 403 customer, 200 staff, 503 unconfigured).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "dummy-secret-key-for-tests-12345678")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
# Meilisearch is deliberately NOT configured here: the fallback and
# unconfigured paths must work with no MEILI_URL / MEILI_MASTER_KEY.

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from fastapi.responses import Response
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import SESSION_COOKIE_NAME, create_session
from db.base import Base
from db.models.user import User, UserRole
from db.session import get_db
from integrations.directus_client import get_directus_client
from integrations.meilisearch_client import (
    INDEX_SETTINGS,
    MeilisearchClient,
    MeilisearchError,
    get_meilisearch_client,
    get_meilisearch_client_dep,
)
from main import app
from services.scheduler import SearchIndexTask
from services.search_index_service import index_document, sync_products_index
from services.search_service import search_products

TV_ID = "22222222-2222-4222-8222-222222222222"
WASHER_ID = "33333333-3333-4333-8333-333333333333"
FRIDGE_ID = "11111111-1111-4111-8111-111111111111"
STALE_ID = "99999999-9999-4999-8999-999999999999"
STAFF_PHONE = "09123456789"
CUSTOMER_PHONE = "09123456780"


def _product(pid, name, description, price, category, condition="new", specs=None):
    """One fake CMS product row (ProductRead shape, with specs)."""
    return {
        "id": pid,
        "name": name,
        "description": description,
        "price_toman": price,
        "stock": 0,
        "reserved": 0,
        "category": category,
        "tag": None,
        "image_path": None,
        "is_active": True,
        "created_at": "2026-11-24T00:00:00+00:00",
        "condition": condition,
        "condition_stars": 4 if condition == "used" else None,
        "specs": specs or {},
    }


FAKE_PRODUCTS = [
    _product(
        TV_ID, "تلویزیون سامسونگ ۵۵ اینچ", "صفحه بزرگ", 32_000_000,
        "تلویزیون و صدا", specs={"screen_size_inch": 55},
    ),
    _product(
        WASHER_ID, "ماشین لباسشویی سامسونگ", "۸ کیلوگرم", 28_500_000,
        "شستشو", condition="used", specs={"capacity_kg": 8},
    ),
    _product(
        FRIDGE_ID, "یخچال سامسونگ", "دو درب", 45_000_000,
        "یخچال و فریزر", specs={"capacity_liter": 450},
    ),
]


class _FakeDirectusClient:
    """CMS stand-in: the three products above, active only."""

    async def list_products(self, limit: int = 20) -> list[dict]:
        return [dict(p) for p in FAKE_PRODUCTS[:limit]]

    async def list_products_by_category(self, category_identifier, limit: int = 50):
        return [dict(p) for p in FAKE_PRODUCTS if p["category"] == str(category_identifier)][:limit]

    async def list_categories(self) -> list[dict]:
        return [
            {"id": 1, "name": "یخچال و فریزر"},
            {"id": 2, "name": "تلویزیون و صدا"},
            {"id": 3, "name": "شستشو"},
        ]


class _FakeMeilisearch(MeilisearchClient):
    """In-memory Meilisearch stand-in (same method names as the real client).

    ``search`` matches by substring, like the fallback — the test
    proves the search path USES Meilisearch (the call is recorded),
    not that it is typo-tolerant; typo tolerance is proven against
    the real binary (docs/search-meilisearch.md).
    """

    def __init__(self, docs=None, fail: bool = False) -> None:
        # No HTTP client: every method below is overridden, so the
        # base class never touches the network. The attributes exist
        # so is_configured and friends do not break.
        self._http = None
        self._base_url = "http://fake"
        self._api_key = "fake"
        self.docs = {d["id"]: dict(d) for d in (docs or [])}
        self.fail = fail
        self.search_calls: list[str] = []
        self.settings_applied: dict | None = None
        self.ensure_calls = 0

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        if self.fail:
            raise MeilisearchError("fake Meilisearch is down")
        self.search_calls.append(query)
        needle = query.lower()
        hits = [
            dict(d)
            for d in self.docs.values()
            if needle in d["name"].lower()
            or needle in (d.get("description") or "").lower()
        ]
        return hits[:limit]

    async def ensure_index(self) -> None:
        self.ensure_calls += 1
        self.settings_applied = dict(INDEX_SETTINGS)

    async def replace_documents(self, documents: list[dict]) -> dict:
        if self.fail:
            raise MeilisearchError("fake Meilisearch is down")
        for doc in documents:
            self.docs[doc["id"]] = dict(doc)
        return {}

    async def delete_documents(self, document_ids: list[str]) -> dict:
        for doc_id in document_ids:
            self.docs.pop(doc_id, None)
        return {}

    async def list_document_ids(self, limit: int = 1000) -> list[str]:
        return list(self.docs)[:limit]

    async def health(self) -> bool:
        return not self.fail


class _FailingMeilisearch(_FakeMeilisearch):
    """Every call raises: proves the fallback path."""

    async def search(self, query: str, limit: int = 20) -> list[dict]:
        raise httpx.ConnectError("fake connection refused", request=httpx.Request("POST", "http://x"))


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """A tiny Meilisearch server: enough to drive the real client."""
    path = request.url.path
    method = request.method
    if method == "GET" and path == "/health":
        return httpx.Response(200, json={"status": "available"})
    if method == "POST" and path == "/indexes/products/search":
        return httpx.Response(
            200,
            json={
                "hits": [dict(FAKE_PRODUCTS[0])],
                "estimatedTotalHits": 1,
                "processingTimeMs": 1,
                "query": "test",
            },
        )
    if method == "GET" and path == "/indexes/products":
        return httpx.Response(404, json={"message": "Index `products` not found."})
    if method == "POST" and path == "/indexes":
        return httpx.Response(
            202,
            json={
                "taskUid": 1,
                "indexUid": "products",
                "status": "enqueued",
                "type": "indexCreation",
                "enqueuedAt": "2026-11-24T00:00:00Z",
            },
        )
    if method == "GET" and path == "/indexes/products/settings":
        # Deliberately different from INDEX_SETTINGS: ensure_index
        # must PATCH them.
        return httpx.Response(200, json={"searchableAttributes": ["*"]})
    if method == "PATCH" and path == "/indexes/products/settings":
        global _PATCHED_SETTINGS
        _PATCHED_SETTINGS = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            202,
            json={
                "taskUid": 2,
                "indexUid": "products",
                "status": "enqueued",
                "type": "settingsUpdate",
                "enqueuedAt": "2026-11-24T00:00:00Z",
            },
        )
    if method == "GET" and path.startswith("/tasks/"):
        return httpx.Response(
            200,
            json={
                "uid": int(path.rsplit("/", 1)[-1]),
                "indexUid": "products",
                "status": "succeeded",
                "type": "indexCreation",
                "enqueuedAt": "2026-11-24T00:00:00Z",
            },
        )
    if method == "POST" and path == "/indexes/products/documents":
        return httpx.Response(
            202,
            json={
                "taskUid": 3,
                "indexUid": "products",
                "status": "enqueued",
                "type": "documentAdditionOrUpdate",
                "enqueuedAt": "2026-11-24T00:00:00Z",
            },
        )
    if method == "DELETE" and path == "/indexes/products/documents":
        return httpx.Response(
            202,
            json={
                "taskUid": 4,
                "indexUid": "products",
                "status": "enqueued",
                "type": "documentDeletion",
                "enqueuedAt": "2026-11-24T00:00:00Z",
            },
        )
    if method == "POST" and path == "/indexes/products/documents/fetch":
        return httpx.Response(
            200,
            json={
                "results": [{"id": TV_ID}, {"id": WASHER_ID}],
                "offset": 0,
                "limit": 20,
                "total": 2,
            },
        )
    return httpx.Response(404, json={"message": "not found"})


# What the mock server last received as PATCHed index settings.
_PATCHED_SETTINGS: dict | None = None


def _cookie_for(phone: str) -> str:
    """A signed session cookie carrying only the phone (no DB token).

    get_current_db_user falls back to the phone lookup when the
    cookie has no session token, so the test user row is enough.
    """
    resp = Response()
    create_session(resp, phone=phone)
    header = resp.headers["set-cookie"]
    return header.split(";")[0].split("=", 1)[1]


async def main_async() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def test_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # 1. Index document shape: no stock in the index -------------------------
    doc = index_document(FAKE_PRODUCTS[0])
    assert "stock" not in doc and "reserved" not in doc, doc
    assert doc["id"] == TV_ID and doc["specs"] == {"screen_size_inch": 55}, doc
    assert doc["category"] == "تلویزیون و صدا", doc
    print("   [OK] index document: no stock/reserved, specs kept")

    # 2. Full sync: index all, delete stale -----------------------------------
    fake_directus = _FakeDirectusClient()
    stale_doc = _product(STALE_ID, "کالای حذف‌شده", "", 1000, "شستشو")
    fake_meili = _FakeMeilisearch(docs=[stale_doc])
    result = await sync_products_index(fake_directus, meilisearch=fake_meili)
    assert result == {"indexed": 3, "deleted": 1}, result
    assert set(fake_meili.docs) == {TV_ID, WASHER_ID, FRIDGE_ID}, fake_meili.docs
    assert fake_meili.ensure_calls == 1, "ensure_index must run once per sync"
    assert fake_meili.settings_applied == INDEX_SETTINGS, fake_meili.settings_applied
    print("   [OK] sync: 3 indexed, 1 stale deleted, settings applied")

    # 3. Sync with no Meilisearch configured raises, never silently skips ----
    try:
        await sync_products_index(fake_directus, meilisearch=None, http=None)
    except MeilisearchError:
        print("   [OK] sync without Meilisearch raises MeilisearchError")
    else:
        raise AssertionError("sync without Meilisearch must raise")
    assert get_meilisearch_client() is None, "factory must be None unconfigured"

    # 4. Search via Meilisearch (condition filter still applies) --------------
    found = await search_products(
        fake_directus, "سامسونگ", meilisearch=fake_meili
    )
    assert fake_meili.search_calls == ["سامسونگ"], fake_meili.search_calls
    assert {row["id"] for row in found} == {TV_ID, WASHER_ID, FRIDGE_ID}, found
    found_used = await search_products(
        fake_directus, "سامسونگ", condition="used", meilisearch=fake_meili
    )
    assert [row["id"] for row in found_used] == [WASHER_ID], found_used
    print("   [OK] search via Meilisearch, condition filter applies")

    # 4b. Arabic-keyboard letters are normalised to Persian before the call.
    # The fake records the query it received: it must be the Persian form.
    found = await search_products(fake_directus, "تلويزيون", meilisearch=fake_meili)
    assert [row["id"] for row in found] == [TV_ID], found
    assert fake_meili.search_calls[-1] == "تلویزیون", fake_meili.search_calls[-1]
    print("   [OK] Arabic-keyboard query normalised to Persian before Meilisearch")

    # 5. Fallback: Meilisearch fails -> substring, same results ----------------
    failing = _FailingMeilisearch()
    found = await search_products(fake_directus, "سامسونگ", meilisearch=failing)
    assert {row["id"] for row in found} == {TV_ID, WASHER_ID, FRIDGE_ID}, found
    found = await search_products(fake_directus, "یخچال", meilisearch=failing)
    assert [row["id"] for row in found] == [FRIDGE_ID], found
    found = await search_products(fake_directus, "تلويزيون", meilisearch=failing)
    assert [row["id"] for row in found] == [TV_ID], found
    print("   [OK] fallback to substring when Meilisearch fails")

    # 6. Unconfigured -> substring (no Meilisearch call at all) ----------------
    found = await search_products(fake_directus, "لباسشویی")
    assert [row["id"] for row in found] == [WASHER_ID], found
    print("   [OK] unconfigured search falls back to substring")

    # 7. Real client against a mock transport ----------------------------------
    transport = httpx.MockTransport(_mock_handler)
    http = httpx.AsyncClient(transport=transport, base_url="http://test")
    client = MeilisearchClient(
        http=http, base_url="http://test", api_key="dummy-master-key"
    )
    assert client.is_configured
    assert await client.health()
    hits = await client.search("test", limit=5)
    assert [hit["id"] for hit in hits] == [TV_ID], hits
    await client.ensure_index()
    assert _PATCHED_SETTINGS == INDEX_SETTINGS, (
        "ensure_index must PATCH the managed settings when they differ"
    )
    await client.replace_documents([index_document(p) for p in FAKE_PRODUCTS])
    await client.delete_documents([STALE_ID])
    ids = await client.list_document_ids()
    assert ids == [TV_ID, WASHER_ID], ids
    print("   [OK] real client vs mock transport: search, ensure, replace, delete, list ids")

    # 7b. A failed task raises MeilisearchError --------------------------------
    def _failing_task_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/tasks/"):
            return httpx.Response(
                200,
                json={
                    "uid": 9,
                    "indexUid": "products",
                    "status": "failed",
                    "error": {"message": "boom", "code": "internal"},
                },
            )
        return httpx.Response(202, json={"taskUid": 9, "status": "enqueued"})

    failing_http = httpx.AsyncClient(
        transport=httpx.MockTransport(_failing_task_handler), base_url="http://test"
    )
    failing_client = MeilisearchClient(
        http=failing_http, base_url="http://test", api_key="dummy-master-key"
    )
    try:
        await failing_client.wait_for_task(9, timeout=5.0)
    except MeilisearchError as exc:
        assert "boom" in str(exc), exc
        print("   [OK] failed task raises MeilisearchError with the server message")
    else:
        raise AssertionError("failed task must raise")

    # 8. Scheduler no-op when Meilisearch is not configured --------------------
    task = SearchIndexTask(interval_seconds=300.0)
    result = await task.run_once()
    assert result.get("skipped") == 1, result
    print("   [OK] scheduler run_once is a logged no-op when unconfigured")

    # 9. Admin reindex endpoint: auth + behaviour ------------------------------
    async with Session() as s:
        s.add_all(
            [
                User(phone=STAFF_PHONE, role=UserRole.STAFF, is_active=True),
                User(phone=CUSTOMER_PHONE, role=UserRole.CUSTOMER, is_active=True),
            ]
        )
        await s.commit()

    app.dependency_overrides[get_db] = test_get_db
    app.dependency_overrides[get_directus_client] = lambda: _FakeDirectusClient()
    staff_client = TestClient(app)
    try:
        # 9a. Guest: 401.
        resp = staff_client.post("/admin/search/reindex")
        assert resp.status_code == 401, resp.status_code
        # 9b. Customer: 403.
        resp = staff_client.post(
            "/admin/search/reindex",
            cookies={SESSION_COOKIE_NAME: _cookie_for(CUSTOMER_PHONE)},
        )
        assert resp.status_code == 403, resp.status_code
        print("   [OK] reindex endpoint: 401 guest, 403 customer")

        # 9c. Staff with a fake Meilisearch: 200 + counts.
        seeded = _FakeMeilisearch(docs=[stale_doc])
        app.dependency_overrides[get_meilisearch_client_dep] = lambda: seeded
        resp = staff_client.post(
            "/admin/search/reindex",
            cookies={SESSION_COOKIE_NAME: _cookie_for(STAFF_PHONE)},
        )
        assert resp.status_code == 200, resp.status_code
        assert resp.json() == {"indexed": 3, "deleted": 1}, resp.json()
        print("   [OK] reindex endpoint: staff 200, 3 indexed, 1 deleted")

        # 9d. Staff with Meilisearch unconfigured: 503.
        app.dependency_overrides[get_meilisearch_client_dep] = lambda: None
        resp = staff_client.post(
            "/admin/search/reindex",
            cookies={SESSION_COOKIE_NAME: _cookie_for(STAFF_PHONE)},
        )
        assert resp.status_code == 503, resp.status_code
        print("   [OK] reindex endpoint: 503 when Meilisearch is not configured")
    finally:
        staff_client.close()
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_directus_client, None)
        app.dependency_overrides.pop(get_meilisearch_client_dep, None)

    print("All Meilisearch checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
