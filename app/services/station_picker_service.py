"""
app/services/station_picker_service.py

Provides station lists for form pickers:
  - Registration forms  → TBFPStation (full ICASA list, all available stations)
  - Detection/Report    → dbo.Station  (operational list, what IS monitored)

Also handles auto-provisioning dbo.Station rows when a subscriber selects
a TBFPStation for monitoring registration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.station import Station
from app.models.tbfp_station import TBFPStation


@dataclass
class StationPickerItem:
    """
    Unified station item for the station picker macro.
    Compatible with the existing macro which uses .StationID and .StationName.
    """
    StationID: int
    StationName: str
    LogoPath: Optional[str] = None


def get_tbfp_stations_for_picker(db: Session) -> list[StationPickerItem]:
    """
    Returns unique station names from TBFPStation for registration forms.
    Uses TBFPStationID as the picker value — these are resolved to
    dbo.Station rows on form submission via ensure_station_exists().
    Excludes stations with no Station_name.
    """
    from sqlalchemy import func

    results = (
        db.query(
            TBFPStation.Station_name,
            func.min(TBFPStation.TBFPStationID).label("tbfp_id"),
            func.max(TBFPStation.LogoPath).label("logo_path"),
            func.max(TBFPStation.StreamURL).label("stream_url"),
        )
        .filter(TBFPStation.Station_name.isnot(None))
        .group_by(TBFPStation.Station_name)
        .order_by(TBFPStation.Station_name)
        .all()
    )

    return [
        StationPickerItem(
            StationID=r.tbfp_id,      # use TBFPStationID as picker value
            StationName=r.Station_name,
            LogoPath=r.logo_path,
        )
        for r in results
    ]


def ensure_station_exists(
    db: Session,
    tbfp_station_id: int,
) -> Optional[Station]:
    """
    Given a TBFPStationID (from a registration form submission), ensure a
    corresponding dbo.Station row exists. Creates one if not found.

    Returns the dbo.Station row (existing or newly created).
    The caller is responsible for db.flush()/commit().
    """
    tbfp = db.get(TBFPStation, tbfp_station_id)
    if not tbfp or not tbfp.Station_name:
        return None

    # Check if dbo.Station already has this station by name
    existing = (
        db.query(Station)
        .filter(Station.StationName == tbfp.Station_name)
        .first()
    )
    if existing:
        return existing

    # Create new Station row (inactive until service starts)
    new_station = Station(
        StationName=tbfp.Station_name,
        StreamURL=tbfp.StreamURL or None,
        IsActive=False,
    )
    db.add(new_station)
    db.flush()
    return new_station


def resolve_tbfp_ids_to_station_ids(
    db: Session,
    tbfp_ids: list[int],
) -> list[int]:
    """
    Convert a list of TBFPStationIDs (from registration form) to
    dbo.Station StationIDs, creating Station rows as needed.

    Returns list of StationIDs ready for subscription_service calls.
    """
    station_ids = []
    for tbfp_id in tbfp_ids:
        station = ensure_station_exists(db, tbfp_id)
        if station and station.StationID not in station_ids:
            station_ids.append(station.StationID)
    return station_ids


def get_monitored_stations_for_picker(db: Session, user=None) -> list[Station]:
    """
    Returns stations relevant to the current user for detection/report pickers.

    - Internal users: all stations in dbo.Station (minus generic StationID=6)
    - Subscribers: only stations they have actual detection data on,
      regardless of IsActive status (historical data always accessible)
    """
    from app.models.user import UserType

    # Internal users see everything
    if user is None or user.user_type == UserType.INTERNAL:
        return (
            db.query(Station)
            .filter(Station.StationID != 6)
            .order_by(Station.StationName)
            .all()
        )

    subscriber_id = user.SubscriberID
    if not subscriber_id:
        return []

    from sqlalchemy import union_all, select, literal_column
    from app.models.detection import Detection, SongDetection, WordDetection
    from app.models.campaign import Commercial

    # Commercial detections: scoped via Commercial.SubscriberID
    commercial_sids = (
        db.query(Detection.StationID)
        .join(Commercial, Commercial.CommercialID == Detection.CommercialID)
        .filter(Commercial.SubscriberID == subscriber_id)
        .distinct()
    )

    # Song detections: scoped via SubscriptionID → ClientSubscription.SubscriberID
    from app.models.detection import ClientSubscription
    song_sids = (
        db.query(SongDetection.StationID)
        .join(ClientSubscription,
              ClientSubscription.SubscriptionID == SongDetection.SubscriptionID)
        .filter(ClientSubscription.SubscriberID == subscriber_id)
        .distinct()
    )

    # Word detections: scoped via SubscriptionID → ClientSubscription.SubscriberID
    word_sids = (
        db.query(WordDetection.StationID)
        .join(ClientSubscription,
              ClientSubscription.SubscriptionID == WordDetection.SubscriptionID)
        .filter(ClientSubscription.SubscriberID == subscriber_id)
        .distinct()
    )

    # Combine all station IDs
    all_sids = set()
    for q in [commercial_sids, song_sids, word_sids]:
        try:
            all_sids.update(r[0] for r in q.all() if r[0])
        except Exception:
            pass  # column may not exist on older schema

    if not all_sids:
        return []

    stations = (
        db.query(Station)
        .filter(Station.StationID.in_(all_sids))
        .filter(Station.StationID != 6)
        .order_by(Station.StationName)
        .all()
    )

    # Respect station restrictions if set
    if user.HasStationRestrictions:
        from app.services.permission_service import visible_stations
        visible = {s.StationID for s in visible_stations(db, user)}
        stations = [s for s in stations if s.StationID in visible]

    return stations
