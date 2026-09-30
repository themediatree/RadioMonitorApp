"""
app/services/transcription_service.py

Handles TranscriptionRequest lifecycle:
  create_request()  -- validate, debit tokens, create row, attempt processing
  process_request() -- find matching chunks, concatenate if needed, mark ready
  _merge_chunks()   -- stitch JSON files with gap markers for missing minutes
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.transcription_request import TranscriptionRequest


def _resolve_json_path(transcript) -> Optional[str]:
    """
    Find the transcript JSON file on disk, trying EarlyJsonPath then JsonPath.
    Also tries replacing C:\\ with D:\\ since AUDIOREC uses D:\\ but some
    paths were written with C:\\ by the pipeline on a different machine.
    """
    def try_path(p: Optional[str]) -> Optional[str]:
        if not p:
            return None
        if os.path.isfile(p):
            return p
        # Try alternate drive letter
        alt = None
        if p.lower().startswith("c:\\"):
            alt = "D:\\" + p[3:]
        elif p.lower().startswith("d:\\"):
            alt = "C:\\" + p[3:]
        if alt and os.path.isfile(alt):
            return alt
        return None

    return (
        try_path(transcript.EarlyJsonPath)
        or try_path(transcript.JsonPath)
    )


def _calculate_actual_hours(
    date_from: date,
    date_to: date,
    time_from: Optional[str],
    time_to: Optional[str],
    schedule_dicts: Optional[list],
) -> Decimal:
    """
    Returns the actual hours of audio content being requested, mirroring the
    frontend cost-preview calculation in request.html::getScheduledHours().

    - schedule_dicts provided → sum window durations × matching calendar days
    - time_from/time_to only  → days × window hours
    - neither                 → days × 24 (full coverage, unchanged behaviour)
    """
    days = (date_to - date_from).days + 1
    single_day = date_from == date_to

    if schedule_dicts:
        total = Decimal("0")
        for offset in range(days):
            current = date_from + timedelta(days=offset)
            dow = current.weekday()  # 0 = Monday
            for w in schedule_dicts:
                w_dow = w["day"]  # -1 = all days
                if w_dow == -1 or single_day or w_dow == dow:
                    t_from = w["from"]  # datetime.time object
                    t_to   = w["to"]
                    mins = (t_to.hour * 60 + t_to.minute) - (t_from.hour * 60 + t_from.minute)
                    if mins > 0:
                        total += Decimal(mins) / Decimal("60")
        return total if total > 0 else Decimal(days * 24)

    if time_from and time_to:
        try:
            tf_h, tf_m = [int(x) for x in str(time_from)[:5].split(":")]
            tt_h, tt_m = [int(x) for x in str(time_to)[:5].split(":")]
            mins_per_day = (tt_h * 60 + tt_m) - (tf_h * 60 + tf_m)
            if mins_per_day > 0:
                return Decimal(days * mins_per_day) / Decimal("60")
        except Exception:
            pass

    return Decimal(days * 24)


def create_request(
    db: Session,
    subscriber_id: int,
    user_id: int,
    station_id: int,
    date_from: date,
    date_to: date,
    time_from: Optional[str],
    time_to: Optional[str],
    output_format: str,
    source: str = 'app',
    schedule_dicts: Optional[list] = None,
    cost: Optional[Decimal] = None,
) -> TranscriptionRequest:
    """Creates a TranscriptionRequest, debits tokens, attempts processing."""
    from app.services.token_service import get_effective_rate, debit
    from decimal import ROUND_HALF_UP
    import json as _json

    if cost is None:
        actual_hours = _calculate_actual_hours(date_from, date_to, time_from, time_to, schedule_dicts)
        rate = get_effective_rate(db, subscriber_id, "transcription")
        cost = (actual_hours * rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    schedule_json = _json.dumps([
        {"day": s["day"], "from": s["from"].strftime("%H:%M"), "to": s["to"].strftime("%H:%M")}
        for s in (schedule_dicts or [])
    ]) if schedule_dicts else None

    req = TranscriptionRequest(
        SubscriberID=subscriber_id,
        UserID=user_id,
        StationID=station_id,
        DateFrom=date_from,
        DateTo=date_to,
        TimeFrom=time_from or None,
        TimeTo=time_to or None,
        OutputFormat=output_format,
        Status="pending",
        TokensDebited=cost,
        Source=source,
    )
    try:
        req.ScheduleJson = schedule_json
    except Exception:
        pass

    db.add(req)
    db.flush()

    actual_hours = _calculate_actual_hours(date_from, date_to, time_from, time_to, schedule_dicts)
    debit(
        db,
        subscriber_id=subscriber_id,
        amount=cost,
        description=f"Transcription request #{req.RequestID} — {float(actual_hours * 60):.0f} min",
        reference_id=req.RequestID,
        reference_type="transcription",
        created_by_user_id=user_id,
        source=source,
    )
    db.flush()

    process_request(db, req)
    return req


def _schedule_filter_pairs(pairs: list, schedule_json, date_from=None, date_to=None) -> list:
    """Filter chunks by schedule. Single-day: time only. Multi-day: day-of-week + time."""
    import json as _json
    if not schedule_json:
        return pairs
    try:
        windows = _json.loads(schedule_json)
    except Exception:
        return pairs
    if not windows:
        return pairs
    single_day = date_from and date_to and date_from == date_to
    filtered = []
    for transcript, chunk in pairs:
        if not chunk.ChunkDate or not chunk.StartTime:
            continue
        try:
            from datetime import datetime as _dt, date as _date
            # StartTime may be a string "06:00:39" or a datetime.time object
            if hasattr(chunk.StartTime, 'hour'):
                chunk_t = chunk.StartTime.replace(second=0, microsecond=0)
            else:
                time_str = str(chunk.StartTime)[:5]
                chunk_t = _dt.strptime(time_str, "%H:%M").time()
            # Get day-of-week from ChunkDate
            if hasattr(chunk.ChunkDate, 'weekday'):
                dow = chunk.ChunkDate.weekday()
            else:
                dow = _dt.strptime(str(chunk.ChunkDate), "%Y-%m-%d").weekday()
        except Exception:
            continue
        for w in windows:
            t_from = time(*[int(x) for x in w["from"].split(":")])
            t_to   = time(*[int(x) for x in w["to"].split(":")])
            if (single_day or w["day"] == -1 or w["day"] == dow) and t_from <= chunk_t < t_to:
                filtered.append((transcript, chunk))
                break
    return filtered



def process_request(db: Session, req: TranscriptionRequest) -> None:
    """Finds matching chunks, links or merges them, updates status."""
    from app.models.detection import Transcript, RecordingChunk

    schedule_json = getattr(req, 'ScheduleJson', None)

    q = (
        db.query(Transcript, RecordingChunk)
        .join(RecordingChunk, RecordingChunk.ChunkID == Transcript.ChunkID)
        .filter(
            RecordingChunk.StationID == req.StationID,
            RecordingChunk.ChunkDate >= str(req.DateFrom),
            RecordingChunk.ChunkDate <= str(req.DateTo),
        )
    )
    if req.TimeFrom and not schedule_json:
        q = q.filter(RecordingChunk.StartTime >= req.TimeFrom)
    if req.TimeTo and not schedule_json:
        q = q.filter(RecordingChunk.StartTime <= req.TimeTo)

    pairs = q.order_by(RecordingChunk.ChunkDate, RecordingChunk.StartTime).all()

    if schedule_json:
        pairs = _schedule_filter_pairs(pairs, schedule_json, req.DateFrom, req.DateTo)

    if not pairs:
        return  # stays pending

    # Verify at least one transcript file actually exists on disk
    found_any = any(_resolve_json_path(t) for t, _ in pairs)
    if not found_any:
        return  # files not yet on disk — stay pending

    req.ChunkCount = len(pairs)
    req.ProcessedAt = datetime.now()

    if req.OutputFormat == "chunks":
        req.Status = "ready"
        return

    # Concatenated -- merge JSON files with gap markers
    merged_path = _merge_chunks(req, pairs)
    if merged_path:
        req.MergedJsonPath = merged_path
        req.Status = "ready"
    else:
        req.Status = "partial"


def _merge_chunks(
    req: TranscriptionRequest,
    pairs: list,
) -> Optional[str]:
    """
    Merges word-level timestamp JSON files from multiple chunks into one,
    inserting gap markers where consecutive chunks are not adjacent.
    Returns the path of the merged file, or None on failure.
    """
    from app.config import settings

    merged_words = []
    time_offset = 0.0
    prev_chunk_end: Optional[str] = None

    for transcript, chunk in pairs:
        json_path = _resolve_json_path(transcript)
        if not json_path:
            # Missing file -- insert gap marker
            if prev_chunk_end:
                merged_words.append({
                    "word": GAP_MARKER.format(
                        start=prev_chunk_end,
                        end=chunk.StartTime or "??:??",
                    ),
                    "start": time_offset,
                    "end": time_offset + 0.01,
                    "is_gap": True,
                })
            prev_chunk_end = chunk.StartTime
            continue

        try:
            data = json.loads(open(json_path).read())
            words = (
                data.get("results", {})
                .get("channels", [{}])[0]
                .get("alternatives", [{}])[0]
                .get("words", [])
            )
        except Exception:
            words = []

        if not words and prev_chunk_end:
            merged_words.append({
                "word": GAP_MARKER.format(
                    start=prev_chunk_end,
                    end=chunk.StartTime or "??:??",
                ),
                "start": time_offset,
                "end": time_offset + 0.01,
                "is_gap": True,
            })
            prev_chunk_end = chunk.StartTime
            continue

        # Rebase word timestamps relative to merged timeline
        chunk_start = words[0]["start"] if words else 0
        for w in words:
            merged_words.append({
                "word": w.get("word", ""),
                "start": round(time_offset + w["start"] - chunk_start, 4),
                "end": round(time_offset + w["end"] - chunk_start, 4),
            })

        if words:
            duration = words[-1]["end"] - words[0]["start"]
            time_offset += duration + 0.5  # small gap between chunks

        prev_chunk_end = chunk.StartTime

    if not merged_words:
        return None

    merged = {
        "request_id": req.RequestID,
        "station_id": req.StationID,
        "date_from": str(req.DateFrom),
        "date_to": str(req.DateTo),
        "output_format": "concatenated",
        "results": {
            "channels": [{
                "alternatives": [{
                    "words": merged_words,
                    "transcript": " ".join(
                        w["word"] for w in merged_words if not w.get("is_gap")
                    ),
                }]
            }]
        },
    }

    out_dir = os.path.join(
        getattr(settings, "transcription_requests_root",
                r"D:\RadioMonitor\transcription_requests")
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"request_{req.RequestID}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f)

    return out_path


def list_requests(
    db: Session,
    subscriber_id: int,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[TranscriptionRequest], int]:
    q = (
        db.query(TranscriptionRequest)
        .filter(TranscriptionRequest.SubscriberID == subscriber_id)
        .order_by(TranscriptionRequest.CreatedAt.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return rows, total


def get_request_chunks(
    db: Session,
    req: TranscriptionRequest,
) -> list:
    """Returns the Transcript+RecordingChunk pairs for a chunks-format request."""
    from app.models.detection import Transcript, RecordingChunk

    q = (
        db.query(Transcript, RecordingChunk)
        .join(RecordingChunk, RecordingChunk.ChunkID == Transcript.ChunkID)
        .filter(
            RecordingChunk.StationID == req.StationID,
            RecordingChunk.ChunkDate >= str(req.DateFrom),
            RecordingChunk.ChunkDate <= str(req.DateTo),
        )
    )
    schedule_json = getattr(req, 'ScheduleJson', None)
    if req.TimeFrom and not schedule_json:
        q = q.filter(RecordingChunk.StartTime >= req.TimeFrom)
    if req.TimeTo and not schedule_json:
        q = q.filter(RecordingChunk.StartTime <= req.TimeTo)
    pairs = q.order_by(RecordingChunk.ChunkDate, RecordingChunk.StartTime).all()
    if schedule_json:
        pairs = _schedule_filter_pairs(pairs, schedule_json, req.DateFrom, req.DateTo)
    return pairs


def retry_pending_requests(db: Session) -> dict:
    """
    Re-runs process_request() on all pending TranscriptionRequests.
    Called by the scheduler and the admin retry endpoint.
    Returns summary: {"processed": N, "still_pending": N}
    """
    import logging
    logger = logging.getLogger("radiomonitor")

    pending = (
        db.query(TranscriptionRequest)
        .filter(TranscriptionRequest.Status == "pending")
        .all()
    )

    processed = 0
    for req in pending:
        try:
            process_request(db, req)
            if req.Status != "pending":
                processed += 1
                logger.info(
                    f"[TRANSCRIPTION] Request #{req.RequestID} → {req.Status} "
                    f"({req.ChunkCount} chunks)"
                )
        except Exception as e:
            logger.error(f"[TRANSCRIPTION] Retry failed for #{req.RequestID}: {e}")

    db.commit()
    still_pending = len(pending) - processed
    logger.info(
        f"[TRANSCRIPTION] Retry complete — processed: {processed}, "
        f"still pending: {still_pending}"
    )
    return {"processed": processed, "still_pending": still_pending}
