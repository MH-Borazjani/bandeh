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
"""Add sms_log table (Meli Payamak SMS audit log)

Revision ID: 012_sms_log
Revises: 011_product_specs
Create Date: 2026-11-22 12:30:00.000000

Why: every SMS the shop sends gets a row (see db/models/sms_log.py)
— written as "queued" BEFORE the provider is called, then "sent"
(with the provider message id) or "failed". The once-only rules
(low-stock alert per product, payment reminder per order) query this
table by (message_type, reference), so the indexes below are what
those checks run on.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_sms_log"
down_revision: str | None = "011_product_specs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sms_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=False),
        sa.Column("message_type", sa.String(length=30), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("body_id", sa.Integer(), nullable=True),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sms_log_message_type_reference",
        "sms_log",
        ["message_type", "reference"],
        unique=False,
    )
    op.create_index("ix_sms_log_phone", "sms_log", ["phone"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sms_log_phone", table_name="sms_log")
    op.drop_index("ix_sms_log_message_type_reference", table_name="sms_log")
    op.drop_table("sms_log")
