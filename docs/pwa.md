# PWA for the Bandeh shop

Plain-language doc. No code reading needed — this explains what was built,
why, and how the shop and Directus stay in sync.

## What a PWA is (one paragraph)

A PWA makes the website behave like an app on the phone: an icon on the
home screen, full-screen opening (no browser bar), and fast repeat visits
even on a slow connection. It is three small additions, not a new stack:

1. **Manifest** (`static/manifest.webmanifest`) — the app's ID card: name
   (گنابِی), icon, colors, start page. The browser reads it to offer
   "Add to Home Screen".
2. **Service worker** (`static/sw.js`) — a small script the browser runs in
   the background. It saves files on the phone and decides what to show
   when the network is slow or gone.
3. **HTTPS** — the browser only trusts a service worker on HTTPS (or on
   localhost). On the laptop it works now; on the real domain it works as
   soon as Caddy gives the server HTTPS at deploy time. Nothing else to do.

## What was built

- Manifest + 4 icons (192, 512, 512 maskable, 180 Apple touch), dark
  background #121110 with an amber G — the shop's own colors.
- Service worker served from the site root (`/sw.js`). Root matters: a
  worker under `/static/` could only control `/static/*`, so the routes
  `/sw.js` and `/manifest.webmanifest` were added in `api/routes/pages.py`,
  next to `robots.txt` and `sitemap.xml`.
- `base.html` links the manifest, sets the theme color, and registers the
  worker after the page loads. Only the full page does this — HTMX
  fragments never load it twice.

## Cache rules (the important part)

| Thing | Rule | Why |
|---|---|---|
| CSS, JS, icons, photos | Saved copy first, refreshed in the background | These change rarely; repeat visits feel instant |
| Public pages (home, products, categories, search) | Show the saved page at once, fetch a fresh copy in the background | Fast on slow internet, fresh on the next visit |
| Cart, checkout, orders, payments, login/account, every `/api/` call | Always network, never saved | Money and private data must never come from a stale copy |
| HTMX fragments | Always network, never saved | Fragments are pieces of a live page, not pages |
| No network at all | A small Persian offline page ("اینترنت نیست") | The shop never shows a blank browser error |

## How the shop and Directus stay in sync

The browser never talks to Directus. The server reads products from
Directus on every request (with a short 60-second server cache), so:

- A product changed in Directus shows up on the next visit at the latest.
  The worker only ever saves a *page*, never treats it as truth — the
  background refresh replaces it right away.
- New product photos have new file names, so the worker fetches them fresh
  the first time and saves them after that.
- Nothing in the worker needs a Directus password or key. The worker only
  sees the same public pages a visitor sees.

## Forcing every phone to forget the old cache

If the design changes a lot, bump `CACHE_VERSION` in `static/sw.js` by one
(`genabay-pwa-v1` → `genabay-pwa-v2`) and redeploy. The new worker deletes
every old `genabay-*` cache on activation.

## What is NOT included (on purpose)

- **Push notifications** — needs a push server and a subscription store.
  A separate job, only if he wants it.
- **Offline buying** — a shop cannot take money offline. The cart and
  checkout always need the network, and the offline page says so.

## Tests

`tests/test_pwa.py` checks the contract: manifest served and valid, icons
exist, worker served from the root with `no-cache`, money routes listed as
network-only in the worker, and the landing page links the manifest and
registers the worker.
