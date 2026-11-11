# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Product search: ILIKE first, Postgres full-text second.

Two implementations, one rule for both: the user's words are ALWAYS a
bound parameter, never pasted into SQL. ``%`` and ``_`` inside the
query are escaped, so they match literally instead of acting as
wildcards.

- ILIKE: substring search over name + description. Works on every
  database, catches partial words (what Persian shoppers type).
- Full-text (Postgres only): ``to_tsvector`` + ``plainto_tsquery``
  with the ``simple`` config (no stemming — Postgres has no Persian
  stemmer; ``simple`` just tokenises) and ``ts_rank`` ordering. Used
  when the database is Postgres; when it finds nothing, ILIKE runs as
  the fallback. On other databases the route uses ILIKE directly.

Both paths are always limited (default 20, max 50) — an unbounded
search is a full-table scan handed to strangers.
"""

from __future__ import annotations

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.product import Product

DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_QUERY_LENGTH = 200


def _normalise_limit(limit: int) -> int:
    """Clamp the limit: at least 1, at most MAX_LIMIT."""
    return min(max(limit, 1), MAX_LIMIT)


def _escape_like(query: str) -> str:
    """Escape LIKE wildcards so % and _ in the query match literally."""
    return (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )


def _clean_query(query: str) -> str:
    """Strip and cap the query. Empty string means 'no search'."""
    return query.strip()[:MAX_QUERY_LENGTH]


async def search_products_ilike(
    db: AsyncSession, query: str, limit: int = DEFAULT_LIMIT
) -> list[Product]:
    """Substring search (name + description), active products only.

    The pattern is a bound parameter — SQLAlchemy sends ``%...%`` as a
    value, never as SQL text. Wildcards inside the query are escaped.
    """
    clean = _clean_query(query)
    if not clean:
        return []
    pattern = f"%{_escape_like(clean)}%"
    stmt = (
        select(Product)
        .where(
            Product.is_active == True,  # noqa: E712 — SQLAlchemy needs == True here
            or_(
                Product.name.ilike(pattern, escape="\\"),
                Product.description.ilike(pattern, escape="\\"),
            ),
        )
        .order_by(Product.created_at.desc())
        .limit(_normalise_limit(limit))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


def build_fulltext_statement(
    query: str, limit: int = DEFAULT_LIMIT
) -> Select[Product, float]:
    """Postgres full-text statement (built, not run — see below).

    Separated from execution so tests can compile it against the
    Postgres dialect and inspect the SQL without a live Postgres.
    ``plainto_tsquery`` turns the user's words into a query; the
    ``@@`` operator matches it against the document vector; ``ts_rank``
    orders best matches first. The query text stays a bound parameter.
    """
    clean = _clean_query(query)
    vector = func.to_tsvector(
        "simple",
        Product.name + " " + func.coalesce(Product.description, ""),
    )
    ts_query = func.plainto_tsquery("simple", clean)
    return (
        select(Product, func.ts_rank(vector, ts_query).label("rank"))
        .where(
            Product.is_active == True,  # noqa: E712
            vector.op("@@")(ts_query),
        )
        .order_by(func.ts_rank(vector, ts_query).desc())
        .limit(_normalise_limit(limit))
    )


async def search_products_fulltext(
    db: AsyncSession, query: str, limit: int = DEFAULT_LIMIT
) -> list[Product]:
    """Run the full-text statement. Postgres only — callers check first."""
    if _clean_query(query) == "":
        return []
    result = await db.execute(build_fulltext_statement(query, limit))
    return [row[0] for row in result.all()]


async def search_products(
    db: AsyncSession, query: str, limit: int = DEFAULT_LIMIT
) -> list[Product]:
    """The route's one search call: full-text on Postgres, ILIKE elsewhere.

    On Postgres, full-text runs first and ILIKE is the fallback when it
    finds nothing (a substring can match where token search cannot).
    """
    if _clean_query(query) == "":
        return []
    is_postgres = db.bind.dialect.name == "postgresql" if db.bind else False
    if is_postgres:
        found = await search_products_fulltext(db, query, limit)
        if found:
            return found
    return await search_products_ilike(db, query, limit)


async def list_products(
    db: AsyncSession, limit: int = DEFAULT_LIMIT
) -> list[Product]:
    """Active products, newest first, always limited."""
    stmt = (
        select(Product)
        .where(Product.is_active == True)  # noqa: E712
        .order_by(Product.created_at.desc())
        .limit(_normalise_limit(limit))
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_QUERY_LENGTH",
    "build_fulltext_statement",
    "list_products",
    "search_products",
    "search_products_fulltext",
    "search_products_ilike",
]
