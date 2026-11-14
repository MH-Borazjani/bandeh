# Frontend stage 3: my orders, search and trust pages

The last frontend stage. Mobile-first, stage 1 design system, dark default,
light toggle.

## My orders

- `GET /orders` (`templates/orders.html`): for a signed-in buyer, the newest
  orders first, with a status badge, the total in Toman (Persian digits) and
  the Tehran date, each linking to the detail view. Guests get a 303 redirect
  to `/auth/login`.
- `GET /orders/{order_id}` and `GET /order/{order_id}`
  (`templates/order_detail.html`): the frozen order items (name, quantity,
  unit price, line total), the recipient name, phone and delivery address,
  and the payment state (gateway, Zibal tracking code, and a pay button when
  unpaid). JSON stays available with `Accept: application/json` or
  `?format=json`.
- `list_orders_for_user` in the order service reads the buyer's orders from
  Postgres.

## Search and category pages

- `GET /search` (`templates/search.html`): a search box, a category filter
  and results from the existing search service and Directus. A browser gets
  HTML; an API caller (`Accept: application/json`) still gets the JSON
  `list[ProductRead]`.
- `GET /category/{identifier}` (`templates/category.html`): the category
  title and description with its products from Directus (database fallback).
  The identifier can be the category id or its name — the landing page links
  use the name.
- `list_categories` and `list_products_by_category` are new Directus client
  methods; the category name map from stage 1 is reused for the product rows.

## Trust pages

`GET /terms`, `/privacy`, `/returns`, `/contact` — simple pages in the design
system, all linked from the shared footer. The header gained a search shortcut
and account links; the account page links to "سفارش‌های من".

## Tests

- `tests/test_frontend_stage3.py`: 10 tests — my orders 200 for a signed-in
  buyer, 303 for a guest, order detail 200 and 303, search page 200 with and
  without a query, JSON search API still working, category page 200, trust
  pages 200, footer contains the trust links.
