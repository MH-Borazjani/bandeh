# httpx and Directus

When Bandeh needs data from another server, **your server is the client**.
httpx is the tool for that job.

## One shared client (`integrations/http_client.py`)

`create_http_client()` builds an `httpx.AsyncClient` with the project's
timeout (10 seconds) and pool limits. One client is created at startup by
the app lifespan in `main.py` and shared by every request, so connections
are reused. The `get_http_client` dependency returns it (and lazily
creates one if the lifespan did not run, for example under a plain
`TestClient`). Tests pass an `httpx.MockTransport` to the factory, so no
real network call is ever made.

## Directus client (`integrations/directus_client.py`)

`DirectusClient` reads the shop's content — `categories` and `products`
collections, published rows only — and maps them to the shape the landing
template expects. Prices stored as Toman integers are formatted to
Persian digits (`format_toman_price`).

**The fallback rule:** if Directus is down, slow, returns an error, or
returns a body with the wrong shape, `get_landing_content()` returns the
hardcoded fallback content and logs a warning. The landing page never
goes blank because the CMS is unreachable. The fallback is the same data
the shop showed before Directus existed.

The landing route (`GET /`) is async and reads its content through the
`DirectusClientDep` dependency.

## Configuration

- `DIRECTUS_URL` — Directus base URL (default `http://localhost:8055`,
  the service from `compose.yaml`).
- `DIRECTUS_TOKEN` — optional bearer token for non-public collections.

## Tests

`python tests/check_httpx.py` — factory timeout, price formatting, healthy
Directus mapping, fallback on dead/sick/confused Directus, and the
landing page rendering both Directus rows and fallback content. No
server, no live Directus, no database.
