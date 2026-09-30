"""
Song detection routes.

    GET  /songs                     active song subscriptions + recent detections
    GET  /songs/subscribe           subscription form
    POST /songs/subscribe           create subscription
    POST /songs/subscribe/{id}/withdraw  deactivate subscription
    GET  /songs/detections          detection list (filterable)
    GET  /songs/detections/{id}     detail + audio player
    GET  /songs/detections/{id}/clip  audio file serve
    GET  /songs/search              retrospective search form
    POST /songs/search              submit job
    GET  /songs/jobs                job list + status
"""

import math
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.station import Station
from app.services.station_picker_service import (
    get_tbfp_stations_for_picker,
    resolve_tbfp_ids_to_station_ids,
    get_monitored_stations_for_picker,
)
from app.models.user import User, UserType
from app.services import audit_service
from app.services.clip_service import resolve_clip_path
from app.services.subscription_service import (
    SubscriptionError,
    create_song_subscription,
    deactivate_subscription,
    get_song_detection,
    list_jobs,
    list_song_detections,
    list_subscriptions,
    submit_song_job,
)
from app.templating import templates

router = APIRouter(prefix="/songs", tags=["songs"])


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


def _target_subscriber_id(user: User) -> Optional[int]:
    if user.user_type == UserType.INTERNAL:
        return None  # internal must select explicitly
    return user.SubscriberID


@router.get("", response_class=HTMLResponse)
def songs_dashboard(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    import json
    from datetime import date as date_type
    from app.models.detection import RecordingChunk
    from app.services.detection_service import parse_aired

    subscriptions = list_subscriptions(db, user, sub_type=None, include_inactive=True)
    song_subs = [s for s in subscriptions
                 if s.SubscriptionType in ("song_track", "song_title")]
    recent_rows, _ = list_song_detections(db, user, page_size=10)
    retro_job = request.query_params.get("retro_job")
    stations = _active_stations(db, user)
    all_stn = db.query(Station).filter(Station.StationID != 6).all()
    station_map = {s.StationID: s.StationName for s in all_stn}
    today = date_type.today()

    # Resolve StationFilter JSON -> names for each subscription
    def resolve_stations(sf):
        if not sf:
            return "All stations"
        try:
            ids = json.loads(sf)
            return ", ".join(station_map.get(i, str(i)) for i in ids)
        except Exception:
            return sf

    # Enrich recent detections with aired date+time
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

    # No schedule filtering for songs — pipeline monitors 24/7, dashboard shows all detections

    return templates.TemplateResponse(
        request=request,
        name="app/songs/dashboard.html",
        context={
            "user": user,
            "subscriptions": song_subs,
            "recent_song_detections": enriched,
            "retro_job": retro_job,
            "station_map": station_map,
            "resolve_stations": resolve_stations,
            "today": today,
            "schedule_flags": {},
            **right_panel_context(user, db, request),
        },
    )


@router.get("/subscribe", response_class=HTMLResponse)
def song_subscribe_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from app.models.subscriber import Subscriber
    from app.services.token_service import get_balance
    from decimal import Decimal
    subscribers = (
        db.query(Subscriber)
        .filter(Subscriber.SubscriberStatus == "active")
        .order_by(Subscriber.Name)
        .all()
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
        name="app/songs/subscribe.html",
        context={
            "user": user,
            "subscribers": subscribers,
            "stations": get_tbfp_stations_for_picker(db),
            "error": None,
            "form": {},
            "token_balance": balance,
            "rp_zar_per_token": _zar,
            "rp_fx_rate": _fx,
            "rp_currency": _cur,
            **right_panel_context(user, db, request),
        },
    )


@router.post("/subscribe")
async def song_subscribe_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    track_id: Annotated[str, Form()] = "",
    artist: Annotated[str, Form()] = "",
    title: Annotated[str, Form()] = "",
    start_date: Annotated[str, Form()] = "",
    end_date: Annotated[str, Form()] = "",
    subscriber_id: Annotated[str, Form()] = "",
):
    from datetime import date as date_type
    from app.models.subscriber import Subscriber

    raw_form = await request.form()
    tbfp_ids = [int(s) for s in raw_form.getlist("station_ids") if s.isdigit()]
    # Resolve TBFPStationIDs → dbo.Station rows (creates new rows if needed)
    station_ids = resolve_tbfp_ids_to_station_ids(db, tbfp_ids)

    def rerender(error: str):
        subs = (
            db.query(Subscriber)
            .filter(Subscriber.SubscriberStatus == "active")
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
            name="app/songs/subscribe.html",
            context={
                "user": user, "subscribers": subs,
                "stations": get_tbfp_stations_for_picker(db),
                "error": error,
                "form": {
                    "track_id": track_id, "artist": artist, "title": title,
                    "start_date": start_date, "end_date": end_date,
                    "subscriber_id": subscriber_id,
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

    if not track_id and not title:
        return rerender("Enter a song title or TrackID.")
    if not station_ids:
        return rerender("Select at least one station.")

    try:
        sd = date_type.fromisoformat(start_date) if start_date else date_type.today()
        if not end_date:
            return rerender("End date is required.")
            ed = None
        ed = date_type.fromisoformat(end_date)
    except ValueError:
        return rerender("Invalid date format.")

    try:
        sub, retro_job = create_song_subscription(
            db,
            subscriber_id=target_sub_id,
            track_id=track_id.strip() or None,
            artist=artist.strip() or None,
            title=title.strip() or None,
            station_ids=station_ids,
            start_date=sd,
            end_date=ed,
            created_by_user_id=user.UserID,
        )
    except SubscriptionError as e:
        return rerender(str(e))

    # Balance check + debit
    from app.services.token_service import calculate_cost, debit, get_balance, InsufficientTokensError
    from decimal import Decimal
    token_cost = calculate_cost(len(station_ids), sd, ed)
    balance = get_balance(db, target_sub_id)
    if balance < token_cost:
        db.rollback()
        return rerender(
            f"Insufficient credits. This subscription requires {token_cost:.2f} credits "
            f"({len(station_ids)} station{'s' if len(station_ids)!=1 else ''} × "
            f"{'30 days (open-ended)' if not ed else str((ed-sd).days+1)+' days'}). "
            f"Your balance is {balance:.2f} credits."
        )

    db.commit()
    try:
        debit(
            db, target_sub_id, token_cost,
            description=(
                f"Song subscription: {title or track_id or artist} — "
                f"{len(station_ids)} station{'s' if len(station_ids)!=1 else ''}"
            ),
            reference_id=sub.SubscriptionID,
            reference_type="song",
            created_by_user_id=user.UserID,
        )
        db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Token debit failed for song sub %s: %s", sub.SubscriptionID, e)

    retro_note = f" retrospective_job_id={retro_job.JobID}" if retro_job else ""
    audit_service.record(
        db, action="song_subscription_created",
        actor_user_id=user.UserID,
        target_subscriber_id=target_sub_id,
        details=f"track_id={track_id} title={title} artist={artist} stations={station_ids}{retro_note}",
        ip_address=_ip(request),
    )
    redirect_url = "/songs"
    if retro_job:
        redirect_url = f"/songs?retro_job={retro_job.JobID}"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subscribe/{subscription_id}/withdraw")
def song_subscribe_withdraw(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    subscription_id: int,
):
    try:
        sub = deactivate_subscription(db, user, subscription_id)
        db.commit()
        audit_service.record(
            db, action="song_subscription_withdrawn",
            actor_user_id=user.UserID,
            target_subscriber_id=sub.SubscriberID,
            details=f"subscription_id={subscription_id}",
            ip_address=_ip(request),
        )
    except SubscriptionError:
        pass
    return RedirectResponse("/songs", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subscribe/{subscription_id}/extend")
def song_subscribe_extend(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    subscription_id: int,
):
    """
    UPDATE/INCLUDE: extend schedule to cover out-of-schedule detections and bill the difference.
    Called when subscriber clicks UPDATE/INCLUDE in the right panel.
    """
    import json as _json
    from app.models.detection import ClientSubscription, SongDetection
    from app.services.schedule_service import (
        get_subscription_schedules, is_detection_in_schedule, extend_schedule_to_cover
    )
    from app.services.token_service import get_balance, debit
    from app.services.billing_service import get_rate
    from decimal import Decimal

    # Verify ownership
    sub = db.get(ClientSubscription, subscription_id)
    if not sub or (user.SubscriberID and sub.SubscriberID != user.SubscriberID):
        return RedirectResponse("/songs", status_code=status.HTTP_303_SEE_OTHER)

    schedules = get_subscription_schedules(db, subscription_id)
    if not schedules:
        # No schedule = nothing to extend
        return RedirectResponse("/songs", status_code=status.HTTP_303_SEE_OTHER)

    # Find all out-of-schedule detections for this subscription
    try:
        station_ids = [int(x) for x in _json.loads(sub.StationFilter or "[]")]
    except Exception:
        station_ids = []

    all_detections = (
        db.query(SongDetection)
        .filter(SongDetection.StationID.in_(station_ids))
        .filter(SongDetection.DetectedAt >= sub.StartDate)
        .all()
    ) if station_ids else []

    oos = [
        {"detection_time": d.DetectedAt}
        for d in all_detections
        if not is_detection_in_schedule(schedules, d.DetectedAt)
    ]

    if not oos:
        return RedirectResponse("/songs", status_code=status.HTTP_303_SEE_OTHER)

    # Get billing rate
    from app.models.subscriber import Subscriber
    subscriber = db.get(Subscriber, sub.SubscriberID)
    plan_code = subscriber.SubscriptionPlan if subscriber else "standard"
    rate = get_rate(db, plan_code)

    result = extend_schedule_to_cover(
        db=db,
        subscription_id=subscription_id,
        out_of_schedule_detections=oos,
        station_ids=station_ids,
        end_date=sub.EndDate,
        rate_per_hour=rate,
    )

    if result["cost"] > 0:
        balance = get_balance(db, sub.SubscriberID)
        if balance >= result["cost"]:
            debit(
                db,
                subscriber_id=sub.SubscriberID,
                amount=result["cost"],
                description=f"Schedule extension: subscription #{subscription_id} — {', '.join(result['days_extended'])}",
                reference_id=subscription_id,
                reference_type="song_extend",
                created_by_user_id=user.UserID,
            )

    db.commit()
    audit_service.record(
        db, action="song_schedule_extended",
        actor_user_id=user.UserID,
        target_subscriber_id=sub.SubscriberID,
        details=f"subscription_id={subscription_id} hours_added={result['hours_added']:.2f} cost={result['cost']}",
        ip_address=_ip(request),
    )
    return RedirectResponse("/songs", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/detections", response_class=HTMLResponse)
def song_detections(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    station_id: Optional[str] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
):
    from datetime import date as date_type
    sid = int(station_id) if station_id and station_id.isdigit() else None
    df = date_type.fromisoformat(date_from) if date_from else None
    dt = date_type.fromisoformat(date_to) if date_to else None

    all_stn = db.query(Station).filter(Station.StationID != 6).all()
    station_map = {s.StationID: s.StationName for s in all_stn}
    stations = _active_stations(db, user)

    is_grouped = not (artist or title)

    if is_grouped:
        all_song_rows, total = list_song_detections(
            db, user,
            station_id=sid, date_from=df, date_to=dt,
            artist=None, title=None,
            page=1, page_size=999999,
        )
        song_counts = {}
        for r in all_song_rows:
            key = f"{r.Artist or ''}|{r.Title or ''}"
            song_counts[key] = song_counts.get(key, 0) + 1
        seen_sg = set()
        rows = []
        for r in all_song_rows:
            key = f"{r.Artist or ''}|{r.Title or ''}"
            if key not in seen_sg:
                seen_sg.add(key)
                rows.append(r)
        total_pages = 1
        total = len(rows)
    else:
        rows, total = list_song_detections(
            db, user,
            station_id=sid, date_from=df, date_to=dt,
            artist=artist or None, title=title or None,
            page=page,
        )
        total_pages = max(1, math.ceil(total / 50))
        key = f"{artist or ''}|{title or ''}"
        song_counts = {key: total}

    # Build schedule awareness — mark detections outside booked windows
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    from app.models.detection import ClientSubscription
    from datetime import datetime as dt_type
    schedule_flags = {}
    if user.SubscriberID:
        subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == user.SubscriberID,
            ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
        ).all()
        # Build station→schedules map (use first scheduled sub per station)
        station_schedules = {}
        for sub in subs:
            import json as _json
            try:
                sids = _json.loads(sub.StationFilter or "[]")
            except Exception:
                sids = []
            schedules = get_subscription_schedules(db, sub.SubscriptionID)
            if schedules:
                for sid2 in sids:
                    if int(sid2) not in station_schedules:
                        station_schedules[int(sid2)] = schedules
        for row in rows:
            schedules = station_schedules.get(row.StationID, [])
            if schedules and row.DetectedAt:
                schedule_flags[row.SongDetectionID] = is_detection_in_schedule(schedules, row.DetectedAt)
            else:
                schedule_flags[row.SongDetectionID] = True  # no schedule = always in
    return templates.TemplateResponse(
        request=request,
        name="app/songs/detections.html",
        context={
            "user": user, "rows": rows, "total": total,
            "page": page, "total_pages": total_pages,
            "stations": get_monitored_stations_for_picker(db, user),
            "station_map": station_map,
            "song_counts": song_counts,
            "is_grouped": is_grouped,
            "schedule_flags": schedule_flags,
            "filters": {
                "station_id": station_id or "",
                "artist": artist or "",
                "title": title or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
            **right_panel_context(user, db, request),
        },
    )


@router.get("/detections/{detection_id}", response_class=HTMLResponse)
def song_detection_detail(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    row = get_song_detection(db, user, detection_id)
    if row is None:
        return RedirectResponse("/songs/detections", status_code=303)
    station = db.get(Station, row.StationID)
    from app.models.detection import RecordingChunk
    chunk = db.get(RecordingChunk, row.ChunkID) if row.ChunkID else None
    from app.services.detection_service import parse_aired
    aired = parse_aired(chunk, row.StartTime, row.EndTime)
    return templates.TemplateResponse(
        request=request,
        name="app/songs/detection_detail.html",
        context={
            "user": user, "detection": row,
            "station": station, "aired": aired,
        },
    )


@router.get("/detections/{detection_id}/clip")
def song_detection_clip(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Inline playback -- never restricted."""
    from fastapi import HTTPException
    row = get_song_detection(db, user, detection_id)
    if row is None:
        raise HTTPException(status_code=404)
    clip_path = resolve_clip_path(row.ClipPath)
    if clip_path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path=clip_path, media_type="audio/mpeg",
                        headers={"Accept-Ranges": "bytes"})


@router.get("/detections/{detection_id}/clip/download")
def song_detection_clip_download(
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
    row = get_song_detection(db, user, detection_id)
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
def song_search_form(
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
        name="app/songs/search.html",
        context={
            "user": user, "subscribers": subscribers,
            "stations": get_monitored_stations_for_picker(db, user),
            "error": None, "form": {},
        },
    )


@router.post("/search")
async def song_search_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    track_id: Annotated[str, Form()] = "",
    artist_filter: Annotated[str, Form()] = "",
    title_filter: Annotated[str, Form()] = "",
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
        return RedirectResponse("/songs/search?error=dates", status_code=303)

    try:
        job = submit_song_job(
            db, user,
            subscriber_id=target_sub_id,
            subscription_id=None,
            track_id=track_id.strip() or None,
            artist_filter=artist_filter.strip() or None,
            title_filter=title_filter.strip() or None,
            station_ids=station_ids,
            date_from=df,
            date_to=dt,
        )
    except SubscriptionError as e:
        return RedirectResponse(f"/songs/search?error={e}", status_code=303)

    db.commit()
    return RedirectResponse(f"/songs/jobs?submitted={job.JobID}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/jobs", response_class=HTMLResponse)
def song_jobs(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    submitted: Optional[int] = None,
):
    jobs = list_jobs(db, user, "song")
    return templates.TemplateResponse(
        request=request,
        name="app/songs/jobs.html",
        context={"user": user, "jobs": jobs, "submitted": submitted},
    )
