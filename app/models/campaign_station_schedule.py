"""
app/models/campaign_station_schedule.py

Time-windowed schedule per CampaignStation (CampaignID + StationID).
A CampaignStation with no schedule rows = full day, every day.
Pipeline detects 24/7 — schedule is billing + display only.
"""

from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKeyConstraint,
    Index, Integer, SmallInteger, Time,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base, utc_now

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class CampaignStationSchedule(Base):
    __tablename__ = "CampaignStationSchedule"
    __table_args__ = (
        ForeignKeyConstraint(
            ["CampaignID", "StationID"],
            ["CampaignStation.CampaignID", "CampaignStation.StationID"],
            ondelete="CASCADE",
        ),
        CheckConstraint("DayOfWeek BETWEEN 0 AND 6", name="CK_CampaignStationSchedule_DayOfWeek"),
        CheckConstraint("TimeTo > TimeFrom",          name="CK_CampaignStationSchedule_Times"),
        Index("IX_CampaignStationSchedule_Campaign_Station", "CampaignID", "StationID"),
    )

    ScheduleID: Mapped[int]      = mapped_column(Integer,     primary_key=True, autoincrement=True)
    CampaignID: Mapped[int]      = mapped_column(Integer,     nullable=False)
    StationID:  Mapped[int]      = mapped_column(Integer,     nullable=False)
    DayOfWeek:  Mapped[int]      = mapped_column(SmallInteger, nullable=False)
    TimeFrom:   Mapped[time]     = mapped_column(Time,        nullable=False)
    TimeTo:     Mapped[time]     = mapped_column(Time,        nullable=False)
    CreatedAt:  Mapped[datetime] = mapped_column(DateTime,    nullable=False, default=utc_now, server_default=func.sysdatetime())

    # campaign_station accessible via service layer query only
    # (composite FK prevents simple SQLAlchemy relationship at mapper init time)

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.DayOfWeek]

    @property
    def hours(self) -> float:
        from_mins = self.TimeFrom.hour * 60 + self.TimeFrom.minute
        to_mins   = self.TimeTo.hour   * 60 + self.TimeTo.minute
        return (to_mins - from_mins) / 60.0

    def __repr__(self) -> str:
        return (
            f"<CampaignStationSchedule id={self.ScheduleID} "
            f"campaign={self.CampaignID} station={self.StationID} "
            f"{self.day_name} {self.TimeFrom:%H:%M}–{self.TimeTo:%H:%M}>"
        )
