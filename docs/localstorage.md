# localStorage

## What exists

- **Cart draft** (`static/js/shop.js`, `BandehStore`) — tapping
  "افزودن به سبد" stores `{id, name, qty}` under
  `bandeh-cart-draft` and updates the badge in the header. It is a
  DRAFT, not the cart: the server owns the real cart (cart topic) and
  checkout re-checks everything there. The draft stores NO prices —
  prices live on the server. Sample products have no UUID yet, so the
  draft id is `sample-<index>` for now; the real catalog will use
  product UUIDs.
- **Theme choice** — `bandeh-theme`, `"dark"` or `"warm"`. Both are
  dark (white hurts eyes); warm is a warmer dark. A tiny inline
  script in `base.html` applies the saved theme before first paint
  (no flash of the wrong dark); the toggle button switches it.
- **The allowlist** — `safeSet` refuses any key not in
  `ALLOWED_KEYS` (`bandeh-cart-draft`, `bandeh-theme`). Login state
  and anything the server must trust can never be stashed in browser
  storage, even by future code, by accident.
- **Wiring** — `/static` is mounted in `main.py`; the landing header
  has the theme toggle + cart badge, and each product button carries
  `data-add-to-cart` / `data-product-id` / `data-product-name`.

## Tests

`python check_localstorage.py` — no browser here, so it checks what
can be checked without one: the landing wires the toggle/badge/
buttons, `shop.js` is served and is valid JavaScript (`node
--check`), the allowlist holds, no login-related word appears in the
script, and the warm theme contains no white. The behaviour itself
(click → draft grows) is for a real browser.
