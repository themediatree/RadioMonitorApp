"""
Word/phrase detection routes.

    GET  /words                         active keyword subscriptions + recent detections
    GET  /words/subscribe               subscription form
    POST /words/subscribe               create subscription
    POST /words/subscribe/{id}/withdraw deactivate subscription
    GET  /words/detections              detection list
    GET  /words/detections/{id}         detail + audio player
    GET  /words/detections/{id}/clip    audio file serve
    GET  /words/search                  retrospective search form
    POST /words/search                  submit job
    GET  /words/jobs                    job list + status
"""

import math
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.station import Station
from app.models.user import User, UserType
from app.services.station_picker_service import (
    get_tbfp_stations_for_picker,
    resolve_tbfp_ids_to_station_ids,
    get_monitored_stations_for_picker,
)
from app.services import audit_service
from app.services.clip_service import resolve_clip_path
from app.services.subscription_service import (
    SubscriptionError,
    create_keyword_subscription,
    deactivate_subscription,
    get_word_detection,
    list_jobs,
    list_subscriptions,
    list_word_detections,
    submit_word_job,
)
from app.templating import templates

router = APIRouter(prefix="/words", tags=["words"])


def _active_stations(db: Session, user=None) -> list[Station]:
    """
    Active stations, respecting the user's station restrictions if any.
    user is optional for backward compatibility with any caller that
    doesn't have it handy -- always pass it when available so restrictions
    are actually enforced.
    """
    if user is not None:
        from app.services.permission_service import visible_stations
        return visible_stations(db, user)
    return (
        db.query(Station)
        .filter(Station.IsActive == True)  # noqa: E712
        .filter(Station.StationID != 6)
        .order_by(Station.StationName)
        .all()
    )


def _ip(request: Request) -> Optional[str]:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.client.host if request.client else None


@router.get("", response_class=HTMLResponse)
def words_dashboard(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    import json
    from datetime import date as date_type
    from app.models.detection import RecordingChunk
    from app.services.detection_service import parse_aired

    subscriptions = list_subscriptions(db, user, sub_type="keyword", include_inactive=True)
    recent_rows, _ = list_word_detections(db, user, page_size=10)
    retro_job = request.query_params.get("retro_job")
    stations = _active_stations(db, user)
    all_stn = db.query(Station).filter(Station.StationID != 6).all()
    station_map = {s.StationID: s.StationName for s in all_stn}
    today = date_type.today()

    def resolve_stations(sf):
        if not sf:
            return "All stations"
        try:
            ids = json.loads(sf)
            return ", ".join(station_map.get(i, str(i)) for i in ids)
        except Exception:
            return sf

    chunk_ids = {d.ChunkID for d in recent_rows if d.ChunkID}
    chunk_map = {}
    if chunk_ids:
        chunks = db.query(RecordingChunk).filter(RecordingChunk.ChunkID.in_(chunk_ids)).all()
        chunk_map = {c.ChunkID: c for c in chunks}

    enriched = []
    for d in recent_rows:
        chunk = chunk_map.get(d.ChunkID)
        aired = parse_aired(chunk, d.StartTime, d.EndTime)
        enriched.append({"detection": d, "aired": aired})

    # No schedule filtering for words — pipeline monitors 24/7

    return templates.TemplateResponse(
        request=request,
        name="app/words/dashboard.html",
        context={
            "user": user,
            "subscriptions": subscriptions,
            "recent_word_detections": enriched,
            "retro_job": retro_job,
            "station_map": station_map,
            "resolve_stations": resolve_stations,
            "today": today,
            "schedule_flags": {},
            **right_panel_context(user, db, request),
        },
    )


@router.get("/subscribe", response_class=HTMLResponse)
def word_subscribe_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.subscriber import Subscriber
    from app.services.token_service import get_balance
    from decimal import Decimal
    subscribers = (
        db.query(Subscriber).filter(Subscriber.SubscriberStatus == "active")
        .order_by(Subscriber.Name).all()
    ) if user.user_type == UserType.INTERNAL else []
    balance = get_balance(db, user.SubscriberID) if user.SubscriberID else Decimal("0")
    from app.services.billing_service import get_rate, get_exchange_rate
    from app.deps import get_currency
    from app.models.subscriber import Subscriber as SubscriberModel
    _sub = db.get(SubscriberModel, user.SubscriberID) if user.SubscriberID else None
    _plan = _sub.SubscriptionPlan if _sub else "standard"
    _zar = float(get_rate(db, _plan))
    _fx = float(get_exchange_rate(db))
    _cur = get_currency(request)
    return templates.TemplateResponse(
        request=request,
        name="app/words/subscribe.html",
        context={
            "user": user, "subscribers": subscribers,
            "stations": get_tbfp_stations_for_picker(db),
            "error": None, "form": {},
            "token_balance": balance,
            "rp_zar_per_token": _zar,
            "rp_fx_rate": _fx,
            "rp_currency": _cur,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/subscribe")
async def word_subscribe_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    keyword: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
    subscriber_id: Annotated[str, Form()] = "",
):
    from datetime import date as date_type
    from app.models.subscriber import Subscriber

    raw_form = await request.form()
    tbfp_ids = [int(s) for s in raw_form.getlist("station_ids") if s.isdigit()]
    station_ids = resolve_tbfp_ids_to_station_ids(db, tbfp_ids)

    def rerender(error: str):
        subs = (
            db.query(Subscriber).filter(Subscriber.SubscriberStatus == "active")
            .order_by(Subscriber.Name).all()
        ) if user.user_type == UserType.INTERNAL else []
        from app.services.token_service import get_balance
        from app.services.billing_service import get_rate, get_exchange_rate
        from app.deps import get_currency
        from decimal import Decimal
        _bal = get_balance(db, user.SubscriberID) if user.SubscriberID else Decimal("0")
        _sub = db.get(Subscriber, user.SubscriberID) if user.SubscriberID else None
        _plan = _sub.SubscriptionPlan if _sub else "standard"
        return templates.TemplateResponse(
            request=request,
            name="app/words/subscribe.html",
            context={
                "user": user, "subscribers": subs,
                "stations": get_tbfp_stations_for_picker(db),
                "error": error,
                "form": {
                    "keyword": keyword, "start_date": start_date,
                    "end_date": end_date, "subscriber_id": subscriber_id,
                },
                "token_balance": _bal,
                "rp_zar_per_token": float(get_rate(db, _plan)),
                "rp_fx_rate": float(get_exchange_rate(db)),
                "rp_currency": get_currency(request),
            },
            status_code=400,
        )

    if user.user_type == UserType.INTERNAL:
        if not subscriber_id.isdigit():
            return rerender("Please select a Subscriber.")
        target_sub_id = int(subscriber_id)
    else:
        target_sub_id = user.SubscriberID

    if not keyword.strip():
        return rerender("A keyword or phrase is required.")
    if not station_ids:
        return rerender("Select at least one station.")

    # Split comma-separated keywords — one subscription + job per keyword
    keywords = [k.strip() for k in keyword.split(",") if k.strip()]
    if not keywords:
        return rerender("A keyword or phrase is required.")

    try:
        sd = date_type.fromisoformat(start_date) if start_date else date_type.today()
        if not end_date:
            return rerender("End date is required.")
            ed = None
        ed = date_type.fromisoformat(end_date)
    except ValueError:
        return rerender("Invalid date format.")

    # Balance check before creating anything
    from app.services.token_service import calculate_cost_for_service, debit, get_balance, InsufficientTokensError
    from decimal import Decimal
    token_cost = calculate_cost_for_service(db, target_sub_id, "word", len(station_ids), sd, ed)
    balance = get_balance(db, target_sub_id)
    if balance < token_cost:
        return rerender(
            f"Insufficient credits. {len(station_ids)} station{'s' if len(station_ids)!=1 else ''} "
            f"requires {token_cost:.2f} credits. "
            f"Your balance is {balance:.2f} credits."
        )

    subs = []
    retro_jobs = []
    try:
        for kw in keywords:
            sub, retro_job = create_keyword_subscription(
                db,
                subscriber_id=target_sub_id,
                keyword=kw,
                station_ids=station_ids,
                start_date=sd,
                end_date=ed,
                created_by_user_id=user.UserID,
            )
            subs.append(sub)
            if retro_job:
                retro_jobs.append(retro_job)
    except SubscriptionError as e:
        return rerender(str(e))



    db.commit()
    try:
        debit(
            db, target_sub_id, token_cost,
            description=f"Keyword subscription: {len(keywords)} keyword(s) — {len(station_ids)} station{'s' if len(station_ids)!=1 else ''}",
            reference_id=subs[0].SubscriptionID,
            reference_type="word",
            created_by_user_id=user.UserID,
        )
        db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Token debit failed for word sub: %s", e)

    retro_note = f" retro_jobs={[j.JobID for j in retro_jobs]}" if retro_jobs else ""
    audit_service.record(
        db, action="keyword_subscription_created",
        actor_user_id=user.UserID,
        target_subscriber_id=target_sub_id,
        details=f"keywords={keywords} stations={station_ids}{retro_note}",
        ip_address=_ip(request),
    )
    redirect_url = "/words"
    if retro_jobs:
        redirect_url = f"/words?retro_job={retro_jobs[0].JobID}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subscribe/{subscription_id}/withdraw")
def word_subscribe_withdraw(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    subscription_id: int,
):
    try:
        sub = deactivate_subscription(db, user, subscription_id)
        db.commit()
        audit_service.record(
            db, action="keyword_subscription_withdrawn",
            actor_user_id=user.UserID,
            target_subscriber_id=sub.SubscriberID,
            details=f"subscription_id={subscription_id}",
            ip_address=_ip(request),
        )
    except SubscriptionError:
        pass
    return RedirectResponse("/words", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subscribe/{subscription_id}/extend")
def word_subscribe_extend(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    subscription_id: int,
):
    """UPDATE/INCLUDE: extend schedule to cover out-of-schedule word detections."""
    import json as _json
    from app.models.detection import ClientSubscription, WordDetection
    from app.services.schedule_service import (
        get_subscription_schedules, is_detection_in_schedule, extend_schedule_to_cover
    )
    from app.services.token_service import get_balance, debit, get_effective_rate
    from decimal import Decimal

    sub = db.get(ClientSubscription, subscription_id)
    if not sub or (user.SubscriberID and sub.SubscriberID != user.SubscriberID):
        return RedirectResponse("/words", status_code=status.HTTP_303_SEE_OTHER)

    schedules = get_subscription_schedules(db, subscription_id)
    if not schedules:
        return RedirectResponse("/words", status_code=status.HTTP_303_SEE_OTHER)

    try:
        station_ids = [int(x) for x in _json.loads(sub.StationFilter or "[]")]
    except Exception:
        station_ids = []

    all_detections = (
        db.query(WordDetection)
        .filter(WordDetection.SubscriptionID == subscription_id)
        .all()
    )

    oos = [
        {"detection_time": d.DetectionTime}
        for d in all_detections
        if not is_detection_in_schedule(schedules, d.DetectionTime)
    ]

    if not oos:
        return RedirectResponse("/words", status_code=status.HTTP_303_SEE_OTHER)

    from app.models.subscriber import Subscriber
    subscriber = db.get(Subscriber, sub.SubscriberID)

    result = extend_schedule_to_cover(
        db=db,
        subscription_id=subscription_id,
        out_of_schedule_detections=oos,
        station_ids=station_ids,
        end_date=sub.EndDate,
        rate_per_hour=Decimal("1.00"),
    )

    if result["cost"] > 0:
        service_rate = get_effective_rate(db, sub.SubscriberID, "word")
        extension_cost = (result["cost"] * service_rate).quantize(Decimal("0.0001"))
        balance = get_balance(db, sub.SubscriberID)
        if balance >= extension_cost:
            debit(
                db,
                subscriber_id=sub.SubscriberID,
                amount=extension_cost,
                description=f"Schedule extension: subscription #{subscription_id} — {', '.join(result['days_extended'])}",
                reference_id=subscription_id,
                reference_type="word_extend",
                created_by_user_id=user.UserID,
            )

    db.commit()
    audit_service.record(
        db, action="word_schedule_extended",
        actor_user_id=user.UserID,
        target_subscriber_id=sub.SubscriberID,
        details=f"subscription_id={subscription_id} hours_added={result['hours_added']:.2f} cost={result['cost']}",
        ip_address=_ip(request),
    )
    return RedirectResponse("/words", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/detections", response_class=HTMLResponse)
def word_detections(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    station_id: Optional[str] = None,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
):
    from datetime import date as date_type
    from sqlalchemy import func as sa_func
    from app.models.detection import WordDetection
    sid = int(station_id) if station_id and station_id.isdigit() else None
    df = date_type.fromisoformat(date_from) if date_from else None
    dt = date_type.fromisoformat(date_to) if date_to else None

    all_stn = db.query(Station).filter(Station.StationID != 6).all()
    station_map = {s.StationID: s.StationName for s in all_stn}
    stations = _active_stations(db, user)

    # When a specific keyword is selected (via + button), show paginated individual detections
    # When no keyword filter, show grouped summary (one row per unique keyword)
    is_grouped = not keyword

    if is_grouped:
        all_rows, total = list_word_detections(
            db, user,
            station_id=sid, date_from=df, date_to=dt,
            keyword=None, source=source or None,
            page=1, page_size=999999,
        )
        keyword_counts = {}
        for r in all_rows:
            kw = r.Keyword or "—"
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1
        seen_kw = set()
        rows = []
        for r in all_rows:
            kw = r.Keyword or "—"
            if kw not in seen_kw:
                seen_kw.add(kw)
                rows.append(r)
        total_pages = 1
        total = len(rows)
    else:
        rows, total = list_word_detections(
            db, user,
            station_id=sid, date_from=df, date_to=dt,
            keyword=keyword, source=source or None,
            page=page,
        )
        total_pages = max(1, math.ceil(total / 50))
        keyword_counts = {keyword: total}

    # Build schedule awareness — mark detections outside booked windows
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    from app.models.detection import ClientSubscription
    schedule_flags = {}
    if user.SubscriberID:
        subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == user.SubscriberID,
            ClientSubscription.SubscriptionType == "keyword",
        ).all()
        sub_schedules = {}
        for sub in subs:
            schedules = get_subscription_schedules(db, sub.SubscriptionID)
            if schedules:
                sub_schedules[sub.SubscriptionID] = schedules
        for row in rows:
            schedules = sub_schedules.get(row.SubscriptionID, [])
            if schedules and row.DetectionTime:
                schedule_flags[row.WordDetectionID] = is_detection_in_schedule(schedules, row.DetectionTime)
            else:
                schedule_flags[row.WordDetectionID] = True
    return templates.TemplateResponse(
        request=request,
        name="app/words/detections.html",
        context={
            "user": user, "rows": rows, "total": total,
            "page": page, "total_pages": total_pages,
            "stations": get_monitored_stations_for_picker(db, user),
            "station_map": station_map,
            "keyword_counts": keyword_counts,
            "schedule_flags": schedule_flags,
            "is_grouped": is_grouped,
            "filters": {
                "station_id": station_id or "",
                "keyword": keyword or "",
                "source": source or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
            **right_panel_context(user, db, request),
        },
    )


@router.get("/detections/{detection_id}", response_class=HTMLResponse)
def word_detection_detail(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    row = get_word_detection(db, user, detection_id)
    if row is None:
        return RedirectResponse("/words/detections", status_code=303)
    station = db.get(Station, row.StationID)
    from app.models.detection import RecordingChunk
    chunk = db.get(RecordingChunk, row.ChunkID) if row.ChunkID else None
    from app.services.detection_service import parse_aired
    aired = parse_aired(chunk, row.StartTime, row.EndTime)

    from app.services.clip_service import resolve_clip_path
    has_transcript = bool(
        row.WordTimestampPath and resolve_clip_path(row.WordTimestampPath)
    )

    return templates.TemplateResponse(
        request=request,
        name="app/words/detection_detail.html",
        context={
            "user": user, "detection": row,
            "station": station, "aired": aired,
            "has_transcript": has_transcript,
        },
    )


@router.get("/detections/{detection_id}/transcript")
def word_detection_transcript(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Serve the context-window word-timestamp JSON for a word/phrase detection."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse as JsonFileResponse
    from app.services.clip_service import resolve_clip_path

    row = get_word_detection(db, user, detection_id)
    if row is None:
        raise HTTPException(status_code=404)

    if not row.WordTimestampPath:
        raise HTTPException(status_code=404, detail="No transcript available for this detection.")

    json_path = resolve_clip_path(row.WordTimestampPath)
    if json_path is None:
        raise HTTPException(status_code=404, detail="Transcript file not found.")

    return JsonFileResponse(path=json_path, media_type="application/json")


@router.get("/detections/{detection_id}/clip")
def word_detection_clip(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Inline playback -- never restricted."""
    from fastapi import HTTPException
    row = get_word_detection(db, user, detection_id)
    if row is None:
        raise HTTPException(status_code=404)
    clip_path = resolve_clip_path(row.ClipPath)
    if clip_path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path=clip_path, media_type="audio/mpeg",
                        headers={"Accept-Ranges": "bytes"})


@router.get("/detections/{detection_id}/clip/download")
def word_detection_clip_download(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Explicit download -- gated by CanDownload."""
    from fastapi import HTTPException
    from app.services.permission_service import can_download
    if not can_download(user):
        raise HTTPException(status_code=403,
            detail="Downloading has been disabled for your account by your administrator.")
    row = get_word_detection(db, user, detection_id)
    if row is None:
        raise HTTPException(status_code=404)
    clip_path = resolve_clip_path(row.ClipPath)
    if clip_path is None:
        raise HTTPException(status_code=404)
    import os
    download_name = os.path.basename(clip_path)
    return FileResponse(path=clip_path, media_type="audio/mpeg", filename=download_name,
                        headers={"Accept-Ranges": "bytes"})


@router.get("/search", response_class=HTMLResponse)
def word_search_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.subscriber import Subscriber
    subscribers = (
        db.query(Subscriber).filter(Subscriber.SubscriberStatus == "active")
        .order_by(Subscriber.Name).all()
    ) if user.user_type == UserType.INTERNAL else []
    return templates.TemplateResponse(
        request=request,
        name="app/words/search.html",
        context={
            "user": user, "subscribers": subscribers,
            "stations": get_monitored_stations_for_picker(db, user),
            "error": None, "form": {},
        },
    )


@router.post("/search")
async def word_search_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    keyword: Annotated[str, Form()] = "",
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
    subscriber_id: Annotated[str, Form()] = "",
):
    from datetime import date as date_type
    raw_form = await request.form()
    station_ids = [int(s) for s in raw_form.getlist("station_ids") if s.isdigit()]

    if user.user_type == UserType.INTERNAL:
        target_sub_id = int(subscriber_id) if subscriber_id.isdigit() else None
    else:
        target_sub_id = user.SubscriberID

    try:
        df = date_type.fromisoformat(date_from)
        dt = date_type.fromisoformat(date_to)
    except ValueError:
        return RedirectResponse("/words/search?error=dates", status_code=303)

    try:
        job = submit_word_job(
            db, user,
            subscriber_id=target_sub_id,
            subscription_id=None,
            keyword=keyword,
            station_ids=station_ids,
            date_from=df,
            date_to=dt,
        )
    except SubscriptionError as e:
        return RedirectResponse(f"/words/search?error={e}", status_code=303)

    db.commit()
    return RedirectResponse(f"/words/jobs?submitted={job.JobID}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/jobs", response_class=HTMLResponse)
def word_jobs(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    submitted: Optional[int] = None,
):
    jobs = list_jobs(db, user, "word")
    return templates.TemplateResponse(
        request=request,
        name="app/words/jobs.html",
        context={"user": user, "jobs": jobs, "submitted": submitted},
    )
