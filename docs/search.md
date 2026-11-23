# Search

## What exists

- **Directus search** (`services/search_service.py`,
  `search_products`) — substring search over name + description,
  active products only. It reads the catalogue from Directus
  (`list_products`, at most 50 rows) and matches in Python: it works
  on every database and catches partial words, which is what Persian
  shoppers type. There is no SQL here at all — no ILIKE, no Postgres
  full-text (both were removed when Directus became the single
  product source, 2026-11-24). The query text is never pasted into
  SQL because there is no SQL to paste it into.
- **Always limited** — default 20, max 50, enforced in the service
  (and in the route's `Query` bounds). An empty query returns `[]`,
  never a full-table dump.
- **Routes** (`api/routes/products.py`) — `GET /search?q=...&limit=...`
  and `GET /products` (active products, newest first). The list sends
  `Cache-Control: public, max-age=60`: the list is the same for every
  visitor, so browsers and CDNs may cache it. (This is the product-list
  header the caching topic planned.) Stock numbers on every result
  come from the shop's `product_stock` table
  (`services/stock_service.attach_stock`), not from the Directus rows.

## Multi-select categories

Both search routes accept `category` as a **repeatable** query param —
one value per picked category:

```
GET /search?category=یخچال و فریزر&category=تلویزیون و صدا
GET /api/search-live?q=سامسونگ&category=یخچال و فریزر&category=تلویزیون و صدا
```

- The route resolves **each** value on its own (a numeric value that
  matches a known category id becomes that category's name; anything
  else passes through unchanged, so an unknown category matches
  nothing). Matching is membership: a product shows when its category
  is in the picked set; an empty set means "no category filter".
- Category filtering is Directus-side, in Python against the CMS rows
  — there is no database search anymore, so every search path (text,
  category, filter-only) reads Directus the same way. An unknown
  category matches nothing.
- The URL is the state, so it is shareable: the search page's category
  chips are toggles (click adds/removes one category), the "همه دسته‌ها"
  chip clears all of them, the form carries one hidden input per
  picked category, and the results header shows one badge per picked
  category. The live search "see all results" link carries every
  picked category as its own repeated param. The header panel does the
  same: its chips toggle independently, "همه" clears all, and the form
  submits repeated `category` params (the live dropdown includes them
  on every keystroke). Condition stays single-select.
- **Shared-filters rule** — when more than one category is picked,
  only filters shared by all of them show (price, condition,
  in-stock); per-category attribute filters show only when exactly
  one category is picked. The `/search` template context exposes this
  as `single_category` (True only when exactly one category is picked)
  alongside `selected_categories` (the resolved list); the search form
  also carries it as `data-single-category` for the client. The
  attribute panel itself is built on this state — see
  `docs/search-filters.md` (Phase 4). There is deliberately no brand
  filter: products carry no brand field, so there is no brand data to
  filter by.

## Known next step (not done here)

The full-text query uses `to_tsvector` as an expression, so Postgres
cannot use an index for it yet. When the catalog grows, add a stored
generated `search_vector` column with a GIN index — one migration, and
the query switches to `Product.search_vector.op("@@")(...)`.

## Tests

`python tests/check_search.py` — in-memory SQLite. Covers name/description
search, inactive exclusion, literal `%`/`_`, empty query, the 50 cap,
an injection attempt (table intact afterwards), the list + cache
header, the full-text SQL compiled for Postgres with the query text
as a bound parameter (never inline), multi-select categories
(two categories return both and none of a third, text + set, single
category, id resolution, unknown matches nothing, live search with
two categories + the see-all link), and the home search box
(button above the chips, first-3 + "more" toggle, advanced link,
and the link carrying query + categories + condition via shop.js).
