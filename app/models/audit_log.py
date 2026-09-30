"""
AuditLog and ImpersonationSession.

Columns reconciled to the actual migrated schema. The audit table uses a
generic (Action, ResourceType, ResourceID, Metadata) shape rather than
typed Target* columns.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now


class AuditLog(Base):
    __tablename__ = "AuditLog"

    AuditID: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    UserID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("User.UserID"), nullable=True
    )
    ImpersonationSessionID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ImpersonationSession.SessionID"), nullable=True
    )
    Action: Mapped[str] = mapped_column(String(100), nullable=False)
    ResourceType: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ResourceID: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    IPAddress: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    UserAgent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    Metadata_: Mapped[Optional[str]] = mapped_column("Metadata", String(4000), nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    actor = relationship("User", foreign_keys=[UserID])

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.AuditID} action={self.Action!r} "
            f"user={self.UserID}>"
        )


class ImpersonationSession(Base):
    """Records when an internal user 'views as' another user for support."""

    __tablename__ = "ImpersonationSession"

    SessionID: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    InternalUserID: Mapped[int] = mapped_column(
        ForeignKey("User.UserID"), nullable=False
    )
    TargetUserID: Mapped[int] = mapped_column(
        ForeignKey("User.UserID"), nullable=False
    )
    Reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    StartedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    EndedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    internal_user = relationship("User", foreign_keys=[InternalUserID])
    target_user = relationship("User", foreign_keys=[TargetUserID])

    @property
    def is_active(self) -> bool:
        return self.EndedAt is None
