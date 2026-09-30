"""User model -- anyone who can log in.

v0.3 (post migration 007): 3 user types only -- internal, subscriber_admin,
subscriber_user. Single SubscriberID FK replaces the three old ClientID/
AgencyID/StationAccountID columns. The CK_User_Ownership rule is:
    internal      -> SubscriberID IS NULL
    non-internal  -> SubscriberID IS NOT NULL
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.subscriber import Subscriber


class UserType(StrEnum):
    """Allowed values for User.UserType. Matches CK_User_Type in the DB."""

    INTERNAL = "internal"
    SUBSCRIBER_ADMIN = "subscriber_admin"
    SUBSCRIBER_USER = "subscriber_user"

    @property
    def is_internal(self) -> bool:
        return self == UserType.INTERNAL

    @property
    def is_subscriber(self) -> bool:
        return self in (UserType.SUBSCRIBER_ADMIN, UserType.SUBSCRIBER_USER)

    @property
    def is_admin_tier(self) -> bool:
        """Admin within their own Subscriber. Internal admins are separate."""
        return self == UserType.SUBSCRIBER_ADMIN


class User(Base):
    """Anyone who logs in. Bound to exactly one Subscriber, or to nothing (internal)."""

    __tablename__ = "User"
    __table_args__ = (
        CheckConstraint(
            "UserType IN ('internal','subscriber_admin','subscriber_user')",
            name="CK_User_Type",
        ),
        CheckConstraint(
            "(UserType = 'internal' AND SubscriberID IS NULL) "
            "OR (UserType <> 'internal' AND SubscriberID IS NOT NULL)",
            name="CK_User_Ownership",
        ),
    )

    UserID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    Email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    PasswordHash: Mapped[str] = mapped_column(String(255), nullable=False)
    FullName: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    SubscriberID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=True
    )
    UserType: Mapped[str] = mapped_column(String(20), nullable=False)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    EmailVerified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    SessionVersion: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    LastLogin: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CanDownload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    HasStationRestrictions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # HasStationRestrictions=False (default, grandfathered for all existing
    # users): unrestricted, sees everything the Subscriber has access to.
    # True: UserStationPermission is the explicit allow-list, consulted by
    # every scoping function (commercials, songs, words, transcriptions).
    # Only a subscriber_admin can set these, only for users within their own
    # Subscriber -- internal staff never configure this for a tenant.
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    UpdatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("User.UserID"), nullable=True
    )

    subscriber: Mapped[Optional["Subscriber"]] = relationship(
        "Subscriber", back_populates="users", foreign_keys=[SubscriberID]
    )
    created_by: Mapped[Optional["User"]] = relationship(
        "User", remote_side=[UserID], foreign_keys=[CreatedByUserID]
    )

    @property
    def user_type(self) -> UserType:
        return UserType(self.UserType)

    @property
    def display_name(self) -> str:
        return self.FullName or self.Email

    def __repr__(self) -> str:
        return f"<User id={self.UserID} email={self.Email!r} type={self.UserType}>"


class UserStationPermission(Base):
    """
    Explicit station allow-list for a subscriber_user. Only consulted when
    the owning User.HasStationRestrictions is True -- otherwise the user is
    unrestricted and this table is ignored entirely for them.

    Only a subscriber_admin can create/delete these rows, only for users
    within their own Subscriber. Internal staff never set these.
    """
    __tablename__ = "UserStationPermission"

    UserID: Mapped[int] = mapped_column(ForeignKey("User.UserID"), primary_key=True)
    StationID: Mapped[int] = mapped_column(ForeignKey("Station.StationID"), primary_key=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    def __repr__(self) -> str:
        return f"<UserStationPermission user={self.UserID} station={self.StationID}>"
