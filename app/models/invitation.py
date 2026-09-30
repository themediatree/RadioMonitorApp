"""UserInvitation model -- pending invites awaiting acceptance (v0.3).

UserType now matches the User model's 3-value vocabulary. SubscriberID
replaces the old ClientID/AgencyID columns.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now


class UserInvitation(Base):
    __tablename__ = "UserInvitation"
    __table_args__ = (
        CheckConstraint(
            "UserType IN ('subscriber_admin', 'subscriber_user')",
            name="CK_Invitation_Type",
        ),
    )

    InvitationID: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    Email: Mapped[str] = mapped_column(String(255), nullable=False)
    Token: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    UserType: Mapped[str] = mapped_column(String(20), nullable=False)
    SubscriberID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=True
    )
    InvitedByUserID: Mapped[int] = mapped_column(
        ForeignKey("User.UserID"), nullable=False
    )
    ExpiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    AcceptedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    invited_by = relationship("User", foreign_keys=[InvitedByUserID])
    subscriber = relationship("Subscriber", foreign_keys=[SubscriberID])

    @property
    def is_accepted(self) -> bool:
        return self.AcceptedAt is not None

    @property
    def is_expired(self) -> bool:
        return self.ExpiresAt < datetime.now(tz=timezone.utc).replace(tzinfo=None)
