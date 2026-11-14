# Purchase path (stage 2)

The full way a buyer goes from a cart to a paid order. Mobile-first, stage 1
design system, dark default, light toggle.

## The flow

1. **Cart page** (`GET /cart`, `templates/cart.html`): reads the cart draft
   from localStorage (`bandeh-cart-draft`, `{id, name, qty}`, no prices).
   Each line shows the name, a quantity stepper, the line price and a remove
   button. The total shows in Persian digits with the تومان label. An empty
   cart shows an empty state.
2. **Checkout page** (`GET /checkout`, `templates/checkout.html`): a form for
   recipient name, recipient phone and delivery address. Submitting calls
   `POST /orders` (also reachable as `POST /checkout`), then starts the Zibal
   payment (`POST /payments/{id}/start`) and redirects the buyer to Zibal.
3. **Payment result** (`GET /payments/result`, `templates/payment_result.html`):
   shown after the buyer returns from Zibal. Success and failure states, with
   the order number, the Zibal tracking code and next steps.

## Delivery address (migration 005)

The Order model had no delivery address. Migration
`005_order_delivery_address` adds three columns to `orders`:
`recipient_name` (varchar 100), `recipient_phone` (varchar 20),
`delivery_address` (varchar 500). The fields are part of `OrderCreate` and
`OrderRead` and are saved by `create_order` in the order service.

## Callback: HTML for browsers, JSON for APIs

`GET /payments/callback` answers HTML when the request comes from a browser
(`Accept: text/html` or `?format=html`) and keeps returning JSON for API
callers. The JSON shape is unchanged.

## Settings note

Alembic runs against the synchronous URL. `sync_database_url` now names the
driver explicitly (`postgresql+psycopg2`) so the migration runs the same way
everywhere.

## Tests

- `tests/test_purchase_path.py`: 6 tests — cart page 200, checkout page 200,
  order created with an address in the database, payment result renders the
  success state and the failure state, JSON callback still returns JSON.
