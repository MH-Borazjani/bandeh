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
"""Database package exposing Base, session dependencies, and models."""

from db.base import Base, TimestampMixin
from db.models import User, UserRole, UserSession
from db.session import get_db, get_engine, get_session_factory, init_db

__all__ = [
    "Base",
    "TimestampMixin",
    "User",
    "UserRole",
    "UserSession",
    "get_db",
    "get_engine",
    "get_session_factory",
    "init_db",
]
