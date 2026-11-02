"""Server-side user session model."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models.user import User


class UserSession(Base, TimestampMixin):
    """Active or revoked session state persisted in PostgreSQL."""

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    session_token: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Relationship back to the owner user
    user: Mapped[User] = relationship(
        "User",
        back_populates="sessions",
        lazy="joined",
    )

    @property
    def is_valid(self) -> bool:
        """Check if session is neither revoked nor expired."""
        now = datetime.datetime.now(datetime.timezone.utc)
        return not self.is_revoked and self.expires_at > now

    def __repr__(self) -> str:
        return f"<UserSession user_id={self.user_id} revoked={self.is_revoked}>"


__all__ = ["UserSession"]
