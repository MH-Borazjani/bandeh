/* Genabay shop service worker (PWA offline shell).
 *
 * What it does, in plain words:
 * - The shop shell (CSS, JS, icons) is saved on the phone, so repeat
 *   visits open fast even on a slow connection.
 * - Public pages (landing, products, categories, search) use
 *   stale-while-revalidate: the saved page shows at once, and the
 *   worker quietly fetches a fresh copy in the background for next time.
 * - Anything with live data is NEVER saved: cart, checkout, orders,
 *   payments, login/account, and every /api call always go to the
 *   network. Prices and stock are only ever read fresh from the server.
 * - Product content comes from Directus through the server on every
 *   request, so a product changed in Directus shows up on the next
 *   visit at the latest. The worker never treats a saved page as truth.
 *
 * To force every phone to drop its old cache (for example after a big
 * design change), bump CACHE_VERSION below by one and redeploy.
 */

const CACHE_VERSION = "genabay-pwa-v2";
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const PAGES_CACHE = `${CACHE_VERSION}-pages`;
const IMAGES_CACHE = `${CACHE_VERSION}-images`;
const ALL_CACHES = [SHELL_CACHE, PAGES_CACHE, IMAGES_CACHE];

// Files saved on the phone at install time: the shell only, never pages
// (a page header depends on login state, so pages are cached on visit).
const PRECACHE_URLS = [
  "/static/css/tailwind.css",
  "/static/js/shop.js",
  "/static/img/icons/icon-192.png",
  "/static/img/icons/icon-512.png",
];

// URL starts that must always go to the network, never the cache:
// money, login, private data, and every live API call.
const NETWORK_ONLY_PREFIXES = [
  "/auth/",
  "/cart",
  "/checkout",
  "/orders",
  "/payments/",
  "/api/",
  "/admin/",
];

function isNetworkOnly(url) {
  return NETWORK_ONLY_PREFIXES.some((prefix) => url.pathname.startsWith(prefix));
}

function isStaticAsset(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("genabay-") && !ALL_CACHES.includes(key))
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

function offlinePage() {
  return new Response(
    `<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">` +
      `<meta name="viewport" content="width=device-width, initial-scale=1">` +
      `<title>گنابِی — بدون اینترنت</title></head>` +
      `<body style="background:#121110;color:#f5f5f4;font-family:sans-serif;` +
      `text-align:center;padding:48px 24px">` +
      `<h1>اینترنت نیست</h1>` +
      `<p>به اینترنت وصل نیستید. فروشگاه با برگشتن اینترنت دوباره باز می‌شود.</p>` +
      `<p><a href="/" style="color:#d97706">تلاش دوباره</a></p>` +
      `</body></html>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live data and HTMX fragments: network only, never stored.
  if (isNetworkOnly(url)) return;
  if (request.headers.get("HX-Request")) return;

  // Static files (CSS, JS, photos, data): saved copy first, network
  // in the background to refresh it.
  if (isStaticAsset(url)) {
    const cacheName = url.pathname.startsWith("/static/img/")
      ? IMAGES_CACHE
      : SHELL_CACHE;
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(cacheName).then((cache) => cache.put(request, copy));
          }
          return response;
        });
        return cached || network;
      }),
    );
    return;
  }

  // Public pages: saved page at once, fresh copy fetched in the
  // background for the next visit. No saved page and no network
  // means the offline page.
  if (request.mode === "navigate") {
    event.respondWith(
      caches.open(PAGES_CACHE).then((cache) =>
        cache.match(request).then((cached) => {
          const network = fetch(request)
            .then((response) => {
              if (response.ok) {
                cache.put(request, response.clone());
              }
              return response;
            })
            .catch(() => cached || offlinePage());
          return cached || network;
        }),
      ),
    );
  }
});
