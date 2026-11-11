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
"""Runnable self-check for core/cache.py and cached landing content.
Run from repo root:

    python tests/check_cache.py

No server, no live Redis, no live Directus, no database: the in-memory
cache and httpx.MockTransport stand in for the real services.
"""

import asyncio
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from core.cache import (
    MemoryCache,
    RedisCache,
    get_cache,
    make_key,
    reset_cache_for_tests,
)
from fastapi.testclient import TestClient
from integrations.directus_client import (
    DirectusClient,
    invalidate_landing_content,
)
from integrations.http_client import create_http_client, get_http_client
from main import app


class _FakeRedis:
    """A tiny in-memory stand-in for redis.asyncio.Redis (tests the wrapper)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._store[key] = (value, time.monotonic() + ex if ex else None)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                removed += 1
            self.deleted.append(key)
        return removed

    async def scan_iter(self, match: str):
        import fnmatch

        for key in list(self._store):
            if fnmatch.fnmatch(key, match):
                yield key


def _directus_ok(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/items/categories"):
        return httpx.Response(
            200, json={"data": [{"name": "یخچال", "description": "ساید و دوقلو"}]}
        )
    if request.url.path.endswith("/items/products"):
        return httpx.Response(
            200, json={"data": [{"name": "یخچال تست", "price": 45000000, "tag": None}]}
        )
    return httpx.Response(404, json={"errors": [{"message": "not found"}]})


def main() -> None:
    # 1. Key namespacing.
    assert make_key("landing", "content") == "bandeh:landing:content"

    # 2. MemoryCache: round-trip, expiry, delete, delete_by_prefix.
    async def _memory() -> None:
        cache = MemoryCache()
        await cache.set("bandeh:a:1", "one", 60)
        await cache.set("bandeh:a:2", "two", 60)
        await cache.set("bandeh:b:1", "three", 60)
        assert await cache.get("bandeh:a:1") == "one"
        assert await cache.get("missing") is None
        await cache.set("bandeh:short", "x", 0.05)
        await asyncio.sleep(0.1)
        assert await cache.get("bandeh:short") is None, "expired keys must miss"
        removed = await cache.delete_by_prefix("bandeh:a:")
        assert removed == 2, removed
        assert await cache.get("bandeh:a:1") is None
        assert await cache.get("bandeh:b:1") == "three"
        await cache.delete("bandeh:b:1")
        assert await cache.get("bandeh:b:1") is None

    asyncio.run(_memory())

    # 3. RedisCache wrapper logic against the fake client (no live Redis).
    async def _redis_wrapper() -> None:
        fake = _FakeRedis()
        cache = RedisCache(fake)
        await cache.set("bandeh:x:1", "v", 60)
        assert await cache.get("bandeh:x:1") == "v"
        await cache.set("bandeh:x:2", "v", 60)
        removed = await cache.delete_by_prefix("bandeh:x:")
        assert removed == 2, removed
        assert await cache.get("bandeh:x:1") is None

    asyncio.run(_redis_wrapper())

    # 4. get_cache falls back to memory when Redis is unreachable.
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6399"
    reset_cache_for_tests()

    async def _fallback() -> None:
        cache = await get_cache()
        assert isinstance(cache, MemoryCache), type(cache)
        await cache.set("bandeh:probe", "ok", 60)
        assert await get_cache() is cache, "get_cache must return one instance"
        assert await cache.get("bandeh:probe") == "ok"

    asyncio.run(_fallback())
    reset_cache_for_tests()
    os.environ.pop("REDIS_URL", None)

    # 5. Landing content is cached 60s: the second call never hits Directus.
    calls = {"count": 0}

    def _counting(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _directus_ok(request)

    async def _cached_landing() -> None:
        cache = MemoryCache()
        transport = httpx.MockTransport(_counting)
        async with create_http_client(transport=transport) as http:
            client = DirectusClient(http, cache=cache)
            first = await client.get_landing_content()
            second = await client.get_landing_content()
            assert first == second
            assert calls["count"] == 2, calls  # one call per collection, once
            # Delete-on-change: after invalidation the next call hits Directus again.
            await invalidate_landing_content(cache)
            third = await client.get_landing_content()
            assert third == first
            assert calls["count"] == 4, calls

    asyncio.run(_cached_landing())

    # 6. The landing route sends a Cache-Control header (private: the
    # header depends on login state, so only the visitor's browser caches).
    async def _mock_http_client() -> httpx.AsyncClient:
        return create_http_client(transport=httpx.MockTransport(_directus_ok))

    app.dependency_overrides[get_http_client] = _mock_http_client
    try:
        with TestClient(app) as test_client:
            r = test_client.get("/")
            assert r.status_code == 200, r.status_code
            assert r.headers["Cache-Control"] == "private, max-age=60", r.headers
            assert "یخچال تست" in r.text, r.text
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    print("All cache checks passed.")


if __name__ == "__main__":
    main()
