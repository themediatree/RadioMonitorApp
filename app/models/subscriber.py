"""
Subscriber -- the unified flat tenant model.

Supersedes the Client / Agency / StationAccount tripartite shape under the
v0.3 entity model. Seven types: Agent, Advertiser, Brand Owner, Radio Station,
Government, Political Party, Other. See ENTITY_MODEL.md and migration 007.

The pipeline does not know or care about this table. It's web-app-internal
authorization scaffolding.
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.station import Station
    from app.models.user import User


SUBSCRIBER_TYPES = (
    "Agent",
    "Advertiser",
    "Brand Owner",
    "Radio Station",
    "Government",
    "Political Party",
    "Other",
)

SUBSCRIBER_STATUSES = ("pending", "active", "expired_grace", "archived", "cancelled")
# Statuses an admin can manually set (active is set automatically on invite acceptance)
ADMIN_SETTABLE_STATUSES = ("pending", "expired_grace", "archived", "cancelled")


class Subscriber(Base):
    __tablename__ = "Subscriber"
    __table_args__ = (
        UniqueConstraint("Slug", name="UQ_Subscriber_Slug"),
        CheckConstraint(
            "SubscriberType IN ('Agent','Advertiser','Brand Owner','Radio Station',"
            "'Government','Political Party','Other')",
            name="CK_Subscriber_Type",
        ),
        CheckConstraint(
            "SubscriberStatus IN ('pending','active','expired_grace','archived','cancelled')",
            name="CK_Subscriber_Status",
        ),
        CheckConstraint(
            "(SubscriberType = 'Other' AND OtherDescription IS NOT NULL "
            "  AND length(OtherDescription) > 0) "
            "OR (SubscriberType <> 'Other')",
            name="CK_Subscriber_Other_Description",
        ),
        CheckConstraint(
            "(SubscriberType = 'Radio Station' AND StationID IS NOT NULL) "
            "OR (SubscriberType <> 'Radio Station' AND StationID IS NULL)",
            name="CK_Subscriber_Station",
        ),
    )

    SubscriberID: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    Name: Mapped[str] = mapped_column(String(255), nullable=False)
    CompanyName: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    Slug: Mapped[str] = mapped_column(String(120), nullable=False)
    SubscriberType: Mapped[str] = mapped_column(String(30), nullable=False)
    OtherDescription: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ContactEmail: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ContactPhone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Must match SubscriptionPlanConfig.PlanCode's String(50) -- 005 lesson.
    SubscriptionPlan: Mapped[str] = mapped_column(
        String(50), ForeignKey("SubscriptionPlanConfig.PlanCode"), nullable=False
    )
    SubscriberStatus: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )
    # For SubscriberType='Radio Station' only.
    StationID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Station.StationID"), nullable=True
    )
    SubscriptionStart: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    SubscriptionEnd: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    UpdatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    plan = relationship("SubscriptionPlanConfig")
    station: Mapped[Optional["Station"]] = relationship("Station")
    users: Mapped[List["User"]] = relationship(
        "User", back_populates="subscriber", foreign_keys="[User.SubscriberID]"
    )

    def __repr__(self) -> str:
        return (f"<Subscriber id={self.SubscriberID} type={self.SubscriberType!r} "
                f"name={self.Name!r}>")
