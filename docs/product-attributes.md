# Product attributes (Phase 3)

Short version: each product has attribute values (a TV is 55 inch, a
washer is 8 kg). The *definitions* of those attributes live in
Directus on the category; the *values* live in Directus on the
product and are copied into Postgres, where the shop filters by them.

## The model

- **Definitions** — `categories.filter_attributes` in Directus (JSON).
  One list per category; each entry is
  `{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ", "type": "number"}`.
  `key` is a snake_case identifier; `label`/`unit` are what the shop
  shows; `type` is `"number"` for now.
- **Values** — `products.specs` in Directus (JSON), e.g.
  `{"screen_size_inch": 55}`. Copied into Postgres
  `products.specs` (JSONB, `NOT NULL DEFAULT '{}'`, GIN index
  `ix_products_specs_gin`, migration `011_product_specs`).
- **Reading** — `DirectusClient.list_products` / `list_products_by_category` /
  `get_product` return `specs` (from `PRODUCT_READ_FIELDS`); the API
  exposes it as `ProductRead.specs`. `list_categories` returns
  `filter_attributes`; `get_category_filter_attributes()` returns
  `{category id: [definitions]}`.
- **Filtering (DB path)** — `services/search_service.py`:
  `clean_spec_filters` (drops bad keys, empty lists, None values),
  `apply_spec_filters` (adds `specs ->> 'key' IN (...)` per attribute,
  bound parameters only), `build_spec_filter_statement` (built, not
  run — tests compile it against the Postgres dialect) and
  `filter_products_by_specs` (runs it; Postgres only, like full-text).

## Seeded test data (Directus + Postgres)

| Product | Category | specs |
|---|---|---|
| تلویزیون ۵۵ اینچ [TEST] | تلویزیون و صدا [TEST] (2) | `{"screen_size_inch": 55}` |
| ماشین لباسشویی ۸ کیلویی [TEST] | شستشو [TEST] (3) | `{"capacity_kg": 8}` |
| یخچال دو درب [TEST] | آشپزخانه [TEST] (1) | `{"capacity_liter": 450}` — **placeholder** |

The TV size (55) and washer capacity (8) come from the product
names. **The fridge 450 litres is a seeded test value only** — the
name says "double-door" and gives no capacity. The shop owner should
correct it in Directus (edit the product's `specs` field) and re-run
the sync below.

Category definitions seeded in Directus:

- تلویزیون و صدا [TEST] → `screen_size_inch` (اندازه صفحه, اینچ)
- آشپزخانه [TEST] → `capacity_liter` (ظرفیت, لیتر)
- شستشو [TEST] → `capacity_kg` (ظرفیت, کیلوگرم)

All type `"number"`. The `[TEST]` suffix on the category names is
deliberate — keep it.

## Sync: Directus → Postgres

Directus owns the specs; Postgres is a copy for database search.
There was no existing product-sync path in the repo, so the sync is a
standalone script: `~/workspace/scripts/sync_product_specs.py`.

**Shared-table note (2026-11-22):** on the laptop, Directus and the
shop use the SAME Postgres database and the SAME `products` table —
so editing `specs` in Directus already lands in Postgres
immediately, and the sync is usually a no-op that just confirms it.
Keep the script anyway: it is the explicit re-run path if the two
ever diverge (or Directus ever points at a different database), and
it is what the tests cover. It also explains why migration 011
converts the Directus-created column instead of adding a new one.

Re-run it after any CMS content change (specs edited, new product,
new category definitions don't need a sync — definitions are read
live from Directus). Run it on the laptop, inside the web container
(Postgres has no host port):

```bash
cd /home/mhb/app/coding/easy-auto
docker compose exec -T web uv run python - < ~/workspace/scripts/sync_product_specs.py
```

It reads products over the public Directus API (no login), updates
only the `specs` column, matched by product id, and reports products
that exist in Directus but not in Postgres (it never creates rows).
Idempotent — safe to re-run any time.

## SQLite note

`postgresql.JSONB` does not run on SQLite, and the check scripts use
in-memory SQLite. The model therefore uses
`MutableDict.as_mutable(JSONB().with_variant(JSON(), "sqlite"))` —
variant **inside** `as_mutable`. The other order (as_mutable on each
side) loads plain dicts on SQLite and silently loses in-place edits.
`tests/check_product_specs.py` covers the round-trip including an
in-place edit.

One SQLite quirk, by design: `specs ->> 'key'` returns a number on
SQLite but text on Postgres, so the spec filter is Postgres-only —
the check script compiles it against the Postgres dialect instead of
running it on SQLite, exactly like the full-text statement.

## How Phase 4 will use it

The filter panel reads the current category's definitions from
`get_category_filter_attributes()`, shows one filter per definition
(label + unit), and sends the visitor's picks as
`{key: [values]}` to `filter_products_by_specs` (DB path) — or to the
equivalent Directus-side filter. No code change is needed when an
editor adds a new attribute definition in Directus.
