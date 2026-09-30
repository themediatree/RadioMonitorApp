"""
Pull API routes -- authenticated via API key (Bearer token).

    GET /api/detections          commercial airings
    GET /api/songs/detections    song airings
    GET /api/words/detections    keyword detections
    GET /api/transcriptions      transcript chunks
    GET /api/clips/<token>       signed clip URL redemption (1-hour TTL)

All endpoints scope data to the requesting Subscriber -- same scoping rules
as the dashboard. Clips are returned as signed URLs, not embedded bytes.

OOS (out-of-schedule) fields are included in detection responses:
  - out_of_schedule: true if the detection fell outside registered time windows
  - broadcast_from / broadcast_to: registered time windows (HH:MM:SS) for
    commercial detections; absent for song/word detections (use subscription
    schedule windows instead -- not currently returned inline).
"""

from datetime import date, datetime, time
from typing import Optional

from fastapi import HTTPException

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.database import SessionLocal, get_db
from app.deps import get_api_key_subscriber
from app.models.subscriber import Subscriber

router = APIRouter(prefix="/api", tags=["api"])


def _db():
    """Yield a fresh DB session for API routes."""
    from sqlalchemy.orm import Session
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _signed_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/clips/{token}"


def _seconds_to_hms(secs) -> Optional[str]:
    """Convert seconds-since-midnight (float/int) to HH:MM:SS string."""
    if secs is None:
        return None
    try:
        total = int(float(secs))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    except Exception:
        return None


def _fmt_time(t) -> Optional[str]:
    """Format a time/timedelta/string value as HH:MM:SS, or None."""
    if t is None:
        return None
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M:%S")
    s = str(t)[:8]
    return s if len(s) >= 5 else None


def _combine_dt(chunk_date, start_time) -> Optional[datetime]:
    """Combine a date and a time/string into a datetime for schedule checking."""
    if not chunk_date:
        return None
    try:
        if isinstance(start_time, time):
            return datetime.combine(chunk_date, start_time)
        if start_time:
            t = time.fromisoformat(str(start_time)[:8])
            return datetime.combine(chunk_date, t)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Commercial detections
# ---------------------------------------------------------------------------

@router.get("/detections")
def api_detections(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    station_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import aliased
    from sqlalchemy import and_, exists
    from app.models.detection import Detection
    from app.models.campaign import Commercial, CampaignCommercial
    from app.models.station import Station
    from app.services.api_key_service import create_signed_clip_token
    from app.services.schedule_service import get_campaign_station_schedules, is_detection_in_schedule

    db: Session = SessionLocal()
    try:
        c_det = aliased(Commercial)
        c_mine = aliased(Commercial)

        fp_match = exists().where(and_(
            c_det.CommercialID == Detection.CommercialID,
            c_det.FingerprintID.isnot(None),
            c_mine.FingerprintID == c_det.FingerprintID,
            c_mine.SubscriberID == subscriber.SubscriberID,
            c_mine.Status == "completed",
        ))
        direct_match = exists().where(and_(
            c_det.CommercialID == Detection.CommercialID,
            c_det.FingerprintID.is_(None),
            c_det.SubscriberID == subscriber.SubscriberID,
            c_det.Status == "completed",
        ))

        q = db.query(Detection).filter(fp_match | direct_match)
        if station_id:
            q = q.filter(Detection.StationID == station_id)
        if date_from:
            q = q.filter(Detection.CreatedAt >= date_from)
        if date_to:
            q = q.filter(Detection.CreatedAt <= date_to + " 23:59:59")

        total = q.count()
        rows = q.order_by(Detection.CreatedAt.desc()).offset((page - 1) * page_size).limit(page_size).all()

        station_map = {s.StationID: s.StationName for s in db.query(Station).all()}
        commercial_map = {c.CommercialID: c for c in db.query(Commercial).filter(
            Commercial.SubscriberID == subscriber.SubscriberID).all()}

        # Build commercial → campaign map for OOS schedule lookup
        commercial_ids = [d.CommercialID for d in rows]
        cc_rows = db.query(CampaignCommercial).filter(
            CampaignCommercial.CommercialID.in_(commercial_ids)
        ).all() if commercial_ids else []
        commercial_campaign_map = {cc.CommercialID: cc.CampaignID for cc in cc_rows}

        # Cache schedule lookups: (campaign_id, station_id) → [schedules]
        schedule_cache: dict = {}
        for d in rows:
            cid = commercial_campaign_map.get(d.CommercialID)
            if cid:
                key = (cid, d.StationID)
                if key not in schedule_cache:
                    schedule_cache[key] = get_campaign_station_schedules(db, cid, d.StationID)

        results = []
        for d in rows:
            clip_url = None
            if d.ClipPath:
                tok = create_signed_clip_token(db, d.ClipPath)
                clip_url = _signed_url(request, tok)
            db.commit()

            com = commercial_map.get(d.CommercialID)

            # OOS + broadcast schedule
            # Use start_time_sec (seconds since midnight) for the actual air time,
            # not CreatedAt which is the processing/detection timestamp.
            campaign_id = commercial_campaign_map.get(d.CommercialID)
            schedules = schedule_cache.get((campaign_id, d.StationID), []) if campaign_id else []
            air_dt = None
            if d.StartTimeSec is not None and d.CreatedAt:
                try:
                    total_s = int(float(d.StartTimeSec))
                    air_time = time(total_s // 3600, (total_s % 3600) // 60, total_s % 60)
                    air_dt = datetime.combine(d.CreatedAt.date(), air_time)
                except Exception:
                    air_dt = d.CreatedAt
            out_of_schedule = (
                not is_detection_in_schedule(schedules, air_dt)
                if schedules and air_dt else False
            )
            from_parts = [_fmt_time(s.TimeFrom) for s in schedules if _fmt_time(s.TimeFrom)]
            to_parts   = [_fmt_time(s.TimeTo)   for s in schedules if _fmt_time(s.TimeTo)]

            results.append({
                "detection_id":     d.DetectionID,
                "commercial_id":    d.CommercialID,
                "tape_id":          com.DisplayTapeID if com else None,
                "commercial_name":  com.CommercialName if com else None,
                "station_id":       d.StationID,
                "station_name":     station_map.get(d.StationID),
                "detected_at":      d.CreatedAt.isoformat() if d.CreatedAt else None,
                "start_time":       _seconds_to_hms(d.StartTimeSec),
                "end_time":         _seconds_to_hms(d.EndTimeSec),
                "category":         d.Category,
                "clip_url":         clip_url,
                "clip_url_expires_in": "3600s",
                "out_of_schedule":  out_of_schedule,
                "broadcast_from":   " | ".join(from_parts) if from_parts else None,
                "broadcast_to":     " | ".join(to_parts)   if to_parts   else None,
            })

        return JSONResponse({"total": total, "page": page, "page_size": page_size, "results": results})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Detections query failed: {str(e)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Song detections
# ---------------------------------------------------------------------------

@router.get("/songs/detections")
def api_song_detections(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    station_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    from app.models.detection import SongDetection, RecordingChunk
    from app.models.station import Station
    from app.models.detection import ClientSubscription
    from app.services.api_key_service import create_signed_clip_token
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    import json as _json
    from sqlalchemy import or_, and_

    db = SessionLocal()
    try:
        q = db.query(SongDetection).join(RecordingChunk, RecordingChunk.ChunkID == SongDetection.ChunkID)

        # Scope to subscriber's active song subscriptions matched by title + station + date
        # Match subscriptions that were active during the requested date window.
        # Use date overlap instead of Status (which moves to 'expired' after EndDate).
        q_subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == subscriber.SubscriberID,
            ClientSubscription.SubscriptionType.in_(["song_track", "song_title"]),
        )
        if date_from:
            q_subs = q_subs.filter(
                (ClientSubscription.EndDate == None) | (ClientSubscription.EndDate >= date_from)
            )
        if date_to:
            q_subs = q_subs.filter(ClientSubscription.StartDate <= date_to)
        subs = q_subs.all()

        if not subs:
            return JSONResponse({"total": 0, "page": page, "page_size": page_size, "results": []})

        # Pre-load subscription schedules
        sub_schedule_map = {
            s.SubscriptionID: get_subscription_schedules(db, s.SubscriptionID)
            for s in subs
        }

        sub_filters = []
        for s in subs:
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            conds = [
                or_(
                    SongDetection.Title.ilike(f"%{s.TargetValue}%") if s.TargetValue else False,
                    SongDetection.Title.ilike(f"%{s.Title}%") if s.Title else False,
                ),
                RecordingChunk.ChunkDate >= str(s.StartDate),
            ]
            if s.EndDate:
                conds.append(RecordingChunk.ChunkDate <= str(s.EndDate))
            if station_ids:
                conds.append(SongDetection.StationID.in_(station_ids))
            sub_filters.append(and_(*conds))

        q = q.filter(or_(*sub_filters))

        if station_id:
            q = q.filter(SongDetection.StationID == station_id)
        if date_from:
            q = q.filter(RecordingChunk.ChunkDate >= date_from)
        if date_to:
            q = q.filter(RecordingChunk.ChunkDate <= date_to)

        total = q.count()
        rows = q.order_by(RecordingChunk.ChunkDate.desc()).offset((page - 1) * page_size).limit(page_size).all()
        station_map = {s.StationID: s.StationName for s in db.query(Station).all()}
        chunk_map = {c.ChunkID: c for c in db.query(RecordingChunk).filter(
            RecordingChunk.ChunkID.in_([d.ChunkID for d in rows])).all()}

        def _find_sub(det, chunk):
            """Return the best-matching subscription for a detection."""
            for s in subs:
                station_ids = []
                if s.StationFilter:
                    try:
                        station_ids = _json.loads(s.StationFilter)
                    except Exception:
                        pass
                if station_ids and det.StationID not in station_ids:
                    continue
                if chunk and s.StartDate and str(chunk.ChunkDate) < str(s.StartDate):
                    continue
                if chunk and s.EndDate and str(chunk.ChunkDate) > str(s.EndDate):
                    continue
                title_lower = (det.Title or "").lower()
                if s.TargetValue and s.TargetValue.lower() in title_lower:
                    return s
                if s.Title and s.Title.lower() in title_lower:
                    return s
            return None

        results = []
        for d in rows:
            chunk = chunk_map.get(d.ChunkID)
            clip_url = None
            if d.ClipPath:
                tok = create_signed_clip_token(db, d.ClipPath)
                clip_url = _signed_url(request, tok)
                db.commit()

            # OOS check
            matching_sub = _find_sub(d, chunk)
            schedules = sub_schedule_map.get(matching_sub.SubscriptionID, []) if matching_sub else []
            det_dt = _combine_dt(chunk.ChunkDate if chunk else None,
                                 chunk.StartTime if chunk else None)
            out_of_schedule = (
                not is_detection_in_schedule(schedules, det_dt)
                if schedules and det_dt else False
            )

            results.append({
                "detection_id":    d.SongDetectionID,
                "station_id":      d.StationID,
                "station_name":    station_map.get(d.StationID),
                "chunk_date":      str(chunk.ChunkDate) if chunk else None,
                "start_time":      _seconds_to_hms(d.StartTime),
                "title":           d.Title,
                "artist":          d.Artist,
                "clip_url":        clip_url,
                "clip_url_expires_in": "3600s",
                "out_of_schedule": out_of_schedule,
            })

        return JSONResponse({"total": total, "page": page, "page_size": page_size, "results": results})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Song detections query failed: {str(e)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Keyword detections
# ---------------------------------------------------------------------------

@router.get("/words/detections")
def api_word_detections(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    station_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    from app.models.detection import WordDetection, RecordingChunk
    from app.models.station import Station
    from app.models.detection import ClientSubscription
    from app.services.api_key_service import create_signed_clip_token
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    import json as _json
    from sqlalchemy import or_, and_

    db = SessionLocal()
    try:
        q = db.query(WordDetection).join(RecordingChunk, RecordingChunk.ChunkID == WordDetection.ChunkID)

        q_subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == subscriber.SubscriberID,
            ClientSubscription.SubscriptionType == "keyword",
        )
        if date_from:
            q_subs = q_subs.filter(
                (ClientSubscription.EndDate == None) | (ClientSubscription.EndDate >= date_from)
            )
        if date_to:
            q_subs = q_subs.filter(ClientSubscription.StartDate <= date_to)
        subs = q_subs.all()

        if not subs:
            return JSONResponse({"total": 0, "page": page, "page_size": page_size, "results": []})

        # Pre-load subscription schedules
        sub_schedule_map = {
            s.SubscriptionID: get_subscription_schedules(db, s.SubscriptionID)
            for s in subs
        }

        sub_filters = []
        for s in subs:
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            conds = [
                WordDetection.Keyword.ilike(f"%{s.TargetValue}%") if s.TargetValue else False,
                RecordingChunk.ChunkDate >= str(s.StartDate),
            ]
            if s.EndDate:
                conds.append(RecordingChunk.ChunkDate <= str(s.EndDate))
            if station_ids:
                conds.append(WordDetection.StationID.in_(station_ids))
            sub_filters.append(and_(*conds))

        q = q.filter(or_(*sub_filters))

        if station_id:
            q = q.filter(WordDetection.StationID == station_id)
        if keyword:
            q = q.filter(WordDetection.Keyword.ilike(f"%{keyword}%"))
        if date_from:
            q = q.filter(RecordingChunk.ChunkDate >= date_from)
        if date_to:
            q = q.filter(RecordingChunk.ChunkDate <= date_to)

        total = q.count()
        rows = q.order_by(RecordingChunk.ChunkDate.desc()).offset((page - 1) * page_size).limit(page_size).all()
        station_map = {s.StationID: s.StationName for s in db.query(Station).all()}
        chunk_map = {c.ChunkID: c for c in db.query(RecordingChunk).filter(
            RecordingChunk.ChunkID.in_([d.ChunkID for d in rows])).all()}

        def _find_sub(det, chunk):
            """Return the best-matching subscription for a word detection."""
            for s in subs:
                station_ids = []
                if s.StationFilter:
                    try:
                        station_ids = _json.loads(s.StationFilter)
                    except Exception:
                        pass
                if station_ids and det.StationID not in station_ids:
                    continue
                if chunk and s.StartDate and str(chunk.ChunkDate) < str(s.StartDate):
                    continue
                if chunk and s.EndDate and str(chunk.ChunkDate) > str(s.EndDate):
                    continue
                if s.TargetValue and (det.Keyword or "").lower().find(s.TargetValue.lower()) >= 0:
                    return s
            return None

        results = []
        for d in rows:
            chunk = chunk_map.get(d.ChunkID)
            clip_url = None
            if d.ClipPath:
                tok = create_signed_clip_token(db, d.ClipPath)
                clip_url = _signed_url(request, tok)
                db.commit()

            # OOS check
            matching_sub = _find_sub(d, chunk)
            schedules = sub_schedule_map.get(matching_sub.SubscriptionID, []) if matching_sub else []
            det_dt = _combine_dt(chunk.ChunkDate if chunk else None,
                                 chunk.StartTime if chunk else None)
            out_of_schedule = (
                not is_detection_in_schedule(schedules, det_dt)
                if schedules and det_dt else False
            )

            results.append({
                "detection_id":    d.WordDetectionID,
                "keyword":         d.Keyword,
                "station_id":      d.StationID,
                "station_name":    station_map.get(d.StationID),
                "chunk_date":      str(chunk.ChunkDate) if chunk else None,
                "start_time":      _seconds_to_hms(d.StartTime),
                "end_time_sec":    float(d.EndTime)   if d.EndTime   is not None else None,
                "clip_url":        clip_url,
                "clip_url_expires_in": "3600s",
                "out_of_schedule": out_of_schedule,
            })

        return JSONResponse({"total": total, "page": page, "page_size": page_size, "results": results})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Transcriptions
# ---------------------------------------------------------------------------

@router.get("/transcriptions")
def api_transcriptions(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    station_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    time_from: Optional[str] = Query(None, description="HH:MM"),
    time_to: Optional[str] = Query(None, description="HH:MM"),
    output_format: Optional[str] = Query(None, description="chunks or concatenated"),
    status: Optional[str] = Query(None, description="pending, ready, or partial"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """
    Returns the subscriber's TranscriptionRequest rows. Read-only, no billing.
    Use POST /api/transcriptions to create a new request.
    """
    from app.models.transcription_request import TranscriptionRequest
    from app.models.station import Station

    db = SessionLocal()
    try:
        q = db.query(TranscriptionRequest).filter(
            TranscriptionRequest.SubscriberID == subscriber.SubscriberID
        )
        if station_id:
            q = q.filter(TranscriptionRequest.StationID == station_id)
        if date_from:
            q = q.filter(TranscriptionRequest.DateFrom >= date_from)
        if date_to:
            q = q.filter(TranscriptionRequest.DateTo <= date_to)
        if time_from:
            q = q.filter(TranscriptionRequest.TimeFrom >= time_from)
        if time_to:
            q = q.filter(TranscriptionRequest.TimeTo <= time_to)
        if output_format:
            if output_format not in ("chunks", "concatenated"):
                raise HTTPException(status_code=422, detail="output_format must be 'chunks' or 'concatenated'")
            q = q.filter(TranscriptionRequest.OutputFormat == output_format)
        if status:
            if status not in ("pending", "ready", "partial"):
                raise HTTPException(status_code=422, detail="status must be 'pending', 'ready', or 'partial'")
            q = q.filter(TranscriptionRequest.Status == status)

        station_map = {s.StationID: s.StationName for s in db.query(Station).all()}
        total = q.count()
        rows = q.order_by(TranscriptionRequest.CreatedAt.desc()).offset(
            (page - 1) * page_size).limit(page_size).all()

        results = []
        for r in rows:
            results.append({
                "request_id": r.RequestID,
                "station_id": r.StationID,
                "station_name": station_map.get(r.StationID),
                "date_from": str(r.DateFrom),
                "date_to": str(r.DateTo),
                "time_from": r.TimeFrom,
                "time_to": r.TimeTo,
                "output_format": r.OutputFormat,
                "status": r.Status,
                "chunk_count": r.ChunkCount,
                "tokens_debited": float(r.TokensDebited),
                "source": r.Source,
                "created_at": r.CreatedAt.isoformat() if r.CreatedAt else None,
                "processed_at": r.ProcessedAt.isoformat() if r.ProcessedAt else None,
                "merged_json_available": bool(r.MergedJsonPath),
            })

        return JSONResponse({"total": total, "page": page, "page_size": page_size, "results": results})

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcriptions query failed: {str(e)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Signed clip redemption
# ---------------------------------------------------------------------------

@router.get("/clips/{token}")
def redeem_clip(token: str):
    """Redeems a signed clip token and serves the audio file."""
    from app.services.api_key_service import redeem_signed_clip_token
    from app.services.clip_service import resolve_clip_path, guess_content_type
    from app.models.api_key import SignedClipToken as SCT
    from datetime import datetime

    db = SessionLocal()
    try:
        row = db.query(SCT).filter(SCT.Token == token).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Clip token not found.")
        if row.UsedAt is not None:
            raise HTTPException(status_code=404, detail="Clip token already used.")
        if row.ExpiresAt <= datetime.now():
            raise HTTPException(status_code=404, detail=f"Clip token expired at {row.ExpiresAt} (now={datetime.now()}).")
        stored_path = row.ClipPath
        resolved = resolve_clip_path(stored_path)
        if resolved is None:
            raise HTTPException(status_code=404, detail=f"Clip file not found at: {stored_path}")
        return FileResponse(
            path=resolved,
            media_type=guess_content_type(resolved),
            headers={"Accept-Ranges": "bytes"},
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Stations reference
# ---------------------------------------------------------------------------

@router.get("/stations")
def api_stations(
    subscriber: Subscriber = Depends(get_api_key_subscriber),
):
    """
    Returns stations this subscriber has registered services against
    (campaigns, song/keyword subscriptions, transcription requests).
    Use the returned station_id values as filters in other GET endpoints.
    For the full list of station names available for POST registrations,
    use GET /api/stations/available.
    """
    from app.models.station import Station
    from app.models.campaign import Campaign, CampaignStation
    from app.models.detection import ClientSubscription
    from app.models.transcription_request import TranscriptionRequest
    import json as _json
    from sqlalchemy import union

    db = SessionLocal()
    try:
        # Station IDs from campaigns
        campaign_station_ids = (
            db.query(CampaignStation.StationID)
            .join(Campaign, Campaign.CampaignID == CampaignStation.CampaignID)
            .filter(Campaign.SubscriberID == subscriber.SubscriberID)
        )

        # Station IDs from transcription requests
        transcription_station_ids = (
            db.query(TranscriptionRequest.StationID)
            .filter(TranscriptionRequest.SubscriberID == subscriber.SubscriberID)
        )

        # Station IDs from song/keyword subscriptions (stored as JSON array in StationFilter)
        subs = db.query(ClientSubscription.StationFilter).filter(
            ClientSubscription.SubscriberID == subscriber.SubscriberID
        ).all()
        sub_station_ids = set()
        for (sf,) in subs:
            if sf:
                try:
                    sub_station_ids.update(_json.loads(sf))
                except Exception:
                    pass

        # Combine all station IDs
        db_station_ids = {r[0] for r in campaign_station_ids.all()}
        db_station_ids |= {r[0] for r in transcription_station_ids.all()}
        db_station_ids |= sub_station_ids
        db_station_ids.discard(6)  # exclude internal station

        if not db_station_ids:
            return JSONResponse({"results": []})

        stations = (
            db.query(Station)
            .filter(Station.StationID.in_(db_station_ids))
            .order_by(Station.StationName)
            .all()
        )
        return JSONResponse({
            "results": [
                {"station_id": s.StationID, "name": s.StationName}
                for s in stations
            ]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stations query failed: {str(e)}")
    finally:
        db.close()


@router.get("/stations/available")
def api_stations_available(
    subscriber: Subscriber = Depends(get_api_key_subscriber),
):
    """
    Returns all stations from the NOCTIV master list (dbo.TBFPStation).
    Use the returned names exactly when registering new services via POST
    endpoints. If a station has not been registered before it will be
    auto-provisioned on first use; station_id is informational only.
    """
    from app.models.tbfp_station import TBFPStation
    db = SessionLocal()
    try:
        rows = (
            db.query(TBFPStation)
            .filter(TBFPStation.Station_name.isnot(None))
            .order_by(TBFPStation.Station_name)
            .all()
        )
        # Deduplicate by station name — one row per unique name.
        # Prefer a row that has a StreamURL; otherwise take the first occurrence.
        seen: dict[str, TBFPStation] = {}
        for r in rows:
            key = r.Station_name.strip().lower()
            if key not in seen:
                seen[key] = r
            elif not seen[key].StreamURL and r.StreamURL:
                seen[key] = r  # upgrade to one that has a stream URL

        return JSONResponse({
            "results": [
                {
                    "name":       s.Station_name,
                    "province":   s.Province,
                    "stream_url": s.StreamURL,
                }
                for s in sorted(seen.values(), key=lambda x: x.Station_name.lower())
            ]
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Available stations query failed: {str(e)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Transcription request detail -- chunks
# ---------------------------------------------------------------------------

@router.get("/transcriptions/{request_id}/chunks")
def api_transcription_chunks(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    request_id: int = None,
):
    """
    Returns the individual transcript chunks for a chunks-format
    TranscriptionRequest. Each chunk includes FullText (where available),
    word-level JSON URL, and signed audio URL (1-hour TTL).
    """
    from app.models.transcription_request import TranscriptionRequest
    from app.models.detection import Transcript, RecordingChunk
    from app.services.api_key_service import create_signed_clip_token
    from app.services.clip_service import resolve_chunk_audio_path

    db = SessionLocal()
    try:
        treq = db.get(TranscriptionRequest, request_id)
        if treq is None or treq.SubscriberID != subscriber.SubscriberID:
            raise HTTPException(status_code=404, detail="Transcription request not found.")
        if treq.OutputFormat != "chunks":
            raise HTTPException(
                status_code=400,
                detail="This request uses concatenated format. Use GET /api/transcriptions/{id}/merged instead."
            )
        if treq.Status == "pending":
            return JSONResponse({
                "request_id": request_id,
                "status": "pending",
                "message": "Transcription not yet available. Check back later.",
                "results": []
            })

        q = (
            db.query(Transcript, RecordingChunk)
            .join(RecordingChunk, RecordingChunk.ChunkID == Transcript.ChunkID)
            .filter(
                RecordingChunk.StationID == treq.StationID,
                RecordingChunk.ChunkDate >= str(treq.DateFrom),
                RecordingChunk.ChunkDate <= str(treq.DateTo),
            )
        )
        if treq.TimeFrom:
            q = q.filter(RecordingChunk.StartTime >= treq.TimeFrom)
        if treq.TimeTo:
            q = q.filter(RecordingChunk.StartTime <= treq.TimeTo)
        pairs = q.order_by(RecordingChunk.ChunkDate, RecordingChunk.StartTime).all()

        results = []
        for transcript, chunk in pairs:
            audio_url = None
            audio_path = (
                resolve_chunk_audio_path(chunk.EarlyAudioPath)
                or resolve_chunk_audio_path(chunk.AudioPath)
            )
            if audio_path:
                tok = create_signed_clip_token(db, audio_path)
                audio_url = _signed_url(request, tok)
                db.commit()

            text = transcript.FullText
            if not text:
                json_path = transcript.EarlyJsonPath or transcript.JsonPath
                if json_path:
                    try:
                        import json as _json, os
                        if os.path.isfile(json_path):
                            data = _json.loads(open(json_path).read())
                            text = (
                                data.get("results", {})
                                .get("channels", [{}])[0]
                                .get("alternatives", [{}])[0]
                                .get("transcript", "")
                            )
                    except Exception:
                        pass

            results.append({
                "transcript_id": transcript.TranscriptID,
                "chunk_date": str(chunk.ChunkDate) if chunk.ChunkDate else None,
                "start_time": str(chunk.StartTime) if chunk.StartTime else None,
                "duration_sec": chunk.DurationSec,
                "word_count": transcript.WordCount,
                "text": text or "",
                "audio_url": audio_url,
                "audio_url_expires_in": "3600s",
            })

        return JSONResponse({
            "request_id": request_id,
            "station_id": treq.StationID,
            "date_from": str(treq.DateFrom),
            "date_to": str(treq.DateTo),
            "status": treq.Status,
            "chunk_count": len(results),
            "results": results,
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chunks query failed: {str(e)}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Transcription request detail -- merged (concatenated)
# ---------------------------------------------------------------------------

@router.get("/transcriptions/{request_id}/merged")
def api_transcription_merged(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    request_id: int = None,
):
    """
    Returns the merged transcript text for a concatenated-format
    TranscriptionRequest. Includes the full word list with timestamps
    and gap markers where audio was not transcribed.
    """
    from app.models.transcription_request import TranscriptionRequest
    import json as _json, os

    db = SessionLocal()
    try:
        treq = db.get(TranscriptionRequest, request_id)
        if treq is None or treq.SubscriberID != subscriber.SubscriberID:
            raise HTTPException(status_code=404, detail="Transcription request not found.")
        if treq.OutputFormat != "concatenated":
            raise HTTPException(
                status_code=400,
                detail="This request uses chunks format. Use GET /api/transcriptions/{id}/chunks instead."
            )
        if treq.Status == "pending":
            return JSONResponse({
                "request_id": request_id,
                "status": "pending",
                "message": "Transcription not yet available. Check back later.",
                "transcript": None,
            })

        if not treq.MergedJsonPath or not os.path.isfile(treq.MergedJsonPath):
            raise HTTPException(
                status_code=404,
                detail="Merged transcript file not yet available."
            )

        data = _json.loads(open(treq.MergedJsonPath).read())
        words = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("words", [])
        )
        full_text = " ".join(
            w.get("word", "") for w in words if not w.get("is_gap")
        )

        return JSONResponse({
            "request_id": request_id,
            "station_id": treq.StationID,
            "date_from": str(treq.DateFrom),
            "date_to": str(treq.DateTo),
            "status": treq.Status,
            "chunk_count": treq.ChunkCount,
            "transcript": full_text,
            "words": words,
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merged transcript query failed: {str(e)}")
    finally:
        db.close()
