"""
Station model -- wraps the pipeline's existing Station table.

The pipeline OWNS this table. The web app reads from it but never modifies its
schema or writes rows from request handlers. Columns reconciled against the
live schema (INFORMATION_SCHEMA dump on AUDIOREC):
    StationID (int, PK), StationName (nvarchar 100), StreamURL (nvarchar 500),
    IsActive (bit), CreatedAt (datetime2 NOT NULL).
There is no Frequency column in the real table.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base, utc_now


class Station(Base):
    __tablename__ = "Station"

    StationID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    StationName: Mapped[str] = mapped_column(String(100), nullable=False)
    StreamURL: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    IsActive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    CreatedAt: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=func.sysdatetime()
    )

    def __repr__(self) -> str:
        return f"<Station id={self.StationID} name={self.StationName!r}>"
