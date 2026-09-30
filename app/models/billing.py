"""Billing models: token accounts, transactions, plan pricing, and FX rate."""

REFERENCE_TYPES = ("commercial", "song", "word", "transcription", "adjustment")
SERVICE_TYPES = ("commercial", "song", "word", "transcription", "spectrum")
TRANSACTION_TYPES = ("credit", "debit", "refund", "purchase", "adjustment")

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now


class SubscriberTokenAccount(Base):
    __tablename__ = "SubscriberTokenAccount"

    AccountID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(ForeignKey("Subscriber.SubscriberID"), nullable=False, unique=True)
    TokenBalance: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    TotalPurchased: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    TotalConsumed: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    UpdatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    subscriber = relationship("Subscriber", foreign_keys=[SubscriberID])


class TokenTransaction(Base):
    __tablename__ = "TokenTransaction"

    TransactionID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(ForeignKey("Subscriber.SubscriberID"), nullable=False)
    Type: Mapped[str] = mapped_column(String(30), nullable=False)
    TokenAmount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    BalanceAfter: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    Description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ReferenceID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ReferenceType: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())
    Source: Mapped[str] = mapped_column(String(10), nullable=False, default="app")


class BillingConfig(Base):
    __tablename__ = "BillingConfig"

    ConfigID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    PlanCode: Mapped[str] = mapped_column(String(50), ForeignKey("SubscriptionPlanConfig.PlanCode"), nullable=False, unique=True)
    ZARPerToken: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=Decimal("3.60"))
    UpdatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())
    UpdatedByUserID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class ExchangeRate(Base):
    __tablename__ = "ExchangeRate"

    RateID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    CurrencyPair: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    Rate: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    UpdatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())
    UpdatedByUserID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SubscriberServiceRate(Base):
    """Per-subscriber overrides for the per-service token rate multiplier.

    Rows here override DEFAULT_SERVICE_RATES in token_service.py for the
    given subscriber + service type combination. Absence of a row means
    the system default applies.
    """
    __tablename__ = "SubscriberServiceRate"

    RateID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(ForeignKey("Subscriber.SubscriberID"), nullable=False)
    ServiceType: Mapped[str] = mapped_column(String(20), nullable=False)   # commercial/song/word/transcription/spectrum
    RatePerHour: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    SetByUserID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    Notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())

    subscriber = relationship("Subscriber", foreign_keys=[SubscriberID])
