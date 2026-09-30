"""
Transcription routes.

    GET  /transcriptions              my transcriptions (search history)
    GET  /transcriptions/request      request form
    POST /transcriptions/request      submit request
    GET  /transcriptions/{id}         view transcript
"""

import math
import os
from datetime import date, datetime, time, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, right_panel_context
from app.models.detection import RecordingChunk, Transcript
from app.models.station import Station
from app.models.user import User, UserType
from app.services.station_picker_service import (
    get_tbfp_stations_for_picker,
    resolve_tbfp_ids_to_station_ids,
)
from app.templating import templates

router = APIRouter(prefix="/transcriptions", tags=["transcriptions"])


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


def _resolve_transcript_json_path(transcript) -> Optional[str]:
    """
    Returns the first JSON path that exists on disk, trying EarlyJsonPath
    before JsonPath, and also trying alternate drive letters (C:\\ vs D:\\)
    since paths may have been written by a pipeline on a different drive.
    """
    def try_path(p):
        if not p:
            return None
        if os.path.isfile(p):
            return p
        if p.lower().startswith("c:\\"):
            alt = "D:\\" + p[3:]
        elif p.lower().startswith("d:\\"):
            alt = "C:\\" + p[3:]
        else:
            return None
        return alt if os.path.isfile(alt) else None

    return try_path(transcript.EarlyJsonPath) or try_path(transcript.JsonPath)


@router.get("", response_class=HTMLResponse)
def transcriptions_list(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
):
    """My transcription requests -- scoped to the subscriber."""
    from app.services.transcription_service import list_requests
    from app.models.transcription_request import TranscriptionRequest
    from app.models.station import Station

    if user.user_type == UserType.INTERNAL:
        q = db.query(TranscriptionRequest)
    elif not user.SubscriberID:
        rows, total = [], 0
        q = None
    else:
        q = db.query(TranscriptionRequest).filter(
            TranscriptionRequest.SubscriberID == user.SubscriberID
        )

    if q is not None:
        total = q.count()
        rows = q.order_by(TranscriptionRequest.CreatedAt.desc()).offset((page-1)*50).limit(50).all()
    total_pages = max(1, math.ceil(total / 50))
    station_map = {s.StationID: s.StationName for s in db.query(Station).all()}

    return templates.TemplateResponse(
        request=request,
        name="app/transcriptions/list.html",
        context={
            "user": user,
            "rows": rows,
            "station_map": station_map,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            **right_panel_context(user, db, request),
        },
    )


@router.get("/request", response_class=HTMLResponse)
def transcription_request_form(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return templates.TemplateResponse(
        request=request,
        name="app/transcriptions/request.html",
        context={
            "user": user,
            "stations": get_tbfp_stations_for_picker(db),
            "error": None,
            "form": {},
            **right_panel_context(user, db, request),
        },
    )


@router.post("/request")
async def transcription_request_submit(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    date_from: Annotated[str, Form()] = "",
    date_to: Annotated[str, Form()] = "",
    output_format: Annotated[str, Form()] = "chunks",
):
    from app.services.transcription_service import create_request
    from app.services.token_service import get_balance, calculate_cost, calculate_cost_for_service, InsufficientTokensError
    from app.services.schedule_service import schedules_from_form, save_schedules, calculate_scheduled_hours

    def rerender(error: str):
        return templates.TemplateResponse(
            request=request,
            name="app/transcriptions/request.html",
            context={
                "user": user,
                "stations": get_tbfp_stations_for_picker(db),
                "error": error,
                "form": {
                    "date_from": date_from, "date_to": date_to,
                    "output_format": output_format,
                },
                **right_panel_context(user, db, request),
            },
            status_code=400,
        )

    raw_form = await request.form()
    tbfp_ids = [int(s) for s in raw_form.getlist("station_ids") if s.isdigit()]
    station_ids = resolve_tbfp_ids_to_station_ids(db, tbfp_ids)

    if not station_ids:
        return rerender("Please select at least one station.")
    if not date_from or not date_to:
        return rerender("Start date and end date are required.")

    try:
        df = date.fromisoformat(date_from)
        dt = date.fromisoformat(date_to)
    except ValueError:
        return rerender("Invalid date format. Use YYYY-MM-DD.")

    if dt < df:
        return rerender("End date must be on or after start date.")

    # Parse schedule windows
    schedule_dicts = schedules_from_form(raw_form)

    # Calculate cost using scheduled hours if schedule set
    if schedule_dicts:
        from decimal import Decimal
        single_day = df == dt
        all_days_mode = any(s['day'] == -1 for s in schedule_dicts)

        if single_day or all_days_mode:
            # Bill for total window minutes × number of days in range
            total_mins = sum(
                (s['to'].hour * 60 + s['to'].minute) - (s['from'].hour * 60 + s['from'].minute)
                for s in schedule_dicts
            )
            num_days = (dt - df).days + 1
            scheduled_hours = (total_mins / 60.0) * (num_days if all_days_mode else 1)
        else:
            sched_objs = [type('S', (), {'DayOfWeek': s['day'], 'TimeFrom': s['from'], 'TimeTo': s['to']})() for s in schedule_dicts]
            scheduled_hours = calculate_scheduled_hours(sched_objs, df, dt, 1)
        cost_per_station = Decimal(str(round(max(scheduled_hours, 1/60), 4)))
    else:
        cost_per_station = calculate_cost_for_service(db, user.SubscriberID, "transcription", 1, df, dt)

    total_cost = cost_per_station * len(station_ids)
    balance = get_balance(db, user.SubscriberID) if user.SubscriberID else 0
    if balance < total_cost:
        return rerender(
            f"Insufficient credits. This request requires {total_cost:,.0f} credits "
            f"({len(station_ids)} station(s) × {cost_per_station:,.0f}). "
            f"Your balance: {balance:,.0f}."
        )

    # Create one request per station
    try:
        # Derive time_from/time_to from schedule for chunk filtering
        req_time_from = None
        req_time_to = None
        if schedule_dicts:
            from datetime import time as _time
            all_froms = [s['from'] for s in schedule_dicts]
            all_tos = [s['to'] for s in schedule_dicts]
            req_time_from = min(all_froms).strftime('%H:%M')
            req_time_to = max(all_tos).strftime('%H:%M')

        for sid in station_ids:
            req = create_request(
                db=db,
                subscriber_id=user.SubscriberID,
                user_id=user.UserID,
                station_id=sid,
                date_from=df,
                date_to=dt,
                time_from=req_time_from,
                time_to=req_time_to,
                output_format=output_format,
                schedule_dicts=schedule_dicts if schedule_dicts else None,
                cost=cost_per_station,
            )
        db.commit()
    except InsufficientTokensError as e:
        db.rollback()
        return rerender(str(e))

    return RedirectResponse("/transcriptions", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{request_id}", response_class=HTMLResponse)
def transcription_detail(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request_id: int,
):
    from app.models.transcription_request import TranscriptionRequest
    from app.services.transcription_service import get_request_chunks

    treq = db.get(TranscriptionRequest, request_id)
    if treq is None or (user.user_type.value != "internal" and treq.SubscriberID != user.SubscriberID):
        return RedirectResponse("/transcriptions", status_code=303)

    station = db.get(Station, treq.StationID)
    chunks_data = []

    if treq.OutputFormat == "concatenated":
        # Show merged JSON if available
        merged_json = None
        if treq.MergedJsonPath:
            if os.path.isfile(treq.MergedJsonPath):
                merged_json = treq.MergedJsonPath
            else:
                # Try alternate drive letter
                alt = None
                p = treq.MergedJsonPath
                if p.lower().startswith("c:\\"):
                    alt = "D:\\" + p[3:]
                elif p.lower().startswith("d:\\"):
                    alt = "C:\\" + p[3:]
                if alt and os.path.isfile(alt):
                    merged_json = alt
        # Read merged JSON content to pass to template
        merged_json_content = None
        if merged_json:
            try:
                import json as _json
                with open(merged_json, "r", encoding="utf-8") as _f:
                    merged_json_content = _json.load(_f)
            except Exception:
                merged_json_content = None

        return templates.TemplateResponse(
            request=request,
            name="app/transcriptions/detail.html",
            context={
                "user": user,
                "treq": treq,
                "station": station,
                "is_concatenated": True,
                "merged_json_path": merged_json,
                "merged_json_content": merged_json_content,
                "merged_json_url": f"/transcriptions/{request_id}/merged-json" if merged_json else None,
                "has_audio": False,
                **right_panel_context(user, db, request),
            },
        )

    # Chunks format -- show list of individual chunks with audio/karaoke
    pairs = get_request_chunks(db, treq)
    from app.services.clip_service import resolve_chunk_audio_path
    for transcript, chunk in pairs:
        audio_path = (
            resolve_chunk_audio_path(chunk.EarlyAudioPath)
            or resolve_chunk_audio_path(chunk.AudioPath)
        )
        chunks_data.append({
            "transcript": transcript,
            "chunk": chunk,
            "has_audio": bool(audio_path),
            "has_json": bool(_resolve_transcript_json_path(transcript)),
        })

    return templates.TemplateResponse(
        request=request,
        name="app/transcriptions/detail.html",
        context={
            "user": user,
            "treq": treq,
            "station": station,
            "is_concatenated": False,
            "chunks_data": chunks_data,
            **right_panel_context(user, db, request),
        },
    )


@router.get("/{transcript_id}/audio")
def transcription_audio(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    transcript_id: int,
):
    """Serve the chunk's audio (EarlyAudioPath preferred, falls back to AudioPath)."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.services.clip_service import resolve_chunk_audio_path

    transcript = db.get(Transcript, transcript_id)
    if transcript is None or not transcript.ChunkID:
        raise HTTPException(status_code=404)

    chunk = db.get(RecordingChunk, transcript.ChunkID)
    if chunk is None:
        raise HTTPException(status_code=404)

    audio_path = (
        resolve_chunk_audio_path(chunk.EarlyAudioPath)
        or resolve_chunk_audio_path(chunk.AudioPath)
    )
    if audio_path is None:
        raise HTTPException(
            status_code=404,
            detail="Audio not yet available for this chunk. It will be available "
                   "shortly after transcription, or once the full pipeline move completes.",
        )

    return FileResponse(path=audio_path, media_type="audio/mpeg",
                        headers={"Accept-Ranges": "bytes"})


@router.get("/{transcript_id}/audio/download")
def transcription_audio_download(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    transcript_id: int,
):
    """Explicit download -- gated by CanDownload."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.services.clip_service import resolve_chunk_audio_path
    from app.services.permission_service import can_download
    import os

    if not can_download(user):
        raise HTTPException(
            status_code=403,
            detail="Downloading has been disabled for your account by your administrator.",
        )

    transcript = db.get(Transcript, transcript_id)
    if transcript is None or not transcript.ChunkID:
        raise HTTPException(status_code=404)

    chunk = db.get(RecordingChunk, transcript.ChunkID)
    if chunk is None:
        raise HTTPException(status_code=404)

    audio_path = (
        resolve_chunk_audio_path(chunk.EarlyAudioPath)
        or resolve_chunk_audio_path(chunk.AudioPath)
    )
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio not yet available.")

    return FileResponse(
        path=audio_path, media_type="audio/mpeg",
        filename=os.path.basename(audio_path),
        headers={"Accept-Ranges": "bytes"},
    )


@router.get("/{transcript_id}/transcript-json")
def transcription_json(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    transcript_id: int,
):
    """Serve the word-level timestamp JSON for karaoke sync."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404)

    json_path = _resolve_transcript_json_path(transcript)
    if json_path is None:
        raise HTTPException(status_code=404, detail="Transcript file not found.")

    return FileResponse(path=json_path, media_type="application/json")


@router.get("/chunks/{transcript_id}/audio")
def chunk_audio(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    transcript_id: int,
):
    """Serve audio for a specific chunk within a TranscriptionRequest."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.services.clip_service import resolve_chunk_audio_path

    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404)
    chunk = db.get(RecordingChunk, transcript.ChunkID)
    if chunk is None:
        raise HTTPException(status_code=404)
    audio_path = (
        resolve_chunk_audio_path(chunk.EarlyAudioPath)
        or resolve_chunk_audio_path(chunk.AudioPath)
    )
    if audio_path is None:
        raise HTTPException(status_code=404, detail="Audio not yet available.")
    return FileResponse(path=audio_path, media_type="audio/mpeg", headers={"Accept-Ranges": "bytes"})


@router.get("/chunks/{transcript_id}/transcript-json")
def chunk_transcript_json(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    transcript_id: int,
):
    """Serve word-timestamp JSON for a specific chunk."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    transcript = db.get(Transcript, transcript_id)
    if transcript is None:
        raise HTTPException(status_code=404)
    json_path = _resolve_transcript_json_path(transcript)
    if json_path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path=json_path, media_type="application/json")


@router.get("/{request_id}/merged-json")
def merged_transcript_json(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    request_id: int,
):
    """Serve the merged JSON for a concatenated TranscriptionRequest."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from app.models.transcription_request import TranscriptionRequest

    treq = db.get(TranscriptionRequest, request_id)
    if treq is None or (user.user_type.value != "internal" and treq.SubscriberID != user.SubscriberID):
        raise HTTPException(status_code=404)
    merged_path = None
    if treq.MergedJsonPath:
        if os.path.isfile(treq.MergedJsonPath):
            merged_path = treq.MergedJsonPath
        else:
            p = treq.MergedJsonPath
            alt = ("D:\\" + p[3:]) if p.lower().startswith("c:\\") else (("C:\\" + p[3:]) if p.lower().startswith("d:\\") else None)
            if alt and os.path.isfile(alt):
                merged_path = alt
    if not merged_path:
        raise HTTPException(status_code=404, detail="Merged transcript not yet available.")
    return FileResponse(path=merged_path, media_type="application/json")
