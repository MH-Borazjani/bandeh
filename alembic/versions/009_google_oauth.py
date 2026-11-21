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
"""009: Google OAuth login — link a Google account to a shop user.

Adds ``users.google_sub`` (Google's stable user id, unique) and
``users.google_email`` so a customer can log in with Google next to the
phone OTP login. Phone stays required: a Google login without a phone
number asks for the phone + OTP once, then links it.
"""

import sqlalchemy as sa
from alembic import op

revision = "009_google_oauth"
down_revision = "008_product_condition"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("google_sub", sa.String(255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("google_email", sa.String(255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_users_google_sub", "users", ["google_sub"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_email")
    op.drop_column("users", "google_sub")
