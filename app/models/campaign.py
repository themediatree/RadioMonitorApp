"""
Campaign + Commercial + the two link tables.

v0.3 (post 007):
  - Campaign owned by ONE Subscriber (replaces ClientID + AgencyID).
  - Commercial owned by ONE Subscriber; carries DisplayTapeID (user-facing
    Tape ID) plus a FingerprintID for Option C dedup.
  - CommercialName == filename stem on disk == "<SubscriberID>_<DisplayTapeID>"
    by construction. See FINGERPRINT_IDENTITY_DESIGN §5.
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.subscriber import Subscriber
    from app.models.user import User
    from app.models.campaign_station_schedule import CampaignStationSchedule


class Campaign(Base):
    __tablename__ = "Campaign"

    CampaignID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=True
    )
    Name: Mapped[str] = mapped_column(String(255), nullable=False)
    Description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    StartDate: Mapped[date] = mapped_column(Date, nullable=False)
    EndDate: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    Budget: Mapped[Optional[float]] = mapped_column(Numeric, nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    UpdatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    subscriber: Mapped[Optional["Subscriber"]] = relationship("Subscriber")
    commercials: Mapped[List["CampaignCommercial"]] = relationship(
        "CampaignCommercial", back_populates="campaign"
    )
    stations: Mapped[List["CampaignStation"]] = relationship(
        "CampaignStation", back_populates="campaign"
    )

    def __repr__(self) -> str:
        return f"<Campaign id={self.CampaignID} name={self.Name!r}>"


class CampaignCommercial(Base):
    """Link: which commercials a campaign uses. Composite PK."""

    __tablename__ = "CampaignCommercial"

    CampaignID: Mapped[int] = mapped_column(
        ForeignKey("Campaign.CampaignID"), primary_key=True
    )
    CommercialID: Mapped[int] = mapped_column(
        ForeignKey("Commercial.CommercialID"), primary_key=True
    )
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="commercials")


class CampaignStation(Base):
    """Link: which stations a campaign runs on. Composite PK."""

    __tablename__ = "CampaignStation"

    CampaignID: Mapped[int] = mapped_column(
        ForeignKey("Campaign.CampaignID"), primary_key=True
    )
    StationID: Mapped[int] = mapped_column(
        ForeignKey("Station.StationID"), primary_key=True
    )
    ExpectedAirings: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="stations")

    @property
    def has_schedule(self) -> bool:
        """Check via service layer - see schedule_service.get_campaign_station_schedules()"""
        return False  # placeholder - use get_campaign_station_schedules() directly


class Commercial(Base):
    """
    A registered commercial. The PIPELINE owns the original table; the web app
    writes rows during registration. CommercialName carries the prefixed
    filename stem; DisplayTapeID is the user-facing convention.
    """

    __tablename__ = "Commercial"
    __table_args__ = (
        UniqueConstraint(
            "SubscriberID", "DisplayTapeID", name="UQ_Commercial_Subscriber_TapeID"
        ),
    )

    CommercialID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    CommercialName: Mapped[str] = mapped_column(String(200), nullable=False)
    Brand: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    CommercialType: Mapped[str] = mapped_column(String(20), nullable=False)
    Description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    Source: Mapped[str] = mapped_column(String(10), nullable=False, default="app")
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    # v0.3 additions:
    SubscriberID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=True
    )
    DisplayTapeID: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    FingerprintID: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("User.UserID"), nullable=True
    )

    subscriber: Mapped[Optional["Subscriber"]] = relationship("Subscriber")
    created_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys="[Commercial.CreatedByUserID]"
    )

    def __repr__(self) -> str:
        return (f"<Commercial id={self.CommercialID} name={self.CommercialName!r} "
                f"sub={self.SubscriberID}>")
