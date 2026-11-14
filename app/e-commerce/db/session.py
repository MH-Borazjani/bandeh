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
"""Async database engine, session factory, and FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.base import Base

# Lazy initialization of engine so importing models/schemas doesn't trigger connections
_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return or create singleton async database engine."""
    global _engine
    if _engine is None:
        try:
            try:
                from env_settings import get_settings
            except ModuleNotFoundError:
                import sys
                from pathlib import Path

                repo_root = Path(__file__).resolve().parents[3]
                if str(repo_root) not in sys.path:
                    sys.path.insert(0, str(repo_root))
                from env_settings import get_settings

            settings = get_settings()
            db_url = settings.async_database_url
            debug_mode = settings.debug
        except Exception as exc:
            raise RuntimeError(
                f"Failed to load application settings for database engine: {exc}"
            ) from exc

        _engine = create_async_engine(
            db_url,
            echo=debug_mode,
            future=True,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return or create singleton async sessionmaker."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an isolated transaction-scoped AsyncSession."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Reusable FastAPI dependency alias for transaction-scoped database session
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]


async def init_db() -> None:
    """Create all registered database tables (used for tests and dev setup)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["DbSessionDep", "get_db", "get_engine", "get_session_factory", "init_db"]
