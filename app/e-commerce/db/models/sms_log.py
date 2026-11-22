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
"""SmsLog: one row per SMS the shop tried to send.

The service (``services.sms_service``) is the ONLY writer: it inserts
the row as ``queued`` BEFORE calling the provider, then updates it to
``sent`` (with the provider message id) or ``failed``. So a row always
exists for an attempted send, even when the provider call blows up —
and the once-only rules (low-stock alert per product, payment
reminder per order) query this table by (message_type, reference)
instead of trusting memory.

``reference`` is the order id for order messages and the product id
for low-stock alerts. ``body_id`` is the panel pattern code used for
the send (None for dev-fallback sends and for the plain-SendSMS
fallback, which uses no pattern). ``text`` holds the ';'-joined
pattern variables actually sent on the wire — the pattern text itself
lives in the panel, not here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class SmsMessageType:
    """Values stored in ``SmsLog.message_type``."""

    OTP = "otp"
    ORDER_PAID = "order_paid"
    ORDER_TRACKING = "order_tracking"
    OWNER_ORDER_PAID = "owner_order_paid"
    LOW_STOCK = "low_stock"
    PAYMENT_REMINDER = "payment_reminder"


class SmsStatus:
    """Values stored in ``SmsLog.status``."""

    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"


class SmsLog(Base, TimestampMixin):
    """One attempted SMS: who, what kind, what text, did it go out."""

    __tablename__ = "sms_log"

    __table_args__ = (
        Index("ix_sms_log_message_type_reference", "message_type", "reference"),
        Index("ix_sms_log_phone", "phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    phone: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    message_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    # Panel pattern code (bodyId) used for this send; None for
    # dev-fallback sends and for the plain-SendSMS fallback.
    body_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<SmsLog id={self.id} type={self.message_type} "
            f"phone={self.phone} status={self.status} body_id={self.body_id}>"
        )


__all__ = ["SmsLog", "SmsMessageType", "SmsStatus"]
