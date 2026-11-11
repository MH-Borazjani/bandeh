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
"""Pydantic schemas for user accounts and sessions."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field

from db.models.user import UserRole


class UserBase(BaseModel):
    """Base user data attributes."""

    phone: str = Field(pattern=r"^09\d{9}$", description="Canonical Iranian mobile number (09xxxxxxxxx)")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class UserRead(UserBase):
    """Public user profile schema."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: UserRole
    is_active: bool
    created_at: datetime.datetime
    last_login_at: datetime.datetime | None = None


class UserProfileUpdate(BaseModel):
    """Schema for updating user profile details."""

    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class SessionRead(BaseModel):
    """Schema for user active sessions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    expires_at: datetime.datetime
    created_at: datetime.datetime
    user_agent: str | None = None
    ip_address: str | None = None


__all__ = ["SessionRead", "UserBase", "UserProfileUpdate", "UserRead"]
