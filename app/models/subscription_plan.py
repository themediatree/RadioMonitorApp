"""
SubscriptionPlanConfig -- the catalog of available subscription tiers.

Was originally inside client.py; promoted to its own module when client.py
was deleted in the v0.3 Subscriber refactor (migration 007 / R2).
The 4 rows are seeded into the database; never modified at runtime.
"""

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SubscriptionPlanConfig(Base):
    """Catalog of available subscription tiers."""

    __tablename__ = "SubscriptionPlanConfig"

    PlanCode: Mapped[str] = mapped_column(String(50), primary_key=True)
    DisplayName: Mapped[str] = mapped_column(String(100), nullable=False)
    PostExpiryDays: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    MaxSubscriptions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    MaxUsers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    AllowApiAccess: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    AllowScheduledReports: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    AllowSpectrumAnalysis: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    AllowTranscription: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    AllowSongDetection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    AllowWordDetection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Billing columns (Phase B1)
    TokensPerMonth: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    TokenPriceUSD: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)
    ContractMonths: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    TrialTokens: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 4), nullable=True)
    TranscriptionPriceUSD: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6), nullable=True)

    def __repr__(self) -> str:
        return f"<Plan {self.PlanCode!r}>"
