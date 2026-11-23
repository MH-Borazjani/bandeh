# File uploads

## What exists

- **Stock** (`db/models/product_stock.py`) — products live in
  Directus now; the shop's database keeps only the stock numbers:
  `stock` (owned) and `reserved` (held for unpaid orders; what a
  customer can buy is `stock - reserved`). CHECK constraints keep the
  numbers honest in the database, not just in Python. Migration
  `013_product_stock`. Staff set the number with
  `PATCH /products/{id}/stock` (`services/stock_service.set_stock`).
- **Uploads** (`services/upload_service.py`) — `save_upload` enforces
  the three rules: server-made UUID filenames (no part of the client's
  filename survives), magic-byte content check (a `.png` that is
  actually a script is rejected), and chunked reading with a 5 MB
  limit (a too-big file leaves no partial file behind). Allowed:
  `.jpg`, `.jpeg`, `.png`, `.webp`. Errors: 415 wrong type, 400 fake
  content, 413 too big. No route calls it anymore — product photos
  are attached in Directus, not uploaded through the shop — but the
  validator is kept for a future Directus-aware upload.
- **Routes** (`api/routes/products.py`) — `PATCH /products/{id}/stock`
  is staff/admin only (`RequireStaffDep`, never an if in the route);
  `GET /products/{id}` and `GET /uploads/{filename}` are public.
  Photos are served from `uploads/` through a route that checks the
  resolved path stays inside the upload dir — never through the static
  file server, and never from `static/`.
- **Background work** (`services/image_tasks.py`) — a stub on purpose,
  and currently unused: the photo upload endpoint that scheduled the
  resize work is gone (photos live in Directus). The stub records the
  job so the seam is real and testable, but no pixels are resized yet.
  A real worker (arq, Celery, or a separate process) replaces the list
  later, if an upload route ever comes back.
- **API schemas** (`schema/product_api.py`) — the Directus-backed shapes
  (int Toman, UUID ids). The teaching `schema/product.py` (Decimal,
  SKU, int ids) is a lesson file and is left untouched.

## Notes

- `uploads/` is git-ignored — user photos are data, not code.
- The upload dir is `BANDEH_UPLOAD_DIR` or `app/e-commerce/uploads`;
  the route reads it through a dependency so tests can point it at a
  temp dir.

## Tests

`python tests/check_uploads.py` — in-memory SQLite + temp dir. Covers the
stock endpoint gate (staff 200, customer 403, guest 401, unknown 404,
below reserved 422), the validator (PNG round-trip, server-made names,
413 with no partial file, 415/400 rejections), and serving (bytes +
content type, missing 404, traversal 404s).
