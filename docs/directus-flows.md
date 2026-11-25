# Directus Flows — one dashboard, logic in code

## The idea

ALL logic lives in the shop code (FastAPI + SQLAlchemy). Directus is
only the shell that shows the data and triggers actions. Directus
holds no business logic.

- **Data** — Directus shows it: products, categories, settings,
  users, orders, order items, stock.
- **Logic** — the shop code does it: payments, stock reservations,
  order status, tracking SMS, search re-index.
- **Actions** — a Directus Flow (webhook) calls the shop, and the
  shop does the work. Directus displays the result.

This is why there is only one dashboard. The shop's own admin pages
stay for nothing except as a fallback; everyday control happens in
Directus.

## What lives in Directus

| Collection | What the admin sees | What the admin can change |
|---|---|---|
| `products`, `categories`, `site_settings`, `trust_badges`, `legal_pages`, `certification_badges`, `mcp_guide_sections` | Content | Everything (this is the CMS) |
| `users` | Phone, role, name, Google email | Role (customer / staff / admin dropdown). `google_sub` is hidden. |
| `orders` | Who bought, what, how much, status, tracking code | Nothing — all fields readonly. Status changes only via a Flow. |
| `order_items` | Product, quantity, frozen price | Nothing — frozen at order time. |
| `product_stock` | Stock and reserved per product | `stock` only. `reserved` is readonly (the shop manages it). |

Public access to `users`, `orders`, `order_items`, `product_stock`
is blocked (403). Only the Directus admin role sees them.

## The internal endpoints

`app/e-commerce/api/routes/internal.py` — prefix `/internal`:

- `POST /internal/directus/orders/{order_id}/status`
  Body: `{"status": "shipped" | "delivered", "tracking_code": "..."}`.
  Updates the order and, with the same guard as the staff admin
  endpoint, sends the tracking SMS ONLY the first time a tracking
  code is set (order had no code before, new status is `shipped`).
- `POST /internal/directus/search/reindex`
  Rebuilds the Meilisearch products index from Directus (same as
  the staff admin endpoint and the 5-minute timer).

Auth is a shared secret, not a cookie and not a user session:

- Header: `X-Directus-Flow-Secret: <secret>`
- Secret: env var `DIRECTUS_FLOW_SECRET` (a long random string,
  per server, stored only in the server `.env`, never in git).
- No secret configured → 503 (the endpoints are off).
- Missing or wrong secret → 401. Compared in constant time.
- The secret is a `SecretStr` and is never logged.

## The Flows (set up in the Directus panel, per server)

1. **Mark shipped** — manual trigger with a form (order id +
   tracking code), or an "item update" trigger on `orders` that
   fires only when the admin asks for it. The Flow calls
   `POST {shop}/internal/directus/orders/{id}/status` with the
   secret header. The shop updates the status and sends the SMS.
   The status field is readonly in Directus, so the Flow is the
   only path that changes it — editing the raw field can never
   skip the SMS.
2. **Reindex search** — manual trigger. The Flow calls
   `POST {shop}/internal/directus/search/reindex` with the secret
   header. The shop rebuilds the Meilisearch index.

Why a Flow instead of editing the field directly: editing the
`status` field in Directus writes the database and skips the shop
code, so the tracking SMS would never fire. The Flow calls the
shop, the shop does the work, Directus shows the result. Data and
action stay in sync.

## Tests

`tests/check_internal_directus.py` (8 checks, in-memory SQLite,
mocked SMS panel): 503 with no secret, 401 on missing/wrong
secret, 404 on unknown order, mark-shipped happy path (200 + one
tracking SMS), once-only SMS guard (second ship: no SMS; order
with a preset code: no SMS), reindex 503 when Meilisearch is
unconfigured. Full suite green (`tests/test_checks_runner.py`).
