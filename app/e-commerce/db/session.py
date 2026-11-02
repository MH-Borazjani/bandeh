"""Async database engine, session factory, and FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import sys
from typing import Any

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
            from env_settings import get_settings

            settings = get_settings()
            db_url = settings.async_database_url
            debug_mode = settings.debug
        except Exception:
            # Fallback connection string for local dev if settings not configured
            db_url = "postgresql+asyncpg://postgres:postgres@localhost:5432/easy_auto"
            debug_mode = False

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


async def init_db() -> None:
    """Create all registered database tables (used for tests and dev setup)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


__all__ = ["get_db", "get_engine", "get_session_factory", "init_db"]
