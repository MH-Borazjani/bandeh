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
