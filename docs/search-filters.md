# Search and category filters (Phase 4)

Short version: the category page and `/search` both have a filter
panel. Picking a value narrows the product grid without a full page
reload, and the URL always carries the filters, so a copied link
reproduces the same view.

## Where the panel lives

- **Category page** (`/category/{id}`): the panel shows that
  category's attribute filters plus the shared filters (price,
  condition, in-stock).
- **`/search`**: the panel shows the same shared filters, plus the
  attribute filters of the picked category — but only when exactly
  one category is picked (see the rule below).

The panel is one shared template, `templates/_filter_panel.html`,
used by both pages. It submits with HTMX (`hx-get`, `hx-target="main"`,
`hx-push-url="true"`, `hx-trigger="change, submit"`): every change
swaps the product grid in place and pushes the new URL. The panel
state comes from the URL params on every render, so it keeps working
when HTMX re-renders it — there is no JavaScript-only state.

**Clear all** («پاک کردن فیلترها») goes to the bare category URL
(`/category/{id}`) or, on `/search`, to `/search` keeping only the
text query, and resets the panel.

## The param contract

| Param | Meaning |
|---|---|
| `spec_<key>=<value>` (repeatable) | Attribute pick, e.g. `spec_screen_size_inch=55`. One checkbox per distinct value. Several values for the same key mean "any of them"; different keys combine with AND. |
| `price_min`, `price_max` | Price bounds in Toman (integers). A non-number is ignored. |
| `condition=new` / `condition=used` | Only offered when the site sells used goods (`site.settings.sells_used`); anything else means "all". On `/search` the condition lives in the chip row above the panel, and the panel carries it as a hidden input so changing another filter never drops it. |
| `in_stock=1` | Only products with `available > 0`. |

The result count next to the grid always reflects the filtered list.

## Shared filters vs attribute filters

- **More than one category picked** (or none, on `/search`): only
  the shared filters show — price, condition, in-stock.
- **Exactly one category picked**: that category's attribute
  filters show too, next to the shared ones.

Changing the picked categories resets the attribute picks (the
category chips do not carry `spec_*` params); the price, condition
and in-stock picks survive a category change.

## How attribute values are derived

The definitions come from Directus, live, per category
(`categories.filter_attributes`, read via
`get_category_filter_attributes()` — Phase 3). The checkbox values
are **not hardcoded**: they are the distinct values actually present
in the `specs` of that category's products (e.g. the TVs on sale are
55 and 65 inch, so the TV panel offers exactly those two). A
definition with no values among the category's products shows
nothing. On `/search` the values come from all of the category's
products, not just the ones matching the text query, so typing a
query never shrinks the checkboxes. Adding a new attribute definition
or a new product value in Directus needs no code change.

## How filtering is applied

- **Primary path (Directus product dicts):** the route filters the
  product dicts in Python (`_matches_product_filters` in
  `api/routes/products.py`), using `clean_spec_filters` from
  `services/search_service.py` (Phase 3) to parse the `spec_*`
  params. Spec values compare as strings, because Directus specs
  hold numbers (55) while the URL carries text ("55").
- **Database fallback path:** when Directus returns nothing, the
  category page falls back to the same table through SQLAlchemy. On
  Postgres the spec narrowing runs in the database
  (`filter_products_by_specs`, Phase 3); on SQLite the Python
  matcher does it, because SQLite's `specs ->> 'key'` returns a
  number where Postgres returns text (see `docs/product-attributes.md`).
  Price and in-stock are applied by the same Python matcher on both
  paths, so the two paths agree.

## No brand filter

There is deliberately **no brand filter**. Products carry no brand
field — no brand data exists to filter by — so a brand checkbox
would have nothing to match against. If a brand field is ever added
to products, it can join the shared filters the same way price does.

## Tests

`tests/check_filters.py` (in-memory SQLite + TestClient, same
pattern as the other `check_*.py` scripts): param parsing, the
matcher, distinct-value derivation, the category panel per category
(TV screen size, washer capacity, fridge 450 L), spec / price /
in-stock / condition filtering, the count, clear-all (bare category
URL; on `/search` it keeps only the text query), the no-match empty
state, the `/search` single- vs multi-category rule, the JSON
`/search` branch, and the DB spec statement compiled against the
Postgres dialect.
