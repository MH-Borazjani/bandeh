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
"""010: MCP server — personal access tokens + server-side cart.

Two tables for the real MCP server at /mcp:

- ``personal_access_tokens``: one row per token a customer creates in
  /auth/account. Only the SHA-256 hash is stored; the plaintext token is
  shown once at creation and never again. A token is how an AI app proves
  it acts for a customer (``Authorization: Bearer <token>``) — the shop
  never sees the customer's AI-app login.
- ``cart_items``: the MCP server's own cart, one row per (customer,
  product). The website's cart lives only in the browser (localStorage
  draft), which no AI can see, so the MCP write tools need this table.
  The website cart page is unchanged for now; merging the two carts is a
  later follow-up, not part of this migration.
"""

import sqlalchemy as sa
from alembic import op

revision = "010_mcp_server_cart"
down_revision = "009_google_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personal_access_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_pat_token_hash"),
    )
    op.create_index(
        "ix_pat_user_id", "personal_access_tokens", ["user_id"]
    )
    op.create_table(
        "cart_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "product_id", name="uq_cart_user_product"),
        sa.CheckConstraint("quantity >= 1", name="ck_cart_quantity_positive"),
    )
    op.create_index("ix_cart_user_id", "cart_items", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cart_user_id", table_name="cart_items")
    op.drop_table("cart_items")
    op.drop_index("ix_pat_user_id", table_name="personal_access_tokens")
    op.drop_table("personal_access_tokens")
