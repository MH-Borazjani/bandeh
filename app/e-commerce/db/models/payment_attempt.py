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
"""PaymentAttempt: one try to pay one frozen order.

One order can have several attempts (first try fails, customer retries),
but each attempt is one try: one idempotency key, one trackId, one
amount. The key is ours to make (uuid4) — Zibal has NO idempotency-key
field, so the gateway never sees it; it exists so OUR code can tell
one try from another and so a retried request reuses the same key.

State machine (``PaymentStatus``):
created -> waiting -> paid | failed | expired.
``waiting`` means Zibal gave us a trackId and the customer is at the
gateway. ``paid`` happens exactly once, in one transaction, after
server-to-server verification — never from the callback parameters.
"""

from __future__ import annotations

import datetime
import enum
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models.order import Order


class PaymentStatus(str, enum.Enum):
    """Where one payment try is."""

    CREATED = "created"
    WAITING = "waiting"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"


class PaymentAttempt(Base, TimestampMixin):
    """One payment try for one order, on one gateway."""

    __tablename__ = "payment_attempts"

    __table_args__ = (
        CheckConstraint("amount_rial >= 0", name="ck_payment_attempts_amount_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Our idempotency key: one UUID per try, unique. Zibal never sees it.
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        unique=True,
        index=True,
        default=uuid.uuid4,
    )
    gateway: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="zibal",
    )
    # Zibal's session name for this try. Null until Zibal answers.
    track_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        unique=True,
        index=True,
    )
    # Frozen amount in Rial for this try (order total × 10 at start time).
    amount_rial: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentStatus.CREATED.value,
        index=True,
    )
    # The final receipt number from Zibal (verify response).
    ref_number: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    # Masked card number from Zibal (e.g. 603799******1234).
    card_number: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    paid_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    order: Mapped[Order] = relationship(
        "Order",
        back_populates="attempts",
    )

    def __repr__(self) -> str:
        return f"<PaymentAttempt order={self.order_id} status={self.status} track_id={self.track_id}>"


__all__ = ["PaymentAttempt", "PaymentStatus"]
