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
"""Admin-only endpoints.

The authorization lives in the dependency, never in an if inside the
route: ``RequireAdminDep`` raises 401 for guests and 403 for customers,
so the handler only ever runs for an admin. The overview is small on
purpose — a first admin page that proves the gate works over HTTP.
"""

from __future__ import annotations

from core.security import RequireAdminDep
from db.session import DbSessionDep
from fastapi import APIRouter
from sqlalchemy import func, select

from db.models.session import UserSession
from db.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def admin_overview(admin: RequireAdminDep, db: DbSessionDep) -> dict:
    """Admin dashboard numbers: total users and live (unrevoked) sessions."""
    total_users = await db.scalar(select(func.count()).select_from(User))
    live_sessions = await db.scalar(
        select(func.count())
        .select_from(UserSession)
        .where(UserSession.is_revoked == False)  # noqa: E712 — SQLAlchemy needs == False here
    )
    return {
        "admin": admin.phone,
        "total_users": total_users or 0,
        "live_sessions": live_sessions or 0,
    }


__all__ = ["router"]
