---
name: sqlalchemy-async
description: SQLAlchemy 2.0 async-only rules for this project. Use when writing or changing any database code, models, sessions, or migrations. NEVER use legacy 1.x patterns.
---

# SQLAlchemy 2.0 Async — Project Rules

This project uses SQLAlchemy 2.0 async ONLY. Legacy patterns are forbidden, not even as examples.

## The only patterns

- Models: `class Product(Base)` with `Mapped[...]` and `mapped_column(...)`. Never `Column` in a mapped class.
- Read: `select()` (a standalone function, NOT a session method) → `await db.execute(stmt)` → `.scalar()` / `.scalars()` / `.scalar_one_or_none()`.
- `scalar()` unwraps the FIRST column. `scalar_one_or_none()` = "at most one row" — use it to catch bugs where a query returns more than one row.
- `await db.get(Product, 42)` = primary-key shortcut, returns the object.
- Write: `db.add(obj)` → `await db.flush()` (only when you need the generated id before commit) → `await db.commit()`. Commit flushes by itself; flush before commit is only for the id. flush = pencil, commit = pen.
- Session factory: `async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False)`. With autoflush=False, call `await db.flush()` after add() for generated ids.
- One session per REQUEST, delivered by the `get_db` generator (FastAPI advances it, injects the session, resumes after the response for cleanup). Calling `get_db()` runs nothing.
- Never one long shared session; shared state lives in committed DB rows (or Redis), never in a session.
- Keep transactions short. Never do SMS/HTTP calls inside a transaction; commit first, then send.
- `db.add()` is slower and returns the object WITH its id; `insert()` is bulk-fast and returns no objects/ids. `product.stock -= qty` IS an update.

## NEVER

- `session.query(...)` (1.x) — never write it, never show it as a bad example.
- `db.execute` without await. A sync session. `Column` in a mapped class.
- `with` does not commit; close only cleans up. Failure before commit = work thrown away.

## Migrations

- Alembic, async env. New revision: `alembic revision --autogenerate -m "..."`, then read the generated file by hand before `alembic upgrade head`. Every new file carries the AGPL-3.0 header.
