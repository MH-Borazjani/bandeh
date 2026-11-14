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
"""Add delivery address fields to orders table

Revision ID: 005_order_delivery_address
Revises: 004_orders_payments
Create Date: 2026-11-14 13:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "005_order_delivery_address"
down_revision: str | None = "004_orders_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("recipient_name", sa.String(length=100), nullable=True))
    op.add_column("orders", sa.Column("recipient_phone", sa.String(length=20), nullable=True))
    op.add_column("orders", sa.Column("delivery_address", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "delivery_address")
    op.drop_column("orders", "recipient_phone")
    op.drop_column("orders", "recipient_name")
