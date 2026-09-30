"""ApiKey and SignedClipToken models."""

from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.database import Base, utc_now


class ApiKey(Base):
    __tablename__ = "ApiKey"

    ApiKeyID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(ForeignKey("Subscriber.SubscriberID"), nullable=False)
    KeyHash: Mapped[str] = mapped_column(String(128), nullable=False)
    KeyPrefix: Mapped[str] = mapped_column(String(16), nullable=False)
    Label: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())
    LastUsedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SignedClipToken(Base):
    __tablename__ = "SignedClipToken"

    TokenID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    Token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    ClipPath: Mapped[str] = mapped_column(String(500), nullable=False)
    ExpiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    UsedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    @property
    def is_valid(self) -> bool:
        return self.UsedAt is None and self.ExpiresAt > datetime.now()
