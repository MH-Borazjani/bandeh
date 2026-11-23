# Product attributes (Phase 3)

Short version: each product has attribute values (a TV is 55 inch, a
washer is 8 kg). The *definitions* of those attributes live in
Directus on the category; the *values* live in Directus on the
product. The shop reads both from Directus and filters in Python —
there is no Postgres copy anymore (the old sync script and the
database spec filter were removed 2026-11-24, when Directus became
the single product source).

## The model

- **Definitions** — `categories.filter_attributes` in Directus (JSON).
  One list per category; each entry is
  `{"key": "screen_size_inch", "label": "اندازه صفحه", "unit": "اینچ", "type": "number"}`.
  `key` is a snake_case identifier; `label`/`unit` are what the shop
  shows; `type` is `"number"` for now.
- **Values** — `products.specs` in Directus (JSON), e.g.
  `{"screen_size_inch": 55}`. The `products` table still has a
  `specs` column (Directus owns that table), but the shop never reads
  it from the database — it reads `specs` from the Directus API.
- **Reading** — `DirectusClient.list_products` / `list_products_by_category` /
  `get_product` return `specs` (from `PRODUCT_READ_FIELDS`); the API
  exposes it as `ProductRead.specs`. `list_categories` returns
  `filter_attributes`; `get_category_filter_attributes()` returns
  `{category id: [definitions]}`.
- **Filtering (Python)** — `services/search_service.py`:
  `clean_spec_filters` (drops bad keys, empty lists, None values).
  The actual match is `_matches_product_filters` in
  `api/routes/products.py`: the product's value for each picked
  attribute must be one of the picked values (IN per key, AND across
  keys), compared as strings because Directus specs hold numbers (55)
  while the URL carries text ("55").

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

## No sync anymore

There used to be a sync script (`~/workspace/scripts/sync_product_specs.py`)
that copied Directus specs into the Postgres `products.specs` column,
because the shop filtered specs in the database. That copy is gone:
since 2026-11-24 the shop reads specs from the Directus API and
filters in Python, so there is nothing to sync. Do NOT run the old
script — it writes to the Directus-owned `products.specs` column and
would overwrite what the editors put in Directus. The script file
still exists in `~/workspace/scripts/`; retire it when convenient.

## SQLite note

The old Product model round-tripped specs on SQLite
(`MutableDict.as_mutable(JSONB().with_variant(JSON(), "sqlite"))` —
variant **inside** `as_mutable`). That model is gone now, and with it
the round-trip test. Specs are plain dicts from the Directus API.
## How Phase 4 will use it

The filter panel reads the current category's definitions from
`get_category_filter_attributes()`, shows one filter per definition
(label + unit), and sends the visitor's picks as `{key: [values]}`;
the route filters the Directus rows in Python
(`_matches_product_filters`). No code change is needed when an editor
adds a new attribute definition in Directus.
