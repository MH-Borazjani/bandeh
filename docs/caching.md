# Caching

A cache is a saved copy of an answer. Instead of asking the database (or
Directus) again, the app keeps a copy and gives the copy.

## Two backends, one interface (`core/cache.py`)

- **Redis** (`RedisCache`) — the real cache in production. `compose.yaml`
  runs redis:7; point at it with `REDIS_URL`
  (default `redis://localhost:6379`).
- **Memory** (`MemoryCache`) — a dict with expiry timestamps. Used in
  tests, and as the automatic fallback: if Redis is unreachable,
  `get_cache()` logs a warning and returns a memory cache. A failed Redis
  must never take the shop down — the fallback only costs speed, never
  correctness.

Both implement `get` / `set` / `delete` / `delete_by_prefix`, so callers
never know which backend they use.

## Rules

- Cache only data that is safe to show again: public content, never a
  user's cart, account, or order.
- Every value has a TTL. Nothing lives forever.
- When the data changes, **delete the key** (delete-on-change) instead of
  waiting for the TTL. A stale price is worse than a slow page.

## What is cached today

- **Landing content** — `DirectusClient` caches successful reads for 60
  seconds (`LANDING_CONTENT_KEY`, `LANDING_CONTENT_TTL`). Failures are
  never cached, so the next request retries Directus right away.
  `invalidate_landing_content(cache)` drops the key immediately; call it
  when content changes (the Directus webhook receiver, once it exists).
- **Landing page** — `Cache-Control: private, max-age=60`. `private`
  because the page header depends on login state: only the visitor's own
  browser may cache it, never a shared proxy.

## Tests

`python check_cache.py` — key names, memory cache round-trip/expiry/
prefix delete, the Redis wrapper against a fake client, `get_cache`
falling back to memory when Redis is down, landing content served from
cache on the second call and re-fetched after invalidation, and the
landing route's `Cache-Control` header. No server, no live Redis, no
live Directus, no database.
