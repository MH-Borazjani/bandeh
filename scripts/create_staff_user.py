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
"""CLI utility to create or promote the first staff or admin user."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

# Ensure repository root and app/e-commerce are discoverable on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app" / "e-commerce"
for p in (REPO_ROOT, APP_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from db.models.user import UserRole
from db.session import get_session_factory
from services.user_service import create_or_promote_staff_user


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or promote a staff or admin user for Bandeh back-office."
    )
    parser.add_argument(
        "phone",
        help="Iranian mobile phone number (e.g. 09123456789 or +989123456789)",
    )
    parser.add_argument(
        "--role",
        choices=["admin", "staff"],
        default="admin",
        help="Role to assign: 'admin' (overview & orders) or 'staff' (order fulfillment)",
    )
    parser.add_argument("--first-name", default=None, help="First name of the staff member")
    parser.add_argument("--last-name", default=None, help="Last name of the staff member")

    args = parser.parse_args()
    role_enum = UserRole.ADMIN if args.role == "admin" else UserRole.STAFF

    factory = get_session_factory()
    async with factory() as session:
        user = await create_or_promote_staff_user(
            db=session,
            phone=args.phone,
            role=role_enum,
            first_name=args.first_name,
            last_name=args.last_name,
        )
        print(f"Successfully configured user {user.phone} with role '{user.role.value}'.")
        print(f"User ID: {user.id}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
