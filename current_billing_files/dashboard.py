"""
Authenticated dashboard.

Phase 1 scope: prove the auth flow works end-to-end. The dashboard just
greets the user and shows their tenancy context. Real widgets (recent
detections, station status, etc.) come in Phase 3.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.subscriber import Subscriber
from app.models.user import User
from app.templating import templates

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Authenticated landing for any user."""
    from app.services.token_service import get_balance
    from app.services.detection_service import list_commercial_detections
    from decimal import Decimal

    tenant_name: str | None = None
    if user.SubscriberID is not None:
        sub = db.get(Subscriber, user.SubscriberID)
        tenant_name = sub.Name if sub else None

    token_balance = (
        get_balance(db, user.SubscriberID)
        if user.SubscriberID else Decimal("0")
    )

    # Recent detections for right panel
    recent_rows, _ = list_commercial_detections(db, user, page_size=5)

    from app.models.detection import Detection, SongDetection, WordDetection, ClientSubscription
    from app.services.scoping import commercial_filter
    from sqlalchemy import or_, and_

    commercial_count = db.query(Detection).filter(commercial_filter(user, db)).count()

    if user.user_type.value == "internal":
        song_count = db.query(SongDetection).count()
        word_count = db.query(WordDetection).count()
    elif user.SubscriberID:
        import json as _json
        import logging as _logging
        _log = _logging.getLogger("radiomonitor")
        try:
            song_subs = db.query(ClientSubscription).filter(
                ClientSubscription.SubscriberID == user.SubscriberID,
                ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
            ).all()
            _log.info(f"[DASHBOARD] song_subs count: {len(song_subs)} for subscriber {user.SubscriberID}")
            if song_subs:
                all_station_ids = set()
                for s in song_subs:
                    if s.StationFilter:
                        try:
                            ids = _json.loads(s.StationFilter)
                            all_station_ids.update(int(i) for i in ids if i)
                        except Exception as e:
                            _log.warning(f"[DASHBOARD] StationFilter parse error: {e}")
                _log.info(f"[DASHBOARD] all_station_ids: {all_station_ids}")
                if all_station_ids:
                    song_count = (
                        db.query(SongDetection)
                        .filter(SongDetection.StationID.in_(all_station_ids))
                        .count()
                    )
                    _log.info(f"[DASHBOARD] song_count: {song_count}")
                else:
                    song_count = 0
            else:
                song_count = 0
        except Exception as e:
            _log.error(f"[DASHBOARD] song_count error: {e}", exc_info=True)
            song_count = 0

        try:
            word_sub_ids = [
                s.SubscriptionID for s in db.query(ClientSubscription).filter(
                    ClientSubscription.SubscriberID == user.SubscriberID,
                    ClientSubscription.SubscriptionType == "keyword",
                ).all()
            ]
            _log.info(f"[DASHBOARD] word_sub_ids: {word_sub_ids}")
            if word_sub_ids:
                word_count = (
                    db.query(WordDetection)
                    .filter(WordDetection.SubscriptionID.in_(word_sub_ids))
                    .count()
                )
                _log.info(f"[DASHBOARD] word_count: {word_count}")
            else:
                word_count = 0
        except Exception as e:
            _log.error(f"[DASHBOARD] word_count error: {e}", exc_info=True)
            word_count = 0
    else:
        song_count = 0
        word_count = 0
    from app.models.campaign import Campaign
    from datetime import date as _date
    today = _date.today()

    if user.user_type.value == "internal":
        # Internal: count all active campaigns today
        commercial_registered = (
            db.query(Campaign)
            .filter(
                Campaign.IsActive == True,
                Campaign.StartDate <= today,
                (Campaign.EndDate == None) | (Campaign.EndDate >= today),
            )
            .count()
        )
    elif user.SubscriberID:
        # Subscriber: count their active campaigns today
        commercial_registered = (
            db.query(Campaign)
            .filter(
                Campaign.SubscriberID == user.SubscriberID,
                Campaign.IsActive == True,
                Campaign.StartDate <= today,
                (Campaign.EndDate == None) | (Campaign.EndDate >= today),
            )
            .count()
        )
    else:
        commercial_registered = 0

    return templates.TemplateResponse(
        request=request,
        name="app/dashboard.html",
        context={
            "user": user,
            "tenant_name": tenant_name,
            "commercial_count": commercial_count,
            "song_count": song_count,
            "word_count": word_count,
            "commercial_registered": commercial_registered,
            **right_panel_context(user, db, request),
        },
    )
