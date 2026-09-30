"""SubscriberTrialConfig — per-service credit limits for trial subscribers only.

Paid plans (standard/premium/enterprise) never have a row here.
The debit() function checks this table before processing any charge;
non-trial subscribers are completely unaffected.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, utc_now


class SubscriberTrialConfig(Base):
    __tablename__ = "SubscriberTrialConfig"

    SubscriberID: Mapped[int] = mapped_column(
        Integer, ForeignKey("Subscriber.SubscriberID"), primary_key=True
    )
    TrialExpiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    StationID: Mapped[int] = mapped_column(
        Integer, ForeignKey("Station.StationID"), nullable=False
    )
    MaxCommercial: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("60.0000")
    )
    MaxSong: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("40.0000")
    )
    MaxWord: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("40.0000")
    )
    MaxTranscription: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("28.0000")
    )
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default="SYSDATETIME()"
    )
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("User.UserID"), nullable=True
    )

    # Relationships
    trial_subscriber = relationship("Subscriber", foreign_keys=[SubscriberID])
    trial_station    = relationship("Station",    foreign_keys=[StationID])

    @property
    def is_active(self) -> bool:
        return datetime.utcnow() < self.TrialExpiresAt

    @property
    def days_remaining(self) -> int:
        delta = self.TrialExpiresAt - datetime.utcnow()
        return max(0, delta.days)

    # Map ReferenceType values → limit column names
    _LIMIT_MAP: dict[str, str] = {
        "commercial":    "MaxCommercial",
        "song":          "MaxSong",
        "word":          "MaxWord",
        "transcription": "MaxTranscription",
        "spectrum":      "MaxCommercial",  # treat spectrum as commercial bucket
    }

    def cap_for(self, reference_type: Optional[str]) -> Optional[Decimal]:
        """Return the credit cap for a given reference_type, or None if uncapped."""
        if not reference_type:
            return None
        col = self._LIMIT_MAP.get(reference_type.lower())
        return getattr(self, col) if col else None

    def __repr__(self) -> str:
        return (
            f"<SubscriberTrialConfig subscriber={self.SubscriberID} "
            f"expires={self.TrialExpiresAt:%Y-%m-%d} station={self.StationID}>"
        )
