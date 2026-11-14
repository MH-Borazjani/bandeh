# Frontend design system (stage 1)

Mobile-first shop frontend in the Craftbyte dark style, warmed up for home
appliances. Two themes, dark (default) and light, with a toggle in the header.

## Themes

CSS variables in `templates/base.html`. Dark is the default (white hurts eyes):

- Dark: warm near-black background `#121110`, cards `#1c1a17`, amber gold
  accent `#d97706`, text `#f5f5f4`.
- Light: warm off-white `#faf8f5`, white cards `#fefefe`, dark text `#1c1917`,
  same amber accent.

The toggle stores the choice in localStorage under `bandeh-theme`. A small
script in the page head reads it before paint, so there is no white flash.
No dropdown menus, no animation anywhere.

## Shared pieces

- `templates/base.html`: header (brand, theme toggle, cart counter badge, auth
  links), footer, and the component classes: `.theme-btn-accent`,
  `.theme-btn-secondary`, `.theme-badge`, `.theme-input`, cards, price display.
- `static/js/shop.js`: theme switching and the cart draft
  (`bandeh-cart-draft`, `{id, name, qty}`, no prices) plus the quantity stepper.
- `templates/landing.html`: the catalog now uses the shared header/footer and
  the design system cards. Product cards link to the product page.

## Product page

- `GET /product/{id}` renders `templates/product.html`: photo or placeholder,
  name, description, category name, tag badge, stock badge, price in Persian
  digits with the تومان label, quantity stepper, Add to Cart (localStorage draft).
- `GET /products/{id}` keeps returning JSON, except when the browser asks for
  HTML (`Accept: text/html` or `?format=html`) — then it renders the same page.
- The category is resolved to its real name by `DirectusClient.get_category_map`
  (Directus sends the category relation as an integer id). The map is cached in
  memory and in Redis for 300 seconds.

## Backend fixes shipped with stage 1

- The app could not reach Postgres from the web container: `get_engine` in
  `db/session.py` swallowed every settings error and silently fell back to a
  `localhost` URL. `env_settings.py` lives at the repo root and was not
  importable from the app directory. Fixed with `PYTHONPATH=/app:/app/app/e-commerce`
  in `compose.yaml` and the `dockerfile`, plus a retry import in `get_engine`,
  which now raises a loud `RuntimeError` instead of the silent fallback.
  A missing product now returns 404 instead of 500, and the database fallback
  works when Directus is down.

## Tests

- `tests/test_product_page.py`: 5 tests (HTML page 200 + name + price, controls
  and badges, database fallback, JSON route unchanged, missing product 404).
- `tests/test_health.py`: 1 test for `GET /health`.
- `tests/test_hardening.py`: 1 test that the Directus client resolves an integer
  category id to the category name.
