---
name: bandeh
description: Bandeh project rules — read FIRST before any code change in this repo. Branch, themes, layout, payments, tests, and what agy must never do.
---

# Bandeh — Project Rules

Read this skill FIRST, before changing any file. Repo docs in `docs/` are the detail; this file is the law.

## Workflow

- Branch is `muse`. Never touch `main`. Never commit or push — the owner pushes through the GitHub connector in one batched snapshot after local testing. Leave the working tree clean and finished; end with a summary + the list of items that still need the owner.
- New files carry the AGPL-3.0 header. Keep code simple and beginner-readable: thin routes, service = brain, `integrations/` for external clients.
- Tests for every piece: `tests/check_*.py` or `tests/test_*.py`, run with `uv run python -m unittest discover -s tests` from the repo root. Then `ruff check` and `ty check` — both must be clean. Baseline: 40 tests, 2 stable pre-existing failures (check_pages stale assertion, check_search event-loop teardown). New tests must pass; no previously-passing test may break.

## Stack and style

- FastAPI + Jinja + HTMX + Alpine.js + Tailwind + Directus + Postgres + Redis. Use the installed skills: `fastapi` (official), `tailwindcss`, `htmx`, `alpine-js`, `postgresql-best-practices`, `redis-best-practices`, `docker`, `sql-best-practices`, `sqlalchemy-async`, `security-audit` (for security reviews; guidance mode by default — full audit only when the owner asks for it), `find-skills` (to discover a new skill from skills.sh when a task needs one we do not have — but NEVER install what it finds blind: read the candidate's full SKILL.md first, check it is not dangerous, never use `-y`; report what was added in the run summary).
- SQLAlchemy 2.0 async ONLY — see the `sqlalchemy-async` skill. Never legacy patterns.
- `fastapi dev` locally, `fastapi run` in production. Docker: build context excludes `data/` (.dockerignore). Containers need explicit DNS (`--dns 8.8.8.8 --dns 1.1.1.1`) — the host uses the systemd-resolved stub.

## Design

- Mobile-first, light AND dark themes, dark default. No animation. No dropdown menus if avoidable.
- The layout must match the Craftbyte reference (`/tmp/design-refs/01-craftbyte-dark-mobile.jpg`): photo hero card, category photo tiles in a row, best-seller rows (thumbnail, name, price, star rating, round + button), fixed bottom nav (Shop, Categories, Wishlist, Account). THE COLOR DOES NOT MATTER — the amber accent stays; fix the structure, not the palette.
- Product photos live in `app/e-commerce/static/img/` (hero-fridge.jpg, product-fridge.jpg, product-tv.jpg, product-washer.jpg). Never ship a grey placeholder.

## Data and money

- Directus is the source of truth for content (products, categories). SQLAlchemy keeps only stock/reserved, search, orders, payments, users, sessions.
- Zibal is the payment gateway. Amounts in Rial; `toman_to_rial` is the single conversion. Verify is the truth — callback is only a hint. Idempotency key is ours (UUID, saved with the order). Zibal has no idempotency field.
- SMS via the Kavenegar client; dev-print fallback when no key is set. Never ask for the key.
