"""
Detection viewing service (v0.3 + aired-time fix).

The 'aired' time is derived from the RecordingChunk filename:
    e.g. 5FM_26-06-05_080105.mp3 -> aired 2026-06-05, chunk started 08:01:05
    StartTimeSec / EndTimeSec are seconds into that chunk -> actual clock times.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.detection import Detection, RecordingChunk
from app.models.user import User
from app.services.scoping import commercial_filter


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Chunk filename pattern: <STATION>_<YY-MM-DD>_<HHMMSS>.<ext>
_CHUNK_RE = re.compile(r'^.+?_(\d{2}-\d{2}-\d{2})_(\d{6})(?:\.\w+)?$')


def parse_aired(chunk: Optional[object],
                start_sec: Optional[float],
                end_sec: Optional[float]) -> dict:
    """
    Return {aired_date, start_clock, end_clock} from a RecordingChunk row.

    ChunkDate is 'YYYY-MM-DD' (DATE column as string).
    StartTime is 'HH:MM:SS' (TIME column as string).
    start_sec / end_sec are seconds into the chunk from Detection table.
    """
    result = {"aired_date": None, "start_clock": None, "end_clock": None}
    if chunk is None:
        return result

    # Prefer the structured DB columns; fall back to filename parsing.
    chunk_date = getattr(chunk, "ChunkDate", None)
    chunk_start_time = getattr(chunk, "StartTime", None)

    if chunk_date:
        # ChunkDate may be a date object or a string
        result["aired_date"] = (
            chunk_date.strftime("%Y-%m-%d")
            if hasattr(chunk_date, "strftime")
            else str(chunk_date)[:10]
        )

    if chunk_date and start_sec is not None:
        try:
            date_str = result["aired_date"]
            start_td = timedelta(seconds=float(start_sec))
            result["start_clock"] = (datetime.strptime(date_str, "%Y-%m-%d") + start_td).strftime("%H:%M:%S")
            if end_sec is not None:
                end_td = timedelta(seconds=float(end_sec))
                result["end_clock"] = (datetime.strptime(date_str, "%Y-%m-%d") + end_td).strftime("%H:%M:%S")
        except (ValueError, TypeError):
            pass

    # Fallback: parse from FileName if structured columns missing
    if result["aired_date"] is None:
        fname = getattr(chunk, "FileName", None)
        if fname:
            m = _CHUNK_RE.match(fname.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
            if m:
                date_part, time_part = m.groups()
                try:
                    chunk_start = datetime.strptime(
                        date_part + time_part, "%y-%m-%d%H%M%S"
                    )
                    result["aired_date"] = chunk_start.strftime("%Y-%m-%d")
                    if start_sec is not None:
                        result["start_clock"] = (
                            datetime.strptime(result["aired_date"], "%Y-%m-%d")
                            + timedelta(seconds=float(start_sec))
                        ).strftime("%H:%M:%S")
                    if end_sec is not None:
                        result["end_clock"] = (
                            datetime.strptime(result["aired_date"], "%Y-%m-%d")
                            + timedelta(seconds=float(end_sec))
                        ).strftime("%H:%M:%S")
                except ValueError:
                    pass
    return result


def _enrich(rows: list[Detection], db: Session) -> list[dict]:
    if not rows:
        return []
    chunk_ids = {r.ChunkID for r in rows if r.ChunkID}
    chunk_map: dict[int, object] = {}
    if chunk_ids:
        chunks = (
            db.query(RecordingChunk)
            .filter(RecordingChunk.ChunkID.in_(chunk_ids))
            .all()
        )
        chunk_map = {c.ChunkID: c for c in chunks}

    enriched = []
    for det in rows:
        chunk = chunk_map.get(det.ChunkID)
        aired = parse_aired(chunk, det.StartTimeSec, det.EndTimeSec)
        enriched.append({"detection": det, "aired": aired})
    return enriched


def list_commercial_detections(
    db: Session,
    user: User,
    *,
    station_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    commercial_name: Optional[str] = None,
    brand: Optional[str] = None,
    campaign_name: Optional[str] = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[dict], int]:
    """
    Return (enriched_rows, total_count). Each enriched row is a dict:
        detection -- the Detection ORM object
        aired     -- {aired_date, start_clock, end_clock}
    """
    from app.models.campaign import Campaign, Commercial
    from app.models.station import Station

    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), MAX_PAGE_SIZE))

    q = (
        db.query(Detection)
        .options(
            joinedload(Detection.commercial),
            joinedload(Detection.campaign),
        )
        .filter(commercial_filter(user, db))
    )
    if station_id is not None:
        q = q.filter(Detection.StationID == station_id)
    if date_from is not None:
        q = q.filter(Detection.CreatedAt >= datetime.combine(date_from, time.min))
    if date_to is not None:
        q = q.filter(Detection.CreatedAt < datetime.combine(
            date_to + timedelta(days=1), time.min))
    if commercial_name:
        q = q.join(Commercial, Commercial.CommercialID == Detection.CommercialID)
        q = q.filter(Commercial.DisplayTapeID.ilike(f"%{commercial_name}%"))
    if brand:
        if not commercial_name:
            q = q.join(Commercial, Commercial.CommercialID == Detection.CommercialID)
        q = q.filter(Commercial.Brand.ilike(f"%{brand}%"))
    if campaign_name:
        q = q.join(Campaign, Campaign.CampaignID == Detection.CampaignID)
        q = q.filter(Campaign.Name.ilike(f"%{campaign_name}%"))

    total = q.count()
    rows = (
        q.order_by(Detection.CreatedAt.desc(), Detection.DetectionID.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _enrich(rows, db), total


def get_visible_detection(db: Session, user: User, detection_id: int) -> Optional[dict]:
    """
    Fetch a single detection with aired time enrichment. Returns None when
    not found or out of scope (same response -- no existence leak).
    """
    det = (
        db.query(Detection)
        .options(
            joinedload(Detection.commercial),
            joinedload(Detection.campaign),
        )
        .filter(Detection.DetectionID == detection_id)
        .filter(commercial_filter(user, db))
        .one_or_none()
    )
    if det is None:
        return None
    return _enrich([det], db)[0]


def get_generic_transcript_path(db: Session, fingerprint_id: Optional[str]) -> Optional[str]:
    """
    Resolve the JSON transcript path for a generic commercial, keyed by
    FingerprintID. Returns the stored JsonPath if the job is complete and
    the path resolves to a real file under clips_path, else None.
    """
    if not fingerprint_id:
        return None
    from app.models.detection import GenericTranscriptionJob
    job = (
        db.query(GenericTranscriptionJob)
        .filter(
            GenericTranscriptionJob.FingerprintID == fingerprint_id,
            GenericTranscriptionJob.Status == "complete",
        )
        .one_or_none()
    )
    if job is None or not job.JsonPath:
        return None
    return job.JsonPath
