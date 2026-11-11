# File uploads

## What exists

- **Product model** (`db/models/product.py`) — created here, because a
  photo needs something to attach to. Money is int Toman. Stock is two
  numbers: `stock` (owned) and `reserved` (held for unpaid orders;
  what a customer can buy is `stock - reserved`). CHECK constraints
  keep the numbers honest in the database, not just in Python.
  Migration `003_products`.
- **Uploads** (`services/upload_service.py`) — `save_upload` enforces
  the three rules: server-made UUID filenames (no part of the client's
  filename survives), magic-byte content check (a `.png` that is
  actually a script is rejected), and chunked reading with a 5 MB
  limit (a too-big file leaves no partial file behind). Allowed:
  `.jpg`, `.jpeg`, `.png`, `.webp`. Errors: 415 wrong type, 400 fake
  content, 413 too big.
- **Routes** (`api/routes/products.py`) — `POST /products` and
  `POST /products/{id}/photo` are staff/admin only (`RequireStaffDep`,
  never an if in the route); `GET /products/{id}` and
  `GET /uploads/{filename}` are public. Photos are served from
  `uploads/` through a route that checks the resolved path stays
  inside the upload dir — never through the static file server, and
  never from `static/`.
- **Background work** (`services/image_tasks.py`) — a stub on purpose.
  The route schedules thumbnail/webp work with FastAPI
  `BackgroundTasks` and returns at once; the stub records the job so
  the seam is real and testable, but no pixels are resized yet. A real
  worker (arq, Celery, or a separate process) replaces the list later.
- **API schemas** (`schema/product_api.py`) — the DB-backed shapes
  (int Toman, UUID ids). The teaching `schema/product.py` (Decimal,
  SKU, int ids) is a lesson file and is left untouched.

## Notes

- `uploads/` is git-ignored — user photos are data, not code.
- The upload dir is `BANDEH_UPLOAD_DIR` or `app/e-commerce/uploads`;
  the route reads it through a dependency so tests can point it at a
  temp dir.

## Tests

`python tests/check_uploads.py` — in-memory SQLite + temp dir. Covers the
staff gate, the PNG round-trip, server-made names, 413 with no partial
file, 415/400 rejections, traversal 404s, and the queued resize job.
