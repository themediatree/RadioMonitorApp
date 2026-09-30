"""
PasswordResetToken model -- self-service "Forgot password" flow.

Mirrors UserInvitation's token pattern: a time-limited, single-use token
emailed to the user, redeemed once to set a new password. UsedAt marks
redemption (distinct from expiry) so a used-but-not-yet-expired token is
still correctly rejected on a second attempt.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now


class PasswordResetToken(Base):
    __tablename__ = "PasswordResetToken"

    TokenID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    UserID: Mapped[int] = mapped_column(ForeignKey("User.UserID"), nullable=False)
    Token: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    ExpiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    UsedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    user = relationship("User")

    @property
    def is_used(self) -> bool:
        return self.UsedAt is not None

    @property
    def is_expired(self) -> bool:
        return self.ExpiresAt < datetime.now(tz=timezone.utc).replace(tzinfo=None)

    @property
    def is_valid(self) -> bool:
        return not self.is_used and not self.is_expired

    def __repr__(self) -> str:
        return f"<PasswordResetToken id={self.TokenID} user={self.UserID}>"
