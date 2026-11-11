# Zibal payments

## The flow (one fridge, start to finish)

1. Checkout freezes the cart into an `Order` (prices copied into
   `OrderItem` rows — later price changes never rewrite history).
   `total_rial` is always `total_toman × 10`, via the one named
   function `core.money.toman_to_rial`.
2. `POST /payments/{order_id}/start` reserves stock in ONE transaction
   with `SELECT ... FOR UPDATE`, creates a `PaymentAttempt` with a
   fresh uuid4 idempotency key, then asks Zibal `POST /v1/request`.
   Zibal answers a `trackId`; the browser is redirected to
   `https://gateway.zibal.ir/start/{trackId}`.
3. The customer pays at Zibal. Zibal redirects the browser to
   `GET /payments/callback?trackId&success&status&orderId`. Those
   parameters are a HINT, never the truth.
4. The callback verifies server-to-server (`POST /v1/verify`,
   no amount sent). Verify-100 + matching amount marks the order paid
   exactly once: attempt → paid (refNumber, masked card, paidAt saved),
   order → paid, reserved → sold (`stock -= qty`, `reserved -= qty`),
   all in one transaction. A second callback for the same trackId
   returns `already_paid` and changes nothing.
5. If the customer never pays, the 15-minute stock hold expires (below).

## Rules that matter

- **Zibal has NO idempotency-key field.** The key is ours (uuid4, one
  per try, UNIQUE in `payment_attempts`). It never goes on the wire.
  Starting twice for one order reuses the live waiting attempt — same
  start URL, no double reservation.
- **Verify is the truth.** Result 100 = verified, 201 = already
  verified (success without changes when already paid; the same
  exactly-once paid transition when still waiting), 202 = not paid /
  failed (release the hold, order failed), 203 = bad trackId (no state
  change; the hold expires on its own).
- **Amount mismatch after verify-100: do NOT mark paid.** Release the
  hold, mark failed. The money question goes to manual review — the
  code never guesses.
- **Gateway refusal at start (e.g. result 105):** the hold is given
  back in the same breath (compensate, then re-raise) — never leave
  stock held for a payment that never started. The route answers 502.
- **The service owns its writes and commits them.** A failed gateway
  call must never roll back its own compensation through the request
  dependency's rollback.
- **Test merchant is the literal string `"zibal"`** (same host as
  production). Set `ZIBAL_MERCHANT` in the environment for the real
  merchant id. `ZIBAL_CALLBACK_URL` sets the public callback URL;
  without it, the app uses its own `/payments/callback` (fine for
  tests, wrong for production — Zibal must be able to reach it).

## Expired holds (cron)

`release_expired_reservations` frees 15-minute holds whose customer
never paid (attempt → expired, order → expired, reserved given back).
It is a plain function, not a route — run it on a schedule:

```cron
*/5 * * * * cd /path/to/bandeh && uv run python -c "import asyncio; ..."  # see below
```

The intended invocation is a small runner that opens a DB session and
calls `services.payment_service.release_expired_reservations` (the
function commits itself and returns how many holds it freed). No
scheduler lives in the app yet — add the runner + cron line at deploy
time, or call the function from the future background worker.

## What is NOT here yet (honest notes)

- No Zibal `POST /v1/inquiry` call (status-only, not verification) —
  nothing in the flow needs it yet.
- The `FOR UPDATE` locks are real on Postgres and ignored on SQLite
  (SQLite has no row locks), so the tests cannot prove the race
  behaviour — only the logic. The exactly-once guarantee also rests on
  the paid-status early return, which the double-callback test covers.
- The callback answers JSON. A shop front-end will want a redirect to
  an order-status page instead — that is a template change, not a
  service change.

## Tests

`python check_payments.py` — in-memory SQLite + httpx MockTransport
(a fake Zibal). Covers order totals, reservation, idempotent start,
paid exactly once on double callback, the ignored hint, verify-201,
amount mismatch, verify-202, ownership (404), insufficient stock,
gateway refusal (502 + compensation), expired-hold release, cancel,
unknown trackId, and the status endpoint.
