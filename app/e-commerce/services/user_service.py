"""User service layer for account retrieval, creation, and profile management."""

from __future__ import annotations

import datetime
import uuid

from db.models.user import User, UserRole
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    """Retrieve an active user record by normalized phone number."""
    stmt = select(User).where(User.phone == phone)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Retrieve a user by unique UUID primary key."""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_or_create_user_by_phone(db: AsyncSession, phone: str) -> tuple[User, bool]:
    """Retrieve existing user or create a new Customer account if first login.

    Returns:
        tuple[User, bool]: (user, created_flag)
    """
    user = await get_user_by_phone(db, phone)
    if user is not None:
        return user, False

    user = User(
        phone=phone,
        role=UserRole.CUSTOMER,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user, True


async def record_user_login(db: AsyncSession, user: User) -> None:
    """Update last login timestamp."""
    user.last_login_at = datetime.datetime.now(datetime.timezone.utc)
    await db.flush()


__all__ = [
    "get_or_create_user_by_phone",
    "get_user_by_id",
    "get_user_by_phone",
    "record_user_login",
]
