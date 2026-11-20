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
"""Agent checkout sessions (ACP + UCP): one row per agent cart.

An AI agent (ChatGPT via ACP, Gemini/AI Mode via UCP) builds a cart
through the protocol endpoints, and this row holds it: the items, the
buyer, the priced totals, and — after ``complete`` — the shop order
and the Zibal payment link.

Money is always int Rial here (the protocols' minor unit), currency
``irr``. The shop still prices in Toman; the service converts with
``core.money.toman_to_rial`` (x10) in exactly one place.

No card data ever lands here. If an agent sends a Stripe SPT or a
Google Pay token, the service records only that a token was offered
(never the value) and answers with the Zibal link instead — the buyer
pays on the bank page. See ``services/agent_commerce.py``.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class AgentCheckoutSession(Base, TimestampMixin):
    """One agent checkout session, for ACP (``cs_…``) or UCP (``chk_…``)."""

    __tablename__ = "agent_checkout_sessions"

    __table_args__ = (
        Index("ix_agent_sessions_session_id", "session_id", unique=True),
        Index("ix_agent_sessions_protocol_key", "protocol", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    # "acp" or "ucp".
    protocol: Mapped[str] = mapped_column(String(8), nullable=False)
    # Public session id the agent uses: cs_<uuid> (ACP) / chk_<uuid> (UCP).
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Protocol status string (ACP: not_ready_for_payment /
    # ready_for_payment / completed / canceled; UCP: incomplete /
    # ready_for_complete / completed / canceled).
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # ISO 4217, lowercase. Always "irr": the shop settles in Rial.
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="irr")
    # Buyer as the agent sent it (name, email, phone, address).
    # MutableDict: the service mutates these dicts in place, and plain
    # JSON columns would silently drop those changes on commit.
    buyer_json: Mapped[dict | None] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=True
    )
    # Resolved cart lines: [{"product_id": str, "quantity": int}].
    items_json: Mapped[list] = mapped_column(
        MutableList.as_mutable(JSON), nullable=False, default=list
    )
    # Full session payload (line items, totals, fulfillment, links,
    # messages, webhook events) as the last response showed it.
    state_json: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    # The shop order created by ``complete`` (None until then).
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Zibal start URL the buyer pays on (None until ``complete``).
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Last Idempotency-Key seen for this session + body fingerprint
    # (sha256 of the canonical request body) + cached response, for
    # spec-shaped replay: same key + same body returns the same answer
    # with Idempotent-Replayed: true; same key + other body is a 422.
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    last_response_json: Mapped[dict | None] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=True
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AgentCheckoutSession {self.protocol}:{self.session_id} "
            f"status={self.status}>"
        )


__all__ = ["AgentCheckoutSession"]
