"""TBFPStation — ICASA TBFP FM frequency assignment data.

Source: ICASA Table of Broadcasting Frequency Plan (TBFP), Table 5.
1,336 FM assignments across South Africa.
RadiusKm is pre-computed: min(28 * sqrt(ERP_kW), 120).
"""

from __future__ import annotations
from typing import Optional
from sqlalchemy import Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

CATEGORY_COLOURS = {
    "PBS": "#cc4444",
    "CML": "#ffaa00",
    "CTY": "#44cc44",
    "LP":  "#888888",
    "TRI": "#888888",
}

LOGO_BASE_PATH = r"D:\RadioMonitorApp\output\station_logo"


class TBFPStation(Base):
    __tablename__ = "TBFPStation"

    TBFPStationID:  Mapped[int]           = mapped_column(Integer, primary_key=True, autoincrement=True)
    RowNo:          Mapped[int]           = mapped_column(Integer, nullable=False)
    Tower_location: Mapped[str]           = mapped_column(String(100), nullable=False)
    Province:       Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    LatDMS:         Mapped[str]           = mapped_column(String(20), nullable=False)
    LonDMS:         Mapped[str]           = mapped_column(String(20), nullable=False)
    LatDecimal:     Mapped[float]         = mapped_column(Float, nullable=False)
    LonDecimal:     Mapped[float]         = mapped_column(Float, nullable=False)
    FreqMHz:        Mapped[float]         = mapped_column(Float, nullable=False)
    ErpKw:          Mapped[float]         = mapped_column(Float, nullable=False)
    Polarisation:   Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    Station_name:   Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    LogoPath:       Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    StreamURL:      Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    Status:         Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    Category:       Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    OnAirDate:      Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    UnitIP:         Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    Comments:       Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    RadiusKm:       Mapped[float]         = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("IX_TBFPStation_Category",       "Category"),
        Index("IX_TBFPStation_FreqMHz",        "FreqMHz"),
        Index("IX_TBFPStation_Tower_location", "Tower_location"),
        Index("IX_TBFPStation_Station_name",   "Station_name"),
        Index("IX_TBFPStation_Province",       "Province"),
    )

    @property
    def colour(self) -> str:
        return CATEGORY_COLOURS.get(self.Category or "", "#888888")

    @property
    def logo_url(self) -> Optional[str]:
        """Returns a web-accessible URL for the station logo, or None."""
        if self.LogoPath:
            return f"/static/station_logos/{self.LogoPath}"
        return None

    @property
    def status_label(self) -> str:
        return {
            "OPE": "Operational",
            "OP":  "Operational",
            "LI":  "Licensed",
            "LIC": "Licensed",
            "SPA": "Spare",
            "SP":  "Spare",
            "TRI": "Trial",
        }.get(self.Status or "", self.Status or "Unknown")

    def to_geojson(self) -> dict:
        return {
            "id":           self.TBFPStationID,
            "name":         self.Tower_location,
            "station_name": self.Station_name or "",
            "province":     self.Province or "",
            "lat":          self.LatDecimal,
            "lon":          self.LonDecimal,
            "freq_mhz":     self.FreqMHz,
            "erp_kw":       self.ErpKw,
            "program":      self.Station_name or "",  # keep 'program' key for JS compatibility
            "status":       self.Status or "",
            "status_label": self.status_label,
            "category":     self.Category or "",
            "radius_km":    self.RadiusKm,
            "color":        self.colour,
            "pol":          self.Polarisation or "",
            "on_air":       self.OnAirDate or "",
            "logo_url":     self.logo_url,
            "stream_url":   self.StreamURL or "",
            "unit_ip":      self.UnitIP or "",
        }
