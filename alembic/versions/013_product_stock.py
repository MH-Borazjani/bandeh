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
"""Add product_stock table (Directus source-of-truth refactor)

Revision ID: 013_product_stock
Revises: 012_sms_log
Create Date: 2026-11-24 01:30:00.000000

Why: Directus now owns ALL product content — name, description,
price, photo, condition, attributes. The shop's SQLAlchemy keeps
only the two numbers that need a real database transaction: stock
and reserved (see db/models/product_stock.py). When two people buy
the last fridge at the same time, the database must lock the row;
the Directus API has no lock, so stock cannot live in Directus.

The table is keyed by the Directus product UUID as plain text: the
shop never creates products anymore, it only tracks stock for
products Directus already has. The backfill below copies the live
stock numbers from the products table, so no sale is lost in the
move. The products table itself is NOT touched by this migration —
Directus still owns it (same Postgres database, same table).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_product_stock"
down_revision: str | None = "012_sms_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_stock",
        sa.Column("directus_product_id", sa.String(length=36), nullable=False),
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("stock >= 0", name="ck_product_stock_stock_nonnegative"),
        sa.CheckConstraint(
            "reserved >= 0", name="ck_product_stock_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "reserved <= stock", name="ck_product_stock_reserved_le_stock"
        ),
        sa.PrimaryKeyConstraint("directus_product_id"),
    )
    # Backfill: the live stock numbers move from the products table
    # into product_stock, keyed by the product id as text. A product
    # with no row here has zero stock after the refactor.
    op.execute(
        "INSERT INTO product_stock "
        "(directus_product_id, stock, reserved, created_at, updated_at) "
        "SELECT id::text, stock, reserved, created_at, updated_at "
        "FROM products"
    )


def downgrade() -> None:
    op.drop_table("product_stock")
