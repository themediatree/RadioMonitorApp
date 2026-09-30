"""Detection viewing routes (v0.3)."""

import math
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.station import Station
from app.models.user import User
from app.services.clip_service import resolve_clip_path
from app.services.detection_service import (
    DEFAULT_PAGE_SIZE,
    get_visible_detection,
    list_commercial_detections,
)
from app.services.station_picker_service import get_monitored_stations_for_picker
from app.templating import templates

router = APIRouter(tags=["detections"])


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


@router.get("/detections", response_class=HTMLResponse)
def detections_list(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    station_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    commercial: Optional[str] = None,
    brand: Optional[str] = None,
    campaign: Optional[str] = None,
    page: int = 1,
):
    from datetime import date

    sid = int(station_id) if station_id and station_id.isdigit() else None
    df = None
    dt = None
    try:
        if date_from:
            df = date.fromisoformat(date_from)
        if date_to:
            dt = date.fromisoformat(date_to)
    except ValueError:
        pass

    rows, total = list_commercial_detections(
        db, user,
        station_id=sid,
        date_from=df,
        date_to=dt,
        commercial_name=commercial or None,
        brand=brand or None,
        campaign_name=campaign or None,
        page=page,
    )
    total_pages = max(1, math.ceil(total / DEFAULT_PAGE_SIZE))

    # Build per-detection schedule flags and, for OOS rows, a human-readable
    # schedule summary string (e.g. "Mon 06:00–09:00 | Sat 09:00–12:00") so
    # the template can show the registered spot windows in orange beneath Start.
    schedule_flags: dict = {}
    schedule_summary_map: dict = {}
    if rows:
        import datetime as _dt
        from app.services.schedule_service import (
            get_campaign_station_schedules as _get_scheds,
            is_detection_in_schedule as _in_sched,
        )
        for item in rows:
            d = item["detection"]
            a = item["aired"]
            did = d.DetectionID
            if not d.CampaignID:
                schedule_flags[did] = True
                continue
            scheds = _get_scheds(db, d.CampaignID, d.StationID)
            if not scheds:
                schedule_flags[did] = True
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
            in_sched = _in_sched(scheds, det_dt) if det_dt else True
            schedule_flags[did] = in_sched
            if not in_sched:
                from_parts, to_parts = [], []
                for s in scheds:
                    ft, tt = s.TimeFrom, s.TimeTo
                    from_parts.append(ft.strftime("%H:%M:%S") if hasattr(ft, "strftime") else str(ft)[:8])
                    to_parts.append(tt.strftime("%H:%M:%S") if hasattr(tt, "strftime") else str(tt)[:8])
                schedule_summary_map[did] = {
                    "from_t": " | ".join(from_parts),
                    "to_t":   " | ".join(to_parts),
                }

    return templates.TemplateResponse(
        request=request,
        name="app/detections_list.html",
        context={
            "user": user,
            "rows": rows,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "stations": get_monitored_stations_for_picker(db, user),
            "station_map": {s.StationID: s.StationName for s in db.query(Station).all()},
            "schedule_flags": schedule_flags,
            "schedule_summary_map": schedule_summary_map,
            **right_panel_context(user, db, request),
            "filters": {
                "station_id": station_id or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
                "commercial": commercial or "",
                "brand": brand or "",
                "campaign": campaign or "",
            },
        },
    )


@router.get("/detections/{detection_id}", response_class=HTMLResponse)
def detection_detail(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    item = get_visible_detection(db, user, detection_id)
    if item is None:
        return templates.TemplateResponse(
            request=request,
            name="app/detections_list.html",
            context={
                "user": user, "rows": [], "total": 0, "page": 1,
                "total_pages": 1, "stations": [], "filters": {},
                "error": "Detection not found or not accessible.",
            },
            status_code=404,
        )

    det = item["detection"]
    from app.models.station import Station
    station = db.get(Station, det.StationID)

    has_transcript = bool(
        det.WordTimestampPath and resolve_clip_path(det.WordTimestampPath)
    )

    # Generic commercial transcript -- keyed by FingerprintID, separate from
    # the liveread per-airing WordTimestampPath.
    has_generic_transcript = False
    if not has_transcript and det.commercial and det.commercial.FingerprintID:
        from app.services.detection_service import get_generic_transcript_path
        from app.services.clip_service import resolve_generic_transcript_path
        gen_json = get_generic_transcript_path(db, det.commercial.FingerprintID)
        has_generic_transcript = bool(gen_json and resolve_generic_transcript_path(gen_json))

    # Broadcast schedule windows for this campaign+station
    broadcast_from = ""
    broadcast_to = ""
    if det.CampaignID:
        from app.services.schedule_service import get_campaign_station_schedules as _get_detail_scheds
        detail_scheds = _get_detail_scheds(db, det.CampaignID, det.StationID)
        if detail_scheds:
            from_parts, to_parts = [], []
            for s in detail_scheds:
                ft, tt = s.TimeFrom, s.TimeTo
                from_parts.append(ft.strftime("%H:%M:%S") if hasattr(ft, "strftime") else str(ft)[:8])
                to_parts.append(tt.strftime("%H:%M:%S") if hasattr(tt, "strftime") else str(tt)[:8])
            broadcast_from = " | ".join(from_parts)
            broadcast_to   = " | ".join(to_parts)

    return templates.TemplateResponse(
        request=request,
        name="app/detection_detail.html",
        context={
            "user": user,
            "detection": det,
            "aired": item["aired"],
            "station": station,
            "has_transcript": has_transcript,
            "has_generic_transcript": has_generic_transcript,
            "transcript_endpoint": (
                f"/detections/{det.DetectionID}/transcript" if has_transcript
                else f"/detections/{det.DetectionID}/generic-transcript"
            ),
            "broadcast_from": broadcast_from,
            "broadcast_to": broadcast_to,
        },
    )


@router.get("/detections/{detection_id}/generic-transcript")
def detection_generic_transcript(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """
    Serve the FingerprintID-keyed generic transcript JSON for a commercial
    detection. Returns 404 if not generic, no FingerprintID, not yet
    transcribed, or out of scope.
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse as JsonFileResponse
    from app.services.detection_service import get_generic_transcript_path
    from app.services.clip_service import resolve_generic_transcript_path

    item = get_visible_detection(db, user, detection_id)
    if item is None:
        raise HTTPException(status_code=404)

    det = item["detection"]
    if not det.commercial or not det.commercial.FingerprintID:
        raise HTTPException(status_code=404, detail="No transcript available for this commercial.")

    json_rel = get_generic_transcript_path(db, det.commercial.FingerprintID)
    if json_rel is None:
        raise HTTPException(status_code=404, detail="Transcript not yet available.")

    json_path = resolve_generic_transcript_path(json_rel)
    if json_path is None:
        raise HTTPException(status_code=404, detail="Transcript file not found.")

    return JsonFileResponse(path=json_path, media_type="application/json")


@router.get("/detections/{detection_id}/transcript")
def detection_transcript(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """
    Serve the word-level timestamp JSON for a liveread detection.
    Returns 404 if not a liveread, not yet transcribed, or out of scope.
    """
    from fastapi import HTTPException
    from fastapi.responses import FileResponse as JsonFileResponse

    item = get_visible_detection(db, user, detection_id)
    if item is None:
        raise HTTPException(status_code=404)

    det = item["detection"]
    if not det.WordTimestampPath:
        raise HTTPException(status_code=404, detail="No transcript available for this detection.")

    json_path = resolve_clip_path(det.WordTimestampPath)
    if json_path is None:
        raise HTTPException(status_code=404, detail="Transcript file not found.")

    return JsonFileResponse(path=json_path, media_type="application/json")


@router.get("/detections/{detection_id}/clip")
def detection_clip(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Inline playback -- never restricted, available to every user regardless
    of download permission. Karaoke and audio listening are always available."""
    item = get_visible_detection(db, user, detection_id)
    if item is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    det = item["detection"]
    clip_path = resolve_clip_path(det.ClipPath)
    if clip_path is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    return FileResponse(
        path=clip_path,
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/detections/{detection_id}/clip/download")
def detection_clip_download(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    detection_id: int,
):
    """Explicit download -- gated by the user's CanDownload permission."""
    from fastapi import HTTPException
    from app.services.permission_service import can_download

    if not can_download(user):
        raise HTTPException(
            status_code=403,
            detail="Downloading has been disabled for your account by your administrator.",
        )

    item = get_visible_detection(db, user, detection_id)
    if item is None:
        raise HTTPException(status_code=404)

    det = item["detection"]
    clip_path = resolve_clip_path(det.ClipPath)
    if clip_path is None:
        raise HTTPException(status_code=404)

    import os
    download_name = os.path.basename(clip_path)

    return FileResponse(
        path=clip_path,
        media_type="audio/mpeg",
        filename=download_name,
        headers={"Accept-Ranges": "bytes"},
    )
