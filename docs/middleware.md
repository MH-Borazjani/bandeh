# Middleware

Middleware runs for **every request**, before any route sees it. Bandeh has
three, registered in `core/middleware.py` and switched on by
`register_middleware(app)` in `main.py`.

## The three, in execution order

1. **Logging + timing** — one line per request: method, path, status, and
   how many milliseconds it took. It also adds an `X-Process-Time` header
   to the response. Only the method, path, status and duration are logged —
   never headers, cookies or bodies, because those can carry secrets.
2. **CORS** — answers which other websites may call the API from a browser.
   The shop pages are same-origin (Jinja served by this app), so CORS
   mainly matters for the future API door and local frontends. Allowed
   origins come from the `BANDEH_CORS_ORIGINS` environment variable
   (comma-separated); the default allows local development origins only.
3. **Security headers** — attached to every response:
   `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a
   Content-Security-Policy, `Referrer-Policy`, and
   Strict-Transport-Security. The CSP allows the Tailwind Play CDN and
   HTMX CDN plus `'unsafe-inline'` for now, because the landing page loads
   Tailwind from the CDN. When Tailwind is built to a static file, tighten
   the CSP: drop `'unsafe-inline'` and serve everything from `'self'`.

## The order rule (easy to get wrong)

Starlette runs middleware in the **reverse** order they are added: the last
one added runs first. So `register_middleware` adds them innermost-first
(security headers, then CORS, then logging) to get the execution order
above. If you add a new middleware, think about where it must run, then
add it in the matching position.

## Tests

`python check_middleware.py` — checks the security headers, the timing
header, one access-log line, a CORS preflight, an allowed cross-origin
request, and a rejected origin. No server, no database.
