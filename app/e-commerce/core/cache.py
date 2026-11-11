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
"""Cache layer: Redis when it is reachable, in-memory fallback when not.

Why two backends: Redis is the real cache in production (compose.yaml
runs redis:7), but tests and local development must never REQUIRE a live
Redis. Both backends share one small interface — ``get`` / ``set`` /
``delete`` / ``delete_by_prefix`` — so callers never know which one they
are using, and tests run entirely on the in-memory backend.

Rules callers must follow:
- Cache only data that is safe to show again: public content, never a
  user's cart, account, or order.
- Every cached value has a TTL (time to live). Nothing lives forever.
- When the underlying data changes, delete the key (delete-on-change)
  instead of waiting for the TTL. A stale price is worse than a slow page.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Annotated, Protocol

from fastapi import Depends

logger = logging.getLogger("bandeh.cache")


class Cache(Protocol):
    """The small interface every cache backend implements."""

    async def get(self, key: str) -> str | None:
        """Return the cached value, or None on a miss (or an expired key)."""
        ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store a value that expires after ``ttl_seconds``."""
        ...

    async def delete(self, key: str) -> None:
        """Remove one key (a no-op when the key does not exist)."""
        ...

    async def delete_by_prefix(self, prefix: str) -> int:
        """Remove every key starting with ``prefix``; return how many were removed."""
        ...


def make_key(*parts: str) -> str:
    """Build a namespaced cache key, e.g. ``bandeh:landing:content``."""
    return ":".join(["bandeh", *parts])


class MemoryCache:
    """In-memory cache: a dict with expiry timestamps.

    Used in tests and as the automatic fallback when Redis is unreachable.
    One instance per process; values die with the process (which is fine —
    a cache is never the source of truth).
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    async def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = (value, time.monotonic() + ttl_seconds)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def delete_by_prefix(self, prefix: str) -> int:
        doomed = [key for key in self._store if key.startswith(prefix)]
        for key in doomed:
            del self._store[key]
        return len(doomed)


class RedisCache:
    """Redis-backed cache (``redis.asyncio``). Production backend."""

    def __init__(self, client) -> None:
        # The client type is redis.asyncio.Redis; kept untyped here so this
        # module imports cleanly even when the redis package is absent.
        self._client = client

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def delete_by_prefix(self, prefix: str) -> int:
        # SCAN (not KEYS): never blocks the Redis server on a big keyspace.
        removed = 0
        async for key in self._client.scan_iter(match=f"{prefix}*"):
            await self._client.delete(key)
            removed += 1
        return removed


_cache: Cache | None = None


async def _build_cache() -> Cache:
    """Try Redis once; fall back to memory when it is unreachable.

    A failed Redis must never take the shop down — the fallback only costs
    speed, never correctness.
    """
    url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        import redis.asyncio

        client = redis.asyncio.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        await client.ping()
        logger.info("Cache backend: Redis at %s", url)
        return RedisCache(client)
    except Exception as exc:  # noqa: BLE001 - any Redis failure means fallback
        logger.warning("Redis unavailable (%s), using in-memory cache", exc)
        return MemoryCache()


async def get_cache() -> Cache:
    """Dependency: return the process-wide cache (built once, lazily)."""
    global _cache
    if _cache is None:
        _cache = await _build_cache()
    return _cache


def reset_cache_for_tests() -> None:
    """Drop the cached backend (tests only) so the next get_cache rebuilds."""
    global _cache
    _cache = None


# Reusable FastAPI dependency alias for the cache
CacheDep = Annotated[Cache, Depends(get_cache)]


__all__ = [
    "Cache",
    "CacheDep",
    "MemoryCache",
    "RedisCache",
    "get_cache",
    "make_key",
    "reset_cache_for_tests",
]
