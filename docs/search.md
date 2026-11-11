# Search

## What exists

- **ILIKE search** (`services/search_service.py`,
  `search_products_ilike`) — substring search over name + description,
  active products only. Works on every database and catches partial
  words, which is what Persian shoppers type.
- **Postgres full-text** (`build_fulltext_statement`,
  `search_products_fulltext`) — `to_tsvector('simple', ...)` +
  `plainto_tsquery('simple', ...)` matched with `@@`, ordered by
  `ts_rank`. `simple` is the honest config: Postgres has no Persian
  stemmer, so `simple` just tokenises. On Postgres the route tries
  full-text first and falls back to ILIKE when it finds nothing; on
  other databases it uses ILIKE directly. The statement is built
  separately from execution so tests can compile it against the
  Postgres dialect without a live Postgres.
- **One rule for both** — the user's words are always a bound
  parameter, never pasted into SQL. `%` and `_` inside the query are
  escaped, so they match literally.
- **Always limited** — default 20, max 50, enforced in the service
  (and in the route's `Query` bounds). An empty query returns `[]`,
  never a full-table dump.
- **Routes** (`api/routes/products.py`) — `GET /search?q=...&limit=...`
  and `GET /products` (active products, newest first). The list sends
  `Cache-Control: public, max-age=60`: the list is the same for every
  visitor, so browsers and CDNs may cache it. (This is the product-list
  header the caching topic planned.)

## Known next step (not done here)

The full-text query uses `to_tsvector` as an expression, so Postgres
cannot use an index for it yet. When the catalog grows, add a stored
generated `search_vector` column with a GIN index — one migration, and
the query switches to `Product.search_vector.op("@@")(...)`.

## Tests

`python check_search.py` — in-memory SQLite. Covers name/description
search, inactive exclusion, literal `%`/`_`, empty query, the 50 cap,
an injection attempt (table intact afterwards), the list + cache
header, and the full-text SQL compiled for Postgres with the query text
as a bound parameter (never inline).
