"""
Push API routes -- authenticated via API key (Bearer token).

    POST /api/commercials    register a commercial (multipart: fields + audio)
    POST /api/songs          subscribe to a song
    POST /api/keywords       subscribe to a keyword/phrase

Same validation, token debiting, and service calls as dashboard forms.

Schedule windows (optional for all POST endpoints):
  Pass schedule_windows as a JSON string to register broadcast/subscription
  time windows -- the same OOS checking applied on the web app will then
  apply to API-sourced detections.

  Format:
    [{"day": 0, "from": "06:00", "to": "09:00"}, ...]

  day: 0=Monday ... 6=Sunday, -1=all days (every day of the week)
  from/to: HH:MM (24-hour)

  Example (all days, 06:00-18:00):
    schedule_windows=[{"day": -1, "from": "06:00", "to": "18:00"}]

  Omit schedule_windows (or pass an empty list) to register without a
  time restriction -- all detections will be considered in-schedule.
"""

from typing import Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.database import SessionLocal
from app.deps import get_api_key_subscriber
from app.models.subscriber import Subscriber

router = APIRouter(prefix="/api", tags=["api-push"])


def _parse_schedule_windows(schedule_windows: str) -> list[dict]:
    """
    Parse the schedule_windows JSON string into a list of schedule dicts
    compatible with schedule_service.save_schedules / save_campaign_station_schedules.

    Returns [] on empty/missing input. Raises HTTPException 422 on bad format.
    """
    import json as _json
    from datetime import time

    if not schedule_windows or schedule_windows.strip() in ("", "[]", "null"):
        return []

    try:
        raw = _json.loads(schedule_windows)
    except _json.JSONDecodeError:
        raise HTTPException(
            status_code=422,
            detail="schedule_windows must be a valid JSON array."
        )

    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="schedule_windows must be a JSON array.")

    result = []
    for i, entry in enumerate(raw):
        try:
            day = int(entry["day"])
            tfrom = time.fromisoformat(entry["from"])
            tto   = time.fromisoformat(entry["to"])
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=422,
                detail=f"schedule_windows[{i}] invalid: {exc}. "
                       f"Expected {{\"day\": 0-6 or -1, \"from\": \"HH:MM\", \"to\": \"HH:MM\"}}."
            )
        if not (-1 <= day <= 6):
            raise HTTPException(
                status_code=422,
                detail=f"schedule_windows[{i}]: day must be -1 (all days) or 0 (Mon) … 6 (Sun)."
            )
        if tto <= tfrom:
            raise HTTPException(
                status_code=422,
                detail=f"schedule_windows[{i}]: 'to' must be after 'from'."
            )
        result.append({"day": day, "from": tfrom.strftime("%H:%M"), "to": tto.strftime("%H:%M")})

    return result


def _resolve_station_ids(db, stations_csv: str) -> list[int]:
    """
    Resolve a comma-separated list of station names to Station.StationID values.

    Resolution order (mirrors web app behaviour):
      1. Match name in dbo.Station (case-insensitive). Re-activate if IsActive=False.
      2. If not found, look up in dbo.TBFPStation.Station_name and auto-provision
         a new dbo.Station row (StationName + StreamURL, IsActive=True).
      3. If not found in either table, raise 422.
    """
    from app.models.station import Station
    from app.models.tbfp_station import TBFPStation

    station_rows = {s.StationName.lower(): s for s in db.query(Station).all()}
    tbfp_rows    = {
        r.Station_name.lower(): r
        for r in db.query(TBFPStation).filter(TBFPStation.Station_name.isnot(None)).all()
    }

    station_ids, unknown = [], []
    for name in [n.strip() for n in stations_csv.split(",") if n.strip()]:
        key = name.lower()
        row = station_rows.get(key)
        if row:
            if not row.IsActive:
                row.IsActive = True
            station_ids.append(row.StationID)
        else:
            tbfp = tbfp_rows.get(key)
            if tbfp:
                # Auto-provision into dbo.Station exactly as the web app does
                new_station = Station(
                    StationName=tbfp.Station_name,
                    StreamURL=tbfp.StreamURL,
                    IsActive=True,
                )
                db.add(new_station)
                db.flush()  # assigns StationID
                station_rows[key] = new_station  # avoid duplicate if name appears twice in CSV
                station_ids.append(new_station.StationID)
            else:
                unknown.append(name)

    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown stations: {', '.join(unknown)}")
    if not station_ids:
        raise HTTPException(status_code=422, detail="At least one station required.")
    return station_ids


# ---------------------------------------------------------------------------
# Register commercial
# ---------------------------------------------------------------------------

@router.post("/commercials")
async def api_register_commercial(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    tape_id: str = Form(...),
    campaign_name: str = Form(...),
    commercial_type: str = Form(...),
    brand: str = Form(default=""),
    stations: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(..., description="YYYY-MM-DD, required"),
    liveread_text: str = Form(default=""),
    audio: Optional[UploadFile] = File(default=None),
    schedule_windows: str = Form(
        default="",
        description=(
            'Optional JSON array of broadcast time windows per station. '
            'E.g. [{"day":-1,"from":"06:00","to":"18:00"}]. '
            'day: -1=all days, 0=Mon … 6=Sun. Omit for no time restriction.'
        ),
    ),
):
    from datetime import date as date_type
    from app.models.station import Station
    from app.models.campaign import Commercial, CampaignCommercial
    from app.services import registration_service
    from app.services.token_service import get_balance, calculate_cost, calculate_cost_for_service, debit
    from app.services.schedule_service import save_campaign_station_schedules
    import tempfile, os, shutil

    schedule_dicts = _parse_schedule_windows(schedule_windows)

    db = SessionLocal()
    tmp_dir = None
    try:
        if commercial_type not in ("generic", "liveread"):
            raise HTTPException(status_code=422, detail="commercial_type must be 'generic' or 'liveread'")

        station_ids = _resolve_station_ids(db, stations)

        try:
            sd = date_type.fromisoformat(start_date)
            ed = date_type.fromisoformat(end_date)
            registration_service.validate_campaign_dates(sd, ed)
            clean_tape_id = registration_service.sanitize_tape_id(tape_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")
        except Exception as e:
            raise HTTPException(status_code=422, detail=str(e))

        cost = calculate_cost_for_service(db, subscriber.SubscriberID, "commercial", len(station_ids), sd, ed)
        if get_balance(db, subscriber.SubscriberID) < cost:
            raise HTTPException(status_code=402, detail=f"Insufficient credits. Need {cost}.")

        if commercial_type == "generic" and audio is None:
            raise HTTPException(status_code=422, detail="Audio file required for generic commercials.")
        if commercial_type == "liveread" and not liveread_text:
            raise HTTPException(status_code=422, detail="liveread_text required for liveread commercials.")

        tmp_dir = tempfile.mkdtemp(prefix="api_upload_")
        fingerprint_id = None
        staged_path = None

        if commercial_type == "generic" and audio:
            from app.utils.audio import convert_to_mp3, AudioError
            from app.utils.fingerprint import compute_fingerprint_id
            suffix = os.path.splitext(audio.filename or ".mp3")[1].lower() or ".mp3"
            raw_path = os.path.join(tmp_dir, f"upload{suffix}")
            with open(raw_path, "wb") as f:
                f.write(await audio.read())
            final_src = os.path.join(tmp_dir, "final.mp3")
            try:
                convert_to_mp3(raw_path, final_src)
            except AudioError as e:
                raise HTTPException(status_code=422, detail=f"Audio conversion failed: {e}")
            try:
                fingerprint_id, _ = compute_fingerprint_id(final_src)
            except RuntimeError as e:
                raise HTTPException(status_code=422, detail=f"Fingerprinting failed: {e}")
            staged_path = final_src
        elif commercial_type == "liveread":
            text_path = os.path.join(tmp_dir, f"{clean_tape_id}.txt")
            with open(text_path, "w", encoding="utf-8") as f:
                f.write(liveread_text)
            staged_path = text_path

        station_names = registration_service.resolve_station_names(db, station_ids)
        campaign, _ = registration_service.get_or_create_campaign(
            db, subscriber_id=subscriber.SubscriberID,
            name=campaign_name,
            start_date=sd, end_date=ed,
        )

        from app.models.campaign import CampaignStation
        existing_station_ids = {
            cs.StationID for cs in db.query(CampaignStation)
            .filter(CampaignStation.CampaignID == campaign.CampaignID).all()
        }
        for sid in station_ids:
            if sid not in existing_station_ids:
                db.add(CampaignStation(CampaignID=campaign.CampaignID, StationID=sid))
        db.flush()

        commercial = Commercial(
            SubscriberID=subscriber.SubscriberID,
            CommercialName=f"{subscriber.SubscriberID}_{clean_tape_id}",
            DisplayTapeID=clean_tape_id,
            Brand=brand or None,
            CommercialType=commercial_type,
            FingerprintID=fingerprint_id,
            Status="pending",
            Source="api",
        )
        db.add(commercial)
        db.flush()
        db.add(CampaignCommercial(
            CampaignID=campaign.CampaignID,
            CommercialID=commercial.CommercialID,
        ))
        db.flush()

        # Save broadcast schedule windows for each station (if provided)
        if schedule_dicts:
            for sid in station_ids:
                save_campaign_station_schedules(
                    db=db,
                    campaign_id=campaign.CampaignID,
                    station_id=sid,
                    schedule_dicts=schedule_dicts,
                )

        if staged_path:
            try:
                registration_service.fan_out_to_staging(
                    station_names=station_names,
                    category="liveread" if commercial_type == "liveread" else "generic",
                    tape_id=clean_tape_id,
                    source_path=staged_path,
                    ext=".mp3" if commercial_type == "generic" else ".txt",
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"File staging failed: {e}")

        debit(db, subscriber_id=subscriber.SubscriberID, amount=cost,
              description=f"Commercial registration: {clean_tape_id}",
              reference_id=commercial.CommercialID, reference_type="commercial",
              source="api")
        db.commit()

        return JSONResponse(status_code=201, content={
            "commercial_id":    commercial.CommercialID,
            "campaign_id":      campaign.CampaignID,
            "tape_id":          commercial.DisplayTapeID,
            "status":           commercial.Status,
            "tokens_debited":   float(cost),
            "schedule_windows": len(schedule_dicts),
        })
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        db.close()


# ---------------------------------------------------------------------------
# Subscribe to song
# ---------------------------------------------------------------------------

@router.post("/songs")
async def api_subscribe_song(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    title: str = Form(default=""),
    artist: str = Form(default=""),
    track_id: str = Form(default=""),
    stations: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(..., description="YYYY-MM-DD, required"),
    schedule_windows: str = Form(
        default="",
        description=(
            'Optional JSON array of monitoring time windows. '
            'E.g. [{"day":-1,"from":"06:00","to":"18:00"}]. '
            'day: -1=all days, 0=Mon … 6=Sun. Omit for no time restriction.'
        ),
    ),
):
    from datetime import date as date_type
    from app.models.station import Station
    from app.services.subscription_service import create_song_subscription
    from app.services.token_service import get_balance, calculate_cost, calculate_cost_for_service, debit
    from app.services.schedule_service import save_schedules

    if not title and not track_id:
        raise HTTPException(status_code=422, detail="Provide title or track_id.")

    schedule_dicts = _parse_schedule_windows(schedule_windows)

    db = SessionLocal()
    try:
        station_ids = _resolve_station_ids(db, stations)

        try:
            sd = date_type.fromisoformat(start_date)
            ed = date_type.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

        cost = calculate_cost_for_service(db, subscriber.SubscriberID, "song", len(station_ids), sd, ed)
        if get_balance(db, subscriber.SubscriberID) < cost:
            raise HTTPException(status_code=402, detail=f"Insufficient credits. Need {cost}.")

        sub, _ = create_song_subscription(
            db=db,
            subscriber_id=subscriber.SubscriberID,
            title=title,
            artist=artist,
            track_id=track_id,
            station_ids=station_ids,
            start_date=sd,
            end_date=ed,
            created_by_user_id=None,
        )
        sub.Source = 'api'

        # Save monitoring schedule windows (if provided)
        if schedule_dicts:
            save_schedules(db=db, subscription_id=sub.SubscriptionID, schedule_dicts=schedule_dicts)

        debit(db, subscriber_id=subscriber.SubscriberID, amount=cost,
              description=f"Song subscription: {sub.TargetValue}",
              reference_id=sub.SubscriptionID, reference_type="song",
              source="api")
        db.commit()

        return JSONResponse(status_code=201, content={
            "subscription_id":  sub.SubscriptionID,
            "type":             sub.SubscriptionType,
            "status":           sub.Status,
            "tokens_debited":   float(cost),
            "schedule_windows": len(schedule_dicts),
        })
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Subscribe to keyword
# ---------------------------------------------------------------------------

@router.post("/keywords")
async def api_subscribe_keyword(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    keyword: str = Form(...),
    stations: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(..., description="YYYY-MM-DD, required"),
    schedule_windows: str = Form(
        default="",
        description=(
            'Optional JSON array of monitoring time windows. '
            'E.g. [{"day":-1,"from":"06:00","to":"18:00"}]. '
            'day: -1=all days, 0=Mon … 6=Sun. Omit for no time restriction.'
        ),
    ),
):
    from datetime import date as date_type
    from app.models.station import Station
    from app.services.subscription_service import create_keyword_subscription
    from app.services.token_service import get_balance, calculate_cost, calculate_cost_for_service, debit
    from app.services.schedule_service import save_schedules

    schedule_dicts = _parse_schedule_windows(schedule_windows)

    db = SessionLocal()
    try:
        station_ids = _resolve_station_ids(db, stations)

        try:
            sd = date_type.fromisoformat(start_date)
            ed = date_type.fromisoformat(end_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

        cost = calculate_cost_for_service(db, subscriber.SubscriberID, "word", len(station_ids), sd, ed)
        if get_balance(db, subscriber.SubscriberID) < cost:
            raise HTTPException(status_code=402, detail=f"Insufficient credits. Need {cost}.")

        sub, _ = create_keyword_subscription(
            db=db,
            subscriber_id=subscriber.SubscriberID,
            keyword=keyword,
            station_ids=station_ids,
            start_date=sd,
            end_date=ed,
            created_by_user_id=None,
        )
        sub.Source = 'api'

        # Save monitoring schedule windows (if provided)
        if schedule_dicts:
            save_schedules(db=db, subscription_id=sub.SubscriptionID, schedule_dicts=schedule_dicts)

        debit(db, subscriber_id=subscriber.SubscriberID, amount=cost,
              description=f"Keyword subscription: {sub.TargetValue}",
              reference_id=sub.SubscriptionID, reference_type="word",
              source="api")
        db.commit()

        return JSONResponse(status_code=201, content={
            "subscription_id":  sub.SubscriptionID,
            "keyword":          sub.TargetValue,
            "status":           sub.Status,
            "tokens_debited":   float(cost),
            "schedule_windows": len(schedule_dicts),
        })
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Request transcription
# ---------------------------------------------------------------------------

@router.post("/transcriptions")
async def api_request_transcription(
    request: Request,
    subscriber: Subscriber = Depends(get_api_key_subscriber),
    station: str = Form(...),           # station name e.g. "5FM"
    date_from: str = Form(...),         # YYYY-MM-DD
    date_to: str = Form(...),           # YYYY-MM-DD
    time_from: str = Form(default=""),  # HH:MM optional
    time_to: str = Form(default=""),    # HH:MM optional
    output_format: str = Form(default="chunks"),  # 'chunks' or 'concatenated'
):
    from datetime import date as date_type
    from app.models.station import Station
    from app.services.transcription_service import create_request
    from app.services.token_service import get_balance, calculate_cost_for_service, InsufficientTokensError

    db = SessionLocal()
    try:
        ids = _resolve_station_ids(db, station)  # single name, re-activates if needed
        station_id = ids[0]

        if output_format not in ("chunks", "concatenated"):
            raise HTTPException(status_code=422, detail="output_format must be 'chunks' or 'concatenated'")

        try:
            df = date_type.fromisoformat(date_from)
            dt = date_type.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

        if dt < df:
            raise HTTPException(status_code=422, detail="date_to must be on or after date_from.")

        cost = calculate_cost_for_service(db, subscriber.SubscriberID, "transcription", 1, df, dt)
        if get_balance(db, subscriber.SubscriberID) < cost:
            raise HTTPException(status_code=402, detail=f"Insufficient credits. Need {cost}.")

        try:
            req = create_request(
                db=db,
                subscriber_id=subscriber.SubscriberID,
                user_id=None,
                station_id=station_id,
                date_from=df,
                date_to=dt,
                time_from=time_from or None,
                time_to=time_to or None,
                output_format=output_format,
                source='api',
            )
            db.commit()
        except InsufficientTokensError as e:
            db.rollback()
            raise HTTPException(status_code=402, detail=str(e))

        return JSONResponse(status_code=201, content={
            "request_id":     req.RequestID,
            "station_id":     station_id,
            "date_from":      date_from,
            "date_to":        date_to,
            "time_from":      time_from or None,
            "time_to":        time_to or None,
            "output_format":  output_format,
            "status":         req.Status,
            "chunk_count":    req.ChunkCount,
            "tokens_debited": float(req.TokensDebited),
        })
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
