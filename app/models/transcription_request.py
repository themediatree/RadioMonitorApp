"""TranscriptionRequest model."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base, utc_now


class TranscriptionRequest(Base):
    __tablename__ = "TranscriptionRequest"

    RequestID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(ForeignKey("Subscriber.SubscriberID"), nullable=False)
    UserID: Mapped[Optional[int]] = mapped_column(ForeignKey("User.UserID"), nullable=True)
    StationID: Mapped[int] = mapped_column(ForeignKey("Station.StationID"), nullable=False)
    DateFrom: Mapped[date] = mapped_column(Date, nullable=False)
    DateTo: Mapped[date] = mapped_column(Date, nullable=False)
    TimeFrom: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    TimeTo:       Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    ScheduleJson: Mapped[Optional[str]] = mapped_column(Text,      nullable=True)
    OutputFormat: Mapped[str] = mapped_column(String(16), nullable=False, default="chunks")
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    TokensDebited: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    MergedJsonPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ChunkCount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())
    ProcessedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    Source: Mapped[str] = mapped_column(String(10), nullable=False, default="app")

    station = relationship("Station")
