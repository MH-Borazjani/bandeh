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
"""Add agent_checkout_sessions table (ACP + UCP checkout sessions)

Revision ID: 007_agent_checkout_sessions
Revises: 006_order_tracking_code
Create Date: 2026-11-20 20:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "007_agent_checkout_sessions"
down_revision: str | None = "006_order_tracking_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_checkout_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("protocol", sa.String(length=8), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="irr"),
        sa.Column("buyer_json", sa.JSON(), nullable=True),
        sa.Column("items_json", sa.JSON(), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("order_id", sa.UUID(), nullable=True),
        sa.Column("payment_url", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("last_response_json", sa.JSON(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_sessions_session_id",
        "agent_checkout_sessions",
        ["session_id"],
        unique=True,
    )
    op.create_index(
        "ix_agent_sessions_protocol_key",
        "agent_checkout_sessions",
        ["protocol", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_sessions_protocol_key", table_name="agent_checkout_sessions")
    op.drop_index("ix_agent_sessions_session_id", table_name="agent_checkout_sessions")
    op.drop_table("agent_checkout_sessions")
