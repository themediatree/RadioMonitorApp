"""Reports routes — Detection report (Phase 3)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.user import User
from app.services.report_service import (
    get_detection_report,
    get_subscriber_campaigns,
    get_subscriber_commercials,
    get_subscriber_artists,
    get_subscriber_titles,
    get_song_report,
    get_subscriber_keywords,
    get_keyword_report,
    get_invoice_report,
    get_proof_of_broadcast,
)
from app.services.station_picker_service import get_monitored_stations_for_picker
from app.templating import templates

router = APIRouter(tags=["reports"])


# ---------------------------------------------------------------------------
# Reports index
# ---------------------------------------------------------------------------

@router.get("/reports", response_class=HTMLResponse)
def reports_index(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="app/reports/index.html",
        context={"user": user, **right_panel_context(user, db, request)},
    )


# ---------------------------------------------------------------------------
# Detection Report — on-screen preview
# ---------------------------------------------------------------------------

@router.get("/reports/detection", response_class=HTMLResponse)
def detection_report(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    commercial_name: Optional[str] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    stations    = get_monitored_stations_for_picker(db, user)
    commercials = get_subscriber_commercials(db, user.SubscriberID if user.SubscriberID else None)

    # Parse dates if provided (used to scope campaign list too)
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    campaigns = get_subscriber_campaigns(db, user.SubscriberID, df, dt) if user.SubscriberID else []

    filters = {
        "date_from":       date_from or "",
        "date_to":         date_to or "",
        "station_id":      station_id or "",
        "commercial_name": commercial_name or "",
        "campaign_id":     campaign_id or "",
    }

    # Run report when any filter is active (not just dates)
    any_filter = all or any([date_from, date_to, station_id, commercial_name, campaign_id]) or user.user_type.value == "internal"
    result = None
    oos_rows: list = []
    out_of_schedule_count = 0
    oos_detection_ids: set = set()
    oos_schedule_map: dict = {}

    if (user.SubscriberID or user.user_type.value == "internal") and any_filter:
        try:
            result = get_detection_report(
                db=db,
                subscriber_id=user.SubscriberID,
                date_from=df,
                date_to=dt,
                station_ids=[int(station_id)] if station_id else None,
                commercial_name=commercial_name or None,
                campaign_id=int(campaign_id) if campaign_id else None,
                base_url=str(request.base_url),
                generate_clip_urls=True,
            )
            db.commit()
        except (ValueError, Exception) as _det_err:
            import logging as _log
            _log.getLogger("radiomonitor").error(f"Detection report error: {_det_err}", exc_info=True)

        # Build OOS panel — unaccepted detections outside registered spot times
        if result and result.rows:
            from app.services.schedule_service import is_detection_in_schedule
            from app.services.detection_service import get_visible_detection
            from app.services.schedule_service import get_campaign_station_schedules as get_campaign_schedules
            import datetime as _dt

            for row in result.rows:
                if not row.detection_id:
                    continue
                item = get_visible_detection(db, user, row.detection_id)
                if item is None:
                    continue
                d = item["detection"]
                a = item["aired"]
                if getattr(d, "IsAccepted", False):
                    continue
                schedules = get_campaign_schedules(db, d.CampaignID, d.StationID)
                if not schedules:
                    continue
                det_dt = None
                try:
                    aired_date = a.get("aired_date") if isinstance(a, dict) else getattr(a, "aired_date", None)
                    start_clock = a.get("start_clock") if isinstance(a, dict) else getattr(a, "start_clock", None)
                    if aired_date and start_clock:
                        det_dt = _dt.datetime.combine(
                            _dt.date.fromisoformat(str(aired_date)),
                            _dt.time.fromisoformat(str(start_clock)[:5]),
                        )
                except Exception:
                    pass
                if det_dt and not is_detection_in_schedule(schedules, det_dt):
                    oos_rows.append(item)

            out_of_schedule_count = len(oos_rows)

        # Build set of ALL OOS detection IDs (accepted or not) appearing in the
        # report table, plus their registered schedule summary string.
        # This drives the "Out of spot" badge and orange schedule hint in the table.
        if result and result.rows:
            from app.services.schedule_service import (
                get_campaign_station_schedules as _get_scheds2,
                is_detection_in_schedule as _in_sched2,
            )
            import datetime as _dt2
            from app.services.detection_service import get_visible_detection as _gvd2
            for row in result.rows:
                if not row.detection_id:
                    continue
                item2 = _gvd2(db, user, row.detection_id)
                if item2 is None:
                    continue
                d2 = item2["detection"]
                a2 = item2["aired"]
                if not d2.CampaignID:
                    continue
                scheds2 = _get_scheds2(db, d2.CampaignID, d2.StationID)
                if not scheds2:
                    continue
                det_dt2 = None
                try:
                    aired_date2 = a2.get("aired_date") if isinstance(a2, dict) else getattr(a2, "aired_date", None)
                    start_clock2 = a2.get("start_clock") if isinstance(a2, dict) else getattr(a2, "start_clock", None)
                    if aired_date2 and start_clock2:
                        det_dt2 = _dt2.datetime.combine(
                            _dt2.date.fromisoformat(str(aired_date2)),
                            _dt2.time.fromisoformat(str(start_clock2)[:5]),
                        )
                except Exception:
                    pass
                if det_dt2 and not _in_sched2(scheds2, det_dt2):
                    oos_detection_ids.add(row.detection_id)
                    from_parts2, to_parts2 = [], []
                    for s2 in scheds2:
                        ft2, tt2 = s2.TimeFrom, s2.TimeTo
                        from_parts2.append(ft2.strftime("%H:%M") if hasattr(ft2, "strftime") else str(ft2)[:5])
                        to_parts2.append(tt2.strftime("%H:%M") if hasattr(tt2, "strftime") else str(tt2)[:5])
                    oos_schedule_map[row.detection_id] = {
                        "from_t": " | ".join(from_parts2),
                        "to_t":   " | ".join(to_parts2),
                    }

    from app.models.station import Station as _Station
    station_map = {s.StationID: s.StationName for s in db.query(_Station).all()}

    return templates.TemplateResponse(
        request=request,
        name="app/reports/detection_report.html",
        context={
            "user": user,
            "stations": stations,
            "commercials": commercials,
            "campaigns": campaigns,
            "filters": filters,
            "result": result,
            "oos_rows": oos_rows,
            "out_of_schedule_count": out_of_schedule_count,
            "station_map": station_map,
            "oos_detection_ids": oos_detection_ids,
            "oos_schedule_map": oos_schedule_map,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# Detection Report — accept OOS detections (called from report page)
# ---------------------------------------------------------------------------

@router.post("/reports/detection/accept-oos")
async def report_detection_accept_oos(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Accept out-of-spot detections from within the Detection Report. Body: {detection_ids: [int]}"""
    from fastapi import HTTPException
    from app.services.token_service import debit, is_postpaid
    from app.models.detection import Detection
    from app.services.detection_service import get_visible_detection

    body = await request.json()
    detection_ids: list[int] = body.get("detection_ids", [])
    if not detection_ids:
        raise HTTPException(status_code=400, detail="No detection_ids provided.")

    total_cost = 0
    accepted_ids: list[int] = []

    for did in detection_ids:
        item = get_visible_detection(db, user, did)
        if item is None:
            continue
        det: Detection = item["detection"]
        if getattr(det, "IsAccepted", False):
            continue
        duration_hrs = max(1.0, (det.EndTimeSec - det.StartTimeSec) / 3600.0)
        cost = round(duration_hrs)
        total_cost += cost
        det.IsAccepted = True
        accepted_ids.append(did)

    if total_cost > 0:
        debit(
            db,
            subscriber_id=user.SubscriberID,
            amount=total_cost,
            description=f"Out-of-spot detections accepted via report: {accepted_ids}",
            reference_id=accepted_ids[0] if accepted_ids else 0,
            reference_type="det_accept_bulk",
            created_by_user_id=user.UserID,
            source="app",
            bypass_balance_check=is_postpaid(db, user.SubscriberID),
        )

    db.commit()
    return {"ok": True, "accepted": accepted_ids, "credits_debited": total_cost}


# ---------------------------------------------------------------------------
# Detection Report — PDF export
# ---------------------------------------------------------------------------

@router.get("/reports/detection/pdf")
def detection_report_pdf(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    commercial_name: Optional[str] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
):
    today = date.today()
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    result = get_detection_report(
        db=db,
        subscriber_id=user.SubscriberID,
        date_from=df,
        date_to=dt,
        station_ids=[int(station_id)] if station_id else None,
        commercial_name=commercial_name or None,
        campaign_id=int(campaign_id) if campaign_id else None,
        base_url=str(request.base_url),
        generate_clip_urls=True,
        embed_logo_base64=True,
    )
    db.commit()

    # Build OOS detection IDs + schedule summaries for PDF badges
    oos_detection_ids_pdf: set = set()
    oos_schedule_map_pdf: dict = {}
    if result and result.rows:
        from app.services.detection_service import get_visible_detection as _gvd_pdf
        from app.services.schedule_service import (
            get_campaign_station_schedules as _get_scheds_pdf,
            is_detection_in_schedule as _in_sched_pdf,
        )
        import datetime as _dt_pdf
        for row in result.rows:
            if not row.detection_id:
                continue
            item_pdf = _gvd_pdf(db, user, row.detection_id)
            if item_pdf is None:
                continue
            d_pdf = item_pdf["detection"]
            a_pdf = item_pdf["aired"]
            if not d_pdf.CampaignID:
                continue
            scheds_pdf = _get_scheds_pdf(db, d_pdf.CampaignID, d_pdf.StationID)
            if not scheds_pdf:
                continue
            det_dt_pdf = None
            try:
                ad = a_pdf.get("aired_date") if isinstance(a_pdf, dict) else getattr(a_pdf, "aired_date", None)
                sc = a_pdf.get("start_clock") if isinstance(a_pdf, dict) else getattr(a_pdf, "start_clock", None)
                if ad and sc:
                    det_dt_pdf = _dt_pdf.datetime.combine(
                        _dt_pdf.date.fromisoformat(str(ad)),
                        _dt_pdf.time.fromisoformat(str(sc)[:5]),
                    )
            except Exception:
                pass
            if det_dt_pdf and not _in_sched_pdf(scheds_pdf, det_dt_pdf):
                oos_detection_ids_pdf.add(row.detection_id)
                from_pp, to_pp = [], []
                for sp in scheds_pdf:
                    ftp, ttp = sp.TimeFrom, sp.TimeTo
                    from_pp.append(ftp.strftime("%H:%M") if hasattr(ftp, "strftime") else str(ftp)[:5])
                    to_pp.append(ttp.strftime("%H:%M") if hasattr(ttp, "strftime") else str(ttp)[:5])
                oos_schedule_map_pdf[row.detection_id] = {
                    "from_t": " | ".join(from_pp),
                    "to_t":   " | ".join(to_pp),
                }

    html_content = templates.get_template(
        "app/reports/detection_report_pdf.html"
    ).render(result=result, oos_detection_ids=oos_detection_ids_pdf, oos_schedule_map=oos_schedule_map_pdf)

    from playwright.sync_api import sync_playwright
    import base64 as _b64
    filename = f"detection_report_{df}_{dt}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        encoded = _b64.b64encode(html_content.encode("utf-8")).decode("ascii")
        page.goto(f"data:text/html;base64,{encoded}", wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            margin={"top": "14mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            print_background=True,
        )
        browser.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Song Monitoring Report — on-screen preview
# ---------------------------------------------------------------------------

@router.get("/reports/songs", response_class=HTMLResponse)
def song_report(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    artist: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    stations = get_monitored_stations_for_picker(db, user)
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None
    artists = get_subscriber_artists(db, user.SubscriberID if user.SubscriberID else None, df, dt)
    titles  = get_subscriber_titles(db, user.SubscriberID if user.SubscriberID else None)

    filters = {
        "date_from":  date_from or "",
        "date_to":    date_to or "",
        "station_id": station_id or "",
        "artist":     artist or "",
        "title":      title or "",
    }

    any_filter = all or any([date_from, date_to, station_id, artist, title])
    result = None
    if (user.SubscriberID or user.user_type.value == "internal") and any_filter:
        try:
            result = get_song_report(
                db=db,
                subscriber_id=user.SubscriberID,
                date_from=df,
                date_to=dt,
                station_ids=[int(station_id)] if station_id else None,
                artist=artist or None,
                title=title or None,
                base_url=str(request.base_url),
                generate_clip_urls=True,
                short_lived=True,
            )
            db.commit()
        except (ValueError, Exception):
            pass

    return templates.TemplateResponse(
        request=request,
        name="app/reports/song_report.html",
        context={
            "user": user,
            "stations": stations,
            "artists": artists,
            "titles": titles,
            "filters": filters,
            "result": result,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# Song Monitoring Report — PDF export
# ---------------------------------------------------------------------------

@router.get("/reports/songs/pdf")
def song_report_pdf(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    artist: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
):
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    result = get_song_report(
        db=db,
        subscriber_id=user.SubscriberID,
        date_from=df,
        date_to=dt,
        station_ids=[int(station_id)] if station_id else None,
        artist=artist or None,
        title=title or None,
        base_url=str(request.base_url),
        generate_clip_urls=True,
        embed_logo_base64=True,
        short_lived=False,
    )
    db.commit()

    html_content = templates.get_template(
        "app/reports/song_report_pdf.html"
    ).render(result=result)

    from playwright.sync_api import sync_playwright
    filename = f"song_report_{df or 'all'}_{dt or 'all'}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        import base64 as _b64
        encoded = _b64.b64encode(html_content.encode("utf-8")).decode("ascii")
        page.goto(f"data:text/html;base64,{encoded}", wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            margin={"top": "14mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            print_background=True,
        )
        browser.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Keyword Detection Report — on-screen preview
# ---------------------------------------------------------------------------

@router.get("/reports/keywords", response_class=HTMLResponse)
def keyword_report(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    stations = get_monitored_stations_for_picker(db, user)
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None
    keywords = get_subscriber_keywords(db, user.SubscriberID if user.SubscriberID else None)

    filters = {
        "date_from":  date_from or "",
        "date_to":    date_to or "",
        "station_id": station_id or "",
        "keyword":    keyword or "",
    }

    any_filter = all or any([date_from, date_to, station_id, keyword])
    result = None
    if (user.SubscriberID or user.user_type.value == "internal") and any_filter:
        try:
            result = get_keyword_report(
                db=db,
                subscriber_id=user.SubscriberID,
                date_from=df,
                date_to=dt,
                station_ids=[int(station_id)] if station_id else None,
                keyword=keyword or None,
                base_url=str(request.base_url),
                generate_clip_urls=True,
                short_lived=True,
            )
            db.commit()
        except (ValueError, Exception):
            pass

    return templates.TemplateResponse(
        request=request,
        name="app/reports/keyword_report.html",
        context={
            "user": user,
            "stations": stations,
            "keywords": keywords,
            "filters": filters,
            "result": result,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# Keyword Detection Report — PDF export
# ---------------------------------------------------------------------------

@router.get("/reports/keywords/pdf")
def keyword_report_pdf(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
):
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    result = get_keyword_report(
        db=db,
        subscriber_id=user.SubscriberID,
        date_from=df,
        date_to=dt,
        station_ids=[int(station_id)] if station_id else None,
        keyword=keyword or None,
        base_url=str(request.base_url),
        generate_clip_urls=True,
        embed_logo_base64=True,
        short_lived=False,
    )
    db.commit()

    html_content = templates.get_template(
        "app/reports/keyword_report_pdf.html"
    ).render(result=result)

    from playwright.sync_api import sync_playwright
    filename = f"keyword_report_{df or 'all'}_{dt or 'all'}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        import base64 as _b64
        encoded = _b64.b64encode(html_content.encode("utf-8")).decode("ascii")
        page.goto(f"data:text/html;base64,{encoded}", wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            margin={"top": "14mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            print_background=True,
        )
        browser.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Invoice Report — on-screen preview
# ---------------------------------------------------------------------------

@router.get("/reports/invoice", response_class=HTMLResponse)
def invoice_report(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    filters = {
        "date_from": date_from or "",
        "date_to":   date_to or "",
    }

    any_filter = all or any([date_from, date_to])
    result = None
    if (user.SubscriberID or user.user_type.value == "internal") and any_filter:
        try:
            result = get_invoice_report(
                db=db,
                subscriber_id=user.SubscriberID,
                date_from=df,
                date_to=dt,
            )
        except (ValueError, Exception):
            pass

    from app.services.token_service import is_postpaid as _is_postpaid
    return templates.TemplateResponse(
        request=request,
        name="app/reports/invoice_report.html",
        context={
            "user": user,
            "filters": filters,
            "result": result,
            "is_postpaid_subscriber": _is_postpaid(db, user.SubscriberID) if user.SubscriberID else False,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# Invoice Report — PDF export
# ---------------------------------------------------------------------------

@router.get("/reports/invoice/pdf")
def invoice_report_pdf(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    result = get_invoice_report(
        db=db,
        subscriber_id=user.SubscriberID,
        date_from=df,
        date_to=dt,
    )

    from app.services.token_service import is_postpaid as _is_postpaid
    _postpaid_pdf = _is_postpaid(db, user.SubscriberID) if user.SubscriberID else False
    html_content = templates.get_template(
        "app/reports/invoice_report_pdf.html"
    ).render(result=result, is_postpaid_subscriber=_postpaid_pdf)

    from playwright.sync_api import sync_playwright
    filename = f"invoice_{df or 'all'}_{dt or 'all'}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        import base64 as _b64
        encoded = _b64.b64encode(html_content.encode("utf-8")).decode("ascii")
        page.goto(f"data:text/html;base64,{encoded}", wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=True,
            margin={"top": "14mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            print_background=True,
        )
        browser.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Proof of Broadcast — on-screen preview
# ---------------------------------------------------------------------------

@router.get("/reports/pob", response_class=HTMLResponse)
def pob_report(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    commercial_name: Optional[str] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    stations    = get_monitored_stations_for_picker(db, user)
    commercials = get_subscriber_commercials(db, user.SubscriberID if user.SubscriberID else None)
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None
    campaigns   = get_subscriber_campaigns(db, user.SubscriberID, df, dt) if user.SubscriberID else []

    filters = {
        "date_from":       date_from or "",
        "date_to":         date_to or "",
        "station_id":      station_id or "",
        "commercial_name": commercial_name or "",
        "campaign_id":     campaign_id or "",
    }

    # Resolve commercial name → ID(s) if provided
    commercial_ids = None
    if commercial_name and user.SubscriberID:
        from app.models.campaign import Commercial
        matching = (
            db.query(Commercial.CommercialID)
            .filter(
                Commercial.SubscriberID == user.SubscriberID,
                Commercial.DisplayTapeID == commercial_name,
            )
            .all()
        )
        commercial_ids = [r.CommercialID for r in matching] or None

    any_filter = all or any([date_from, date_to, station_id, commercial_name, campaign_id]) or user.user_type.value == "internal"
    result = None
    if (user.SubscriberID or user.user_type.value == "internal") and any_filter:
        try:
            result = get_proof_of_broadcast(
                db=db,
                subscriber_id=user.SubscriberID,
                date_from=df,
                date_to=dt,
                commercial_ids=commercial_ids,
                station_ids=[int(station_id)] if station_id else None,
                campaign_id=int(campaign_id) if campaign_id else None,
                base_url=str(request.base_url),
                generate_clip_urls=True,
            )
            db.commit()
        except (ValueError, Exception):
            pass

    return templates.TemplateResponse(
        request=request,
        name="app/reports/pob_report.html",
        context={
            "user": user,
            "stations": stations,
            "commercials": commercials,
            "campaigns": campaigns,
            "filters": filters,
            "result": result,
            **right_panel_context(user, db, request),
        },
    )


# ---------------------------------------------------------------------------
# Proof of Broadcast — PDF export
# ---------------------------------------------------------------------------

@router.get("/reports/pob/pdf")
def pob_report_pdf(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    station_id: Optional[str] = Query(default=None),
    commercial_name: Optional[str] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    all: Optional[str] = Query(default=None),
):
    df = date.fromisoformat(date_from) if date_from else None
    dt = date.fromisoformat(date_to)   if date_to   else None

    commercial_ids = None
    if commercial_name and user.SubscriberID:
        from app.models.campaign import Commercial
        matching = (
            db.query(Commercial.CommercialID)
            .filter(
                Commercial.SubscriberID == user.SubscriberID,
                Commercial.DisplayTapeID == commercial_name,
            )
            .all()
        )
        commercial_ids = [r.CommercialID for r in matching] or None

    result = get_proof_of_broadcast(
        db=db,
        subscriber_id=user.SubscriberID,
        date_from=df,
        date_to=dt,
        commercial_ids=commercial_ids,
        station_ids=[int(station_id)] if station_id else None,
        campaign_id=int(campaign_id) if campaign_id else None,
        base_url=str(request.base_url),
        generate_clip_urls=True,
    )

    # Commit clip tokens to DB before rendering PDF
    db.commit()

    html_content = templates.get_template(
        "app/reports/pob_report_pdf.html"
    ).render(result=result)

    from playwright.sync_api import sync_playwright
    filename = f"proof_of_broadcast_{result.certificate_number}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        import base64 as _b64
        encoded = _b64.b64encode(html_content.encode("utf-8")).decode("ascii")
        page.goto(f"data:text/html;base64,{encoded}", wait_until="networkidle")
        pdf_bytes = page.pdf(
            format="A4",
            landscape=False,
            margin={"top": "16mm", "bottom": "20mm", "left": "16mm", "right": "16mm"},
            print_background=True,
        )
        browser.close()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Trial Status Page (subscriber-facing)
# ---------------------------------------------------------------------------

@router.get("/trial-status", response_class=HTMLResponse)
def trial_status(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.trial_config import SubscriberTrialConfig
    from app.services.trial_service import get_all_service_consumed

    trial_cfg = db.get(SubscriberTrialConfig, user.SubscriberID) if user.SubscriberID else None
    consumed  = get_all_service_consumed(db, user.SubscriberID) if trial_cfg else {}

    return templates.TemplateResponse(
        request=request,
        name="app/trial_status.html",
        context={
            "user": user,
            "trial_cfg": trial_cfg,
            "consumed": consumed,
            **right_panel_context(user, db, request),
        },
    )
