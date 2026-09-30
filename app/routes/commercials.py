"""
Registered commercials list + detail + withdrawal.

    GET  /commercials               list
    GET  /commercials/{id}          detail with receipt
    POST /commercials/{id}/withdraw withdraw a pending/active commercial
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.campaign import Commercial
from app.models.user import User, UserType
from app.services import audit_service
from app.services.commercial_service import get_commercial_detail, list_commercials
from app.templating import templates

router = APIRouter(tags=["commercials"])


def _ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


@router.get("/commercials", response_class=HTMLResponse)
def commercials_list(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: Optional[str] = None,
):
    rows, total = list_commercials(db, user, status_filter=status or None)
    return templates.TemplateResponse(
        request=request,
        name="app/commercials_list.html",
        context={
            "user": user,
            "rows": rows,
            "total": total,
            "status_filter": status or "",
            **right_panel_context(user, db, request),
        },
    )


@router.get("/commercials/{commercial_id}", response_class=HTMLResponse)
def commercial_detail(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    commercial_id: int,
):
    detail = get_commercial_detail(db, user, commercial_id)
    if detail is None:
        return templates.TemplateResponse(
            request=request,
            name="app/commercials_list.html",
            context={
                "user": user, "rows": [], "total": 0,
                "status_filter": "",
                "error": "Commercial not found or not accessible.",
            },
            status_code=404,
        )

    # Build broadcast schedule windows across all campaigns + stations
    from app.models.campaign import CampaignStation, CampaignCommercial
    from app.models.station import Station
    from app.services.schedule_service import get_campaign_station_schedules

    station_name_map = {s.StationID: s.StationName for s in db.query(Station).all()}
    campaign_ids = [c.CampaignID for c in detail["campaigns"]]
    schedule_windows = []
    if campaign_ids:
        cs_rows = (
            db.query(CampaignStation)
            .filter(CampaignStation.CampaignID.in_(campaign_ids))
            .all()
        )
        for cs in cs_rows:
            for w in get_campaign_station_schedules(db, cs.CampaignID, cs.StationID):
                w.station_name = station_name_map.get(cs.StationID, str(cs.StationID))
                schedule_windows.append(w)

    return templates.TemplateResponse(
        request=request,
        name="app/commercial_detail.html",
        context={
            "user": user,
            "commercial": detail["commercial"],
            "campaigns": detail["campaigns"],
            "stations": detail["stations"],
            "receipt": detail["receipt"],
            "sibling_count": detail["sibling_count"],
            "schedule_windows": schedule_windows,
            "withdraw_success": request.query_params.get("withdrawn") == "1",
            **right_panel_context(user, db, request),
        },
    )


@router.post("/commercials/{commercial_id}/withdraw")
def withdraw_commercial(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    commercial_id: int,
):
    """
    Withdraw a pending or active commercial. Scoped — users can only withdraw
    their own Subscriber's commercials. Internal can withdraw any.
    Implements Option-α re-staging when the file-owner withdraws with siblings.
    """
    from app.services.registration_service import withdraw_commercial as _withdraw

    q = db.query(Commercial).filter(Commercial.CommercialID == commercial_id)
    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return RedirectResponse("/commercials", status_code=status.HTTP_303_SEE_OTHER)
        q = q.filter(Commercial.SubscriberID == user.SubscriberID)

    commercial = q.one_or_none()
    if commercial is None or commercial.Status == "withdrawn":
        return RedirectResponse(
            f"/commercials/{commercial_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    tape_id = commercial.CommercialName
    subscriber_id = commercial.SubscriberID

    # Calculate partial refund for remaining station-hours before withdrawing.
    from app.services.token_service import calculate_cost_for_service, refund as token_refund, get_balance
    from datetime import date as date_type
    refund_amount = None
    try:
        from app.models.campaign import CampaignCommercial, CampaignStation
        station_count = (
            db.query(CampaignStation)
            .join(CampaignCommercial, CampaignCommercial.CampaignID == CampaignStation.CampaignID)
            .filter(CampaignCommercial.CommercialID == commercial_id)
            .count()
        )
        # Find campaign end date for remaining hours calculation
        from app.models.campaign import Campaign
        camp = (
            db.query(Campaign)
            .join(CampaignCommercial, CampaignCommercial.CampaignID == Campaign.CampaignID)
            .filter(CampaignCommercial.CommercialID == commercial_id)
            .first()
        )
        today = date_type.today()
        if camp and camp.EndDate and camp.EndDate > today and station_count > 0:
            refund_amount = calculate_cost_for_service(db, subscriber_id, "commercial", station_count, today, camp.EndDate)
    except Exception:
        refund_amount = None

    sibling_restaged = _withdraw(db, commercial)
    db.commit()

    if refund_amount:
        try:
            token_refund(
                db, subscriber_id, refund_amount,
                description=f"Withdrawal refund: {tape_id} — {refund_amount:.2f} remaining credits returned",
                reference_id=commercial_id,
                reference_type="commercial",
                created_by_user_id=user.UserID,
            )
            db.commit()
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Refund failed on withdrawal of %s: %s", commercial_id, e)

    audit_service.record(
        db,
        action="commercial_withdrawn",
        actor_user_id=user.UserID,
        target_subscriber_id=commercial.SubscriberID,
        details=(
            f"commercial_id={commercial_id} tape_id={tape_id}"
            + (f" option_alpha_restaged={sibling_restaged}" if sibling_restaged else "")
        ),
        ip_address=_ip(request),
    )

    return RedirectResponse(
        f"/commercials/{commercial_id}?withdrawn=1",
        status_code=status.HTTP_303_SEE_OTHER,
    )
