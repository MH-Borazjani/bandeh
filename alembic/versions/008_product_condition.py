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
"""Add product condition (new / used) + cleanliness stars

Revision ID: 008_product_condition
Revises: 007_agent_checkout_sessions
Create Date: 2026-11-20 21:45:00.000000

Why: the shop sells second-hand appliances too. A used product carries
its condition on the card — «دست دوم» plus 1–5 stars for how clean it
is (5 = like new, 1 = a wreck) — and search can filter new vs used.
A site that sells new only hides the whole flow with the
``sells_used`` switch in site settings (see services/site_content.py).

The columns live in the same ``products`` table Directus manages, so
the CMS adopts them (create the matching fields in Directus AFTER this
migration is applied: ``condition`` string with choices new/used,
``condition_stars`` integer 1–5, nullable). Existing rows become 'new'.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "008_product_condition"
down_revision: str | None = "007_agent_checkout_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "condition",
            sa.String(length=16),
            nullable=False,
            server_default="new",
        ),
    )
    op.add_column(
        "products",
        sa.Column("condition_stars", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_products_condition_values",
        "products",
        "condition IN ('new', 'used')",
    )
    op.create_check_constraint(
        "ck_products_condition_stars_range",
        "products",
        "condition_stars IS NULL OR (condition_stars BETWEEN 1 AND 5)",
    )
    op.create_check_constraint(
        "ck_products_stars_only_when_used",
        "products",
        "condition = 'used' OR condition_stars IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_products_stars_only_when_used", table_name="products")
    op.drop_constraint("ck_products_condition_stars_range", table_name="products")
    op.drop_constraint("ck_products_condition_values", table_name="products")
    op.drop_column("products", "condition_stars")
    op.drop_column("products", "condition")
