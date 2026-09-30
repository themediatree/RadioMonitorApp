"""
BulkUploadSession / BulkUploadRow -- staging tables bridging the bulk
upload validate -> review -> commit flow.

A session is created at validate time (workbook parsed, every row checked,
total cost computed) and consumed once at commit time. Short-lived --
expires after 1 hour, not meant as permanent storage.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now


class BulkUploadSession(Base):
    __tablename__ = "BulkUploadSession"

    SessionID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SessionToken: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    UserID: Mapped[int] = mapped_column(ForeignKey("User.UserID"), nullable=False)
    SubscriberID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="validated")
    TotalCost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    ValidRowCount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    FailedRowCount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ZipStoragePath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    ExpiresAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    CommittedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    rows = relationship("BulkUploadRow", back_populates="session", cascade="all, delete-orphan")

    @property
    def is_expired(self) -> bool:
        return self.ExpiresAt < datetime.now()

    def __repr__(self) -> str:
        return f"<BulkUploadSession id={self.SessionID} status={self.Status}>"


class BulkUploadRow(Base):
    __tablename__ = "BulkUploadRow"

    RowID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SessionID: Mapped[int] = mapped_column(ForeignKey("BulkUploadSession.SessionID"), nullable=False)
    SheetName: Mapped[str] = mapped_column(String(30), nullable=False)
    RowNumber: Mapped[int] = mapped_column(Integer, nullable=False)
    IsValid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ErrorMessage: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    RowDataJson: Mapped[str] = mapped_column(Text, nullable=False)
    EstimatedCost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    ProcessedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ResultMessage: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    session = relationship("BulkUploadSession", back_populates="rows")

    def __repr__(self) -> str:
        return f"<BulkUploadRow id={self.RowID} sheet={self.SheetName} row={self.RowNumber} valid={self.IsValid}>"
