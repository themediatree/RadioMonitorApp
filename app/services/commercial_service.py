"""
app/services/commercial_service.py

List and retrieve Commercial rows scoped to the current user's Subscriber.
Internal users see all. External users see only their own Subscriber's rows.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.campaign import Campaign, CampaignCommercial, CampaignStation, Commercial
from app.models.station import Station
from app.models.user import User, UserType


def list_commercials(
    db: Session,
    user: User,
    status_filter: Optional[str] = None,
) -> tuple[list[Commercial], int]:
    """
    Return (rows, total) of Commercial rows visible to the user.
    Optionally filter by Status (pending/active/withdrawn).
    Further restricted to the user's allowed stations if they have
    explicit station restrictions set (a commercial is visible if ANY of
    its campaign's stations is in the user's allow-list).
    """
    q = db.query(Commercial)

    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return [], 0
        q = q.filter(Commercial.SubscriberID == user.SubscriberID)

    from app.services.permission_service import get_allowed_station_ids
    allowed_stations = get_allowed_station_ids(db, user)
    if allowed_stations is not None:
        if not allowed_stations:
            return [], 0
        q = (
            q.join(CampaignCommercial, CampaignCommercial.CommercialID == Commercial.CommercialID)
            .join(CampaignStation, CampaignStation.CampaignID == CampaignCommercial.CampaignID)
            .filter(CampaignStation.StationID.in_(allowed_stations))
            .distinct()
        )

    if status_filter:
        q = q.filter(Commercial.Status == status_filter)

    q = q.order_by(Commercial.CreatedAt.desc())
    rows = q.all()

    # Enrich each row with its campaigns and stations for list display.
    enriched = []
    for c in rows:
        camps = (
            db.query(Campaign)
            .join(CampaignCommercial, CampaignCommercial.CampaignID == Campaign.CampaignID)
            .filter(CampaignCommercial.CommercialID == c.CommercialID)
            .all()
        )
        station_ids = set()
        for camp in camps:
            for cs in db.query(CampaignStation).filter(
                CampaignStation.CampaignID == camp.CampaignID
            ).all():
                station_ids.add(cs.StationID)
        stations = (
            db.query(Station).filter(Station.StationID.in_(station_ids)).all()
            if station_ids else []
        )
        enriched.append({
            "commercial": c,
            "campaigns": camps,
            "stations": stations,
        })
    return enriched, len(enriched)


def get_commercial_detail(
    db: Session,
    user: User,
    commercial_id: int,
) -> Optional[dict]:
    """
    Return a commercial with its campaign + station list, creator, audit
    receipt, and sibling count. None if not found / out of scope.
    """
    from app.models.audit_log import AuditLog
    from sqlalchemy.orm import joinedload

    q = (
        db.query(Commercial)
        .options(
            joinedload(Commercial.subscriber),
            joinedload(Commercial.created_by),
        )
        .filter(Commercial.CommercialID == commercial_id)
    )
    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return None
        q = q.filter(Commercial.SubscriberID == user.SubscriberID)

    commercial = q.one_or_none()
    if commercial is None:
        return None

    # Campaigns this commercial belongs to.
    campaigns = (
        db.query(Campaign)
        .join(CampaignCommercial,
              CampaignCommercial.CampaignID == Campaign.CampaignID)
        .filter(CampaignCommercial.CommercialID == commercial_id)
        .all()
    )

    # Stations across all those campaigns.
    station_ids = set()
    for camp in campaigns:
        for cs in db.query(CampaignStation).filter(
            CampaignStation.CampaignID == camp.CampaignID
        ).all():
            station_ids.add(cs.StationID)

    stations = (
        db.query(Station).filter(Station.StationID.in_(station_ids)).all()
        if station_ids else []
    )

    # Registration receipt — the audit log entry created at registration time.
    receipt = (
        db.query(AuditLog)
        .filter(
            AuditLog.Action == "commercial_registered",
            AuditLog.UserID == commercial.CreatedByUserID,
        )
        .order_by(AuditLog.CreatedAt.desc())
        .all()
    )
    # Find the receipt entry that mentions this TapeID in Metadata.
    import json
    registration_receipt = None
    for entry in receipt:
        try:
            meta = json.loads(entry.Metadata_ or "{}")
            details = meta.get("details", "")
            if commercial.DisplayTapeID and commercial.DisplayTapeID in details:
                registration_receipt = entry
                break
        except (json.JSONDecodeError, AttributeError):
            pass

    # Count siblings sharing this FingerprintID (other Subscribers with same audio).
    sibling_count = 0
    if commercial.FingerprintID:
        sibling_count = (
            db.query(Commercial)
            .filter(
                Commercial.FingerprintID == commercial.FingerprintID,
                Commercial.CommercialID != commercial_id,
                Commercial.Status.in_(["active", "pending"]),
            )
            .count()
        )

    return {
        "commercial": commercial,
        "campaigns": campaigns,
        "stations": stations,
        "receipt": registration_receipt,
        "sibling_count": sibling_count,
    }
