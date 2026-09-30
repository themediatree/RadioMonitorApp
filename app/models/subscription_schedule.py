"""
app/models/subscription_schedule.py

Time-windowed schedule for a ClientSubscription.
A subscription with no SubscriptionSchedule rows = full day, every day.
A subscription with schedule rows = only those day+time windows are "booked".

DayOfWeek follows ISO weekday: 0=Monday, 1=Tuesday, ..., 6=Sunday.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey,
    Index, Integer, SmallInteger, Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.detection import ClientSubscription

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class SubscriptionSchedule(Base):
    """One time window on one day of the week for a subscription."""

    __tablename__ = "SubscriptionSchedule"
    __table_args__ = (
        CheckConstraint("DayOfWeek BETWEEN 0 AND 6", name="CK_SubscriptionSchedule_DayOfWeek"),
        CheckConstraint("TimeTo > TimeFrom",          name="CK_SubscriptionSchedule_Times"),
        Index("IX_SubscriptionSchedule_SubscriptionID", "SubscriptionID"),
    )

    ScheduleID:     Mapped[int]      = mapped_column(Integer,     primary_key=True, autoincrement=True)
    SubscriptionID: Mapped[int]      = mapped_column(BigInteger,  ForeignKey("ClientSubscription.SubscriptionID", ondelete="CASCADE"), nullable=False)
    DayOfWeek:      Mapped[int]      = mapped_column(SmallInteger, nullable=False)   # 0=Mon … 6=Sun
    TimeFrom:       Mapped[time]     = mapped_column(Time,        nullable=False)
    TimeTo:         Mapped[time]     = mapped_column(Time,        nullable=False)
    CreatedAt:      Mapped[datetime] = mapped_column(DateTime,    nullable=False, default=utc_now, server_default=func.sysdatetime())

    subscription: Mapped["ClientSubscription"] = relationship(
        "ClientSubscription", back_populates="schedules"
    )

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.DayOfWeek]

    @property
    def hours(self) -> float:
        """Duration of this window in hours."""
        from_mins = self.TimeFrom.hour * 60 + self.TimeFrom.minute
        to_mins   = self.TimeTo.hour   * 60 + self.TimeTo.minute
        return (to_mins - from_mins) / 60.0

    def __repr__(self) -> str:
        return (
            f"<SubscriptionSchedule id={self.ScheduleID} "
            f"sub={self.SubscriptionID} {self.day_name} "
            f"{self.TimeFrom:%H:%M}–{self.TimeTo:%H:%M}>"
        )
