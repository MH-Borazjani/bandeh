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
"""Add product attribute values (specs JSONB + GIN index)

Revision ID: 011_product_specs
Revises: 010_mcp_server_cart
Create Date: 2026-11-22 12:00:00.000000

Why: the shop filters products by attributes (TV screen size, washer
capacity, fridge volume). The attribute *definitions* live in Directus
(categories.filter_attributes); the per-product *values* live in
Directus products.specs and are synced into this column (see
scripts/sync_product_specs.py). Phase 4 reads the definitions for the
filter panel and filters by these values.

``specs`` is JSONB, not a separate table: attributes differ per
category and editors add new ones in Directus without code changes.
The GIN index makes ``specs ->> 'key'`` lookups fast. Existing rows
get ``{}`` (no attributes yet) via the backfill below.

Shared-table wrinkle: Directus manages the SAME ``products`` table,
so adding the ``specs`` field in the CMS already creates the column —
as nullable ``json``, no default. This migration therefore does not
blindly add the column: it adds it only if missing, converts whatever
is there to ``jsonb``, backfills NULLs to ``{}``, and then sets the
default + NOT NULL. On a fresh database (no Directus field yet) the
same statements create the column as JSONB from scratch.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_product_specs"
down_revision: str | None = "010_mcp_server_cart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # See the docstring: Directus may already own this column (as
    # nullable json). ADD IF NOT EXISTS + convert covers both orders:
    # migration-first on a fresh DB, and CMS-field-first on a live one.
    op.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS specs JSONB")
    op.execute("UPDATE products SET specs = '{}' WHERE specs IS NULL")
    op.execute(
        "ALTER TABLE products ALTER COLUMN specs TYPE JSONB USING specs::jsonb"
    )
    op.execute(
        "ALTER TABLE products ALTER COLUMN specs SET DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE products ALTER COLUMN specs SET NOT NULL")
    op.create_index(
        "ix_products_specs_gin",
        "products",
        ["specs"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    # The column stays: Directus owns it (its products.specs field
    # reads/writes the same column). Only the index and the strictness
    # this migration added are removed.
    op.drop_index("ix_products_specs_gin", table_name="products")
    op.execute(
        "ALTER TABLE products ALTER COLUMN specs DROP DEFAULT"
    )
    op.execute("ALTER TABLE products ALTER COLUMN specs DROP NOT NULL")
    op.execute(
        "ALTER TABLE products ALTER COLUMN specs TYPE JSON USING specs::json"
    )
