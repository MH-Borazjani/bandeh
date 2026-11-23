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
"""Drop the product foreign keys (Directus source-of-truth refactor)

Revision ID: 014_remove_product_model
Revises: 013_product_stock
Create Date: 2026-11-24 01:40:00.000000

Why: the SQLAlchemy Product model is gone — Directus owns product
content now, and the shop's SQLAlchemy keeps only product_stock
(migration 013). The order_items and cart_items tables still carry
their product_id UUIDs (they must: orders freeze history, carts
point at products), but the database must no longer enforce them
against a table SQLAlchemy no longer manages.

What this migration does NOT do, on purpose: it does NOT drop the
``products`` table. Directus manages that table in this same
Postgres database — dropping it would destroy every product in the
CMS. The table stays, owned by Directus; only the shop's foreign
keys to it go away. The stock/reserved columns on that table are
dead (the shop reads stock from product_stock); removing those two
columns from the Directus collection is a live-admin follow-up, not
a migration.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "014_remove_product_model"
down_revision: str | None = "013_product_stock"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres auto-names these constraints {table}_{column}_fkey.
    # IF EXISTS so the migration is safe to re-run.
    op.execute(
        "ALTER TABLE order_items "
        "DROP CONSTRAINT IF EXISTS order_items_product_id_fkey"
    )
    op.execute(
        "ALTER TABLE cart_items "
        "DROP CONSTRAINT IF EXISTS cart_items_product_id_fkey"
    )


def downgrade() -> None:
    # The products table still exists (Directus owns it), so the
    # foreign keys can come back with their original rules.
    op.create_foreign_key(
        "order_items_product_id_fkey",
        "order_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "cart_items_product_id_fkey",
        "cart_items",
        "products",
        ["product_id"],
        ["id"],
        ondelete="CASCADE",
    )
