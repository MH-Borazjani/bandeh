# Agent Commerce Protocols — ACP + UCP on Genabay

Genabay speaks two agentic commerce protocols, so an AI agent can
search the shop, build a cart, and start a checkout without opening
one page. Both are built on the shop's own order and Zibal code — no
new payment system, no new order system.

## The two protocols

- **ACP** (OpenAI + Stripe, spec 2026-04-17): the checkout protocol
  for agents that talk to a merchant API. Endpoints under `/acp`.
- **UCP** (Google, spec 2026-08-25): the merchant profile at
  `/.well-known/ucp` plus the shopping service under `/ucp`. This is
  the one Gemini and Google AI Mode use.

## What an agent can do

1. **Discover** — `GET /.well-known/ucp` (public): the shop's
   capabilities, the checkout/catalog/orders services, and the
   payment handler `ir.genabay.payments.zibal`.
2. **Search the catalog** — `POST /ucp/catalog/search|lookup|product`
   (UCP). Prices are in Rial (`irr`).
3. **Create a checkout session** — `POST /acp/checkout_sessions`
   (201) or `POST /ucp/checkout-sessions` (200), with line items
   (the shop's product UUIDs) and an optional buyer.
4. **Update it** — reprice, change items, add the buyer and the
   delivery address.
5. **Complete it** — the shop creates a real order and starts a real
   Zibal payment, and returns the Zibal payment link.

## What an agent CANNOT do

- **Charge anything.** A delegated payment token (Stripe SPT, Google
  Pay token) is never charged and never forwarded to Zibal — only
  the provider/handler name is recorded. The answer carries the
  Zibal start URL, and the buyer pays on the bank page. This is the
  shop's standing never-auto-pay rule.
- **Log in as a customer.** Agents cannot do the shop's OTP login.
  The buyer is a guest user, created by phone
  (`get_or_create_user_by_phone`). The order is unpaid until the
  buyer pays.
- **Cancel a paid order or change an address after the fact** — the
  protocols only cancel an open session.

## Money

- All amounts are **Rial** (`irr`). Toman has no ISO code; the
  shop's own pages still show Toman to people.
- Delivery is one option, amount 0 — the shop has no shipping fee
  configured. Totals are subtotal + total only.
- A session lives **6 hours** (the UCP default). An expired session
  answers `expired` (ACP) / `canceled` (UCP) with a session_expired
  message.

## Auth, idempotency, signatures

- `AGENT_API_KEY` (env): `Authorization: Bearer <key>` is required
  on every `/acp/*` and `/ucp/*` call. Discovery
  (`/.well-known/ucp`) and the payment-handler schema stay public.
  **If the variable is unset, the endpoints refuse everything
  (401) — never open by accident.**
- ACP requires an `Idempotency-Key` on every POST (400 without it).
  UCP accepts one when sent. Same key + same body replays the
  stored answer with `Idempotent-Replayed: true`; same key +
  different body is a 422.
- `ACP_SIGNING_SECRET` (env, optional): when set, POSTs must carry
  `Signature` = base64 HMAC-SHA256(secret,
  `timestamp + "." + request_id + "." + raw_body`) with `Timestamp`
  within 5 minutes. The spec requires the header but leaves the
  canonical string to the merchant — this is ours. Responses and
  order webhooks are signed the same way.
- `ACP_WEBHOOK_URL` / `UCP_WEBHOOK_URL` (env, optional): the agent's
  order-events endpoint. The shop POSTs `order_create` /
  `order_update` there (HMAC signed, 10s timeout, best effort).
  Without a URL the event is recorded on the session as undelivered.

## Where the code lives

- `db/models/agent_session.py` — the `agent_checkout_sessions`
  table (migration `007_agent_checkout_sessions`).
- `services/agent_commerce.py` — the whole flow: sessions, pricing,
  guest users, order + Zibal start, webhooks, manifest, catalog.
- `api/routes/acp.py`, `api/routes/ucp.py` — the two routers.
- `api/routes/pages.py` — `GET /.well-known/ucp`.
- `tests/check_agent_commerce.py` — the checks (in-memory SQLite +
  fake Zibal): manifest, auth, totals, catalog, complete with a
  Zibal link, foreign-handler rejection, SPT token never stored or
  forwarded, idempotency replay/conflict, cancel rules, webhooks.

## Honest limits

- Payment is a **link**, not a charge: Zibal has no token charging,
  and the protocols' Stripe/Google Pay tokens are not usable here.
  Real agent payment (charging a token) needs an international PSP
  later, when the shop sells internationally.
- The shop is not deployed yet, so no outside agent can reach these
  endpoints until deploy (genabay.ir + HTTPS).
- The official ACP/UCP conformance suites were not run — the checks
  above verify the shop's own behaviour against the spec's rules.
