"""PiDevice — Raspberry Pi RTL-SDR remote FM monitoring units.

Each Pi connects via WireGuard VPN and authenticates to the web app
using a per-device UUID ApiKey sent as a Bearer token.

Status fields are updated by the Pi heartbeat endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base, utc_now

if TYPE_CHECKING:
    from app.models.station import Station


def _new_api_key() -> str:
    return str(uuid.uuid4())


class PiDevice(Base):
    """Raspberry Pi RTL-SDR remote monitoring device."""

    __tablename__ = "PiDevice"
    __table_args__ = (
        Index("IX_PiDevice_StationID", "StationID"),
        Index("IX_PiDevice_IsActive",  "IsActive"),
        Index("IX_PiDevice_LastSeen",  "LastSeen"),
    )

    # ── Identity ────────────────────────────────────────────
    PiDeviceID:  Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    DeviceName:  Mapped[str]           = mapped_column(String(100), nullable=False)
    VpnIP:       Mapped[str]           = mapped_column(String(20),  nullable=False, unique=True)
    StationID:   Mapped[Optional[int]] = mapped_column(ForeignKey("Station.StationID", ondelete="SET NULL"), nullable=True)
    ApiKey:      Mapped[str]           = mapped_column(String(36),  nullable=False, unique=True, default=_new_api_key)

    # ── Lifecycle ───────────────────────────────────────────
    IsActive:    Mapped[bool]               = mapped_column(Boolean, nullable=False, default=True)
    LastSeen:    Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CreatedAt:   Mapped[datetime]           = mapped_column(DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime())

    # ── Status fields (updated by Pi heartbeat) ─────────────
    InternetSource:    Mapped[Optional[str]]   = mapped_column(String(50),  nullable=True)   # "ethernet" | "wifi" | "4G"
    InternetConnected: Mapped[Optional[bool]]  = mapped_column(Boolean,     nullable=True)   # None=unknown
    FmSignal:          Mapped[Optional[bool]]  = mapped_column(Boolean,     nullable=True)
    StreamActive:      Mapped[Optional[bool]]  = mapped_column(Boolean,     nullable=True)
    StreamUrl:         Mapped[Optional[str]]   = mapped_column(String(500), nullable=True)
    Frequency:         Mapped[Optional[float]] = mapped_column(Numeric(7, 3), nullable=True) # MHz e.g. 94.700
    PowerSource:       Mapped[Optional[str]]   = mapped_column(String(50),  nullable=True)   # "mains" | "UPS" | "battery"
    Uptime:            Mapped[Optional[str]]   = mapped_column(String(50),  nullable=True)   # "3d 14h 22m"

    # ── Relationships ───────────────────────────────────────
    station: Mapped[Optional["Station"]] = relationship("Station", foreign_keys=[StationID])

    # ── Helpers ─────────────────────────────────────────────
    @property
    def status_colour(self) -> str:
        """Traffic-light colour for admin UI status badge."""
        if not self.IsActive:
            return "#888888"
        if self.LastSeen is None:
            return "#888888"
        from datetime import timezone
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - self.LastSeen).total_seconds()
        if age < 120:    return "#4ade80"   # green  — seen < 2 min ago
        if age < 600:    return "#facc15"   # yellow — seen < 10 min ago
        return "#f87171"                     # red    — stale / offline

    @property
    def status_label(self) -> str:
        if not self.IsActive:
            return "Disabled"
        if self.LastSeen is None:
            return "Never seen"
        from datetime import timezone
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - self.LastSeen).total_seconds()
        if age < 120:  return "Online"
        if age < 600:  return "Idle"
        return "Offline"

    @property
    def last_seen_ago(self) -> str:
        """Human-readable time since last heartbeat."""
        if self.LastSeen is None:
            return "Never"
        from datetime import timezone
        age = int((datetime.now(timezone.utc).replace(tzinfo=None) - self.LastSeen).total_seconds())
        if age < 60:   return f"{age}s ago"
        if age < 3600: return f"{age // 60}m ago"
        if age < 86400: return f"{age // 3600}h ago"
        return f"{age // 86400}d ago"

    def __repr__(self) -> str:
        return f"<PiDevice id={self.PiDeviceID} name={self.DeviceName!r} vpn={self.VpnIP}>"
