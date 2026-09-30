"""
app/services/subscription_service.py

Song and keyword subscription management.

Handles:
  - Creating/deactivating song subscriptions (live monitoring)
  - Creating/deactivating keyword subscriptions (live monitoring)
  - Submitting retrospective search jobs
  - Listing a Subscriber's subscriptions and jobs
  - Listing song/word detection results scoped to a Subscriber

Design per SONG_DETECTION_DESIGN_BRIEFING.md and
WORD_DETECTION_DESIGN_BRIEFING.md.

Billing is NOT wired yet (deferred to Phase B).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.database import utc_now
from app.models.detection import (
    ClientSubscription,
    SongDetection,
    SongDetectionJob,
    WordDetection,
    WordDetectionJob,
)
from app.models.station import Station
from app.models.user import User, UserType


class SubscriptionError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_can_manage(user: User, subscriber_id: int) -> None:
    """Raise SubscriptionError if the user can't manage this Subscriber."""
    if user.user_type == UserType.INTERNAL:
        return
    if user.SubscriberID != subscriber_id:
        raise SubscriptionError("You are not authorised for that Subscriber.")


def _station_filter_json(station_ids: list[int]) -> Optional[str]:
    """Convert a list of station IDs to a JSON string, or None if empty (all stations)."""
    if not station_ids:
        return None
    return json.dumps(sorted(station_ids))


def _validate_dates(start: date, end: Optional[date]) -> None:
    if end is not None and end < start:
        raise SubscriptionError("End date must be on or after the start date.")


# ---------------------------------------------------------------------------
# Song subscriptions
# ---------------------------------------------------------------------------

def create_song_subscription(
    db: Session,
    *,
    subscriber_id: int,
    track_id: Optional[str],
    artist: Optional[str],
    title: Optional[str],
    station_ids: list[int],
    start_date: date,
    end_date: Optional[date],
    created_by_user_id: Optional[int],
) -> tuple[ClientSubscription, Optional[SongDetectionJob]]:
    """
    Register a song subscription for live monitoring.
    If StartDate is in the past, also creates a retrospective SongDetectionJob
    covering StartDate → yesterday so the full date range is covered.
    Returns (subscription, retrospective_job_or_None).
    """
    if not track_id and not title:
        raise SubscriptionError("A TrackID or song title is required.")

    _validate_dates(start_date, end_date)

    sub_type = "song_track" if track_id else "song_title"
    target = track_id or title

    now = utc_now()
    subscription = ClientSubscription(
        SubscriberID=subscriber_id,
        SubscriptionType=sub_type,
        TargetValue=target,
        StationFilter=_station_filter_json(station_ids),
        Status="active",
        StartDate=start_date,
        EndDate=end_date,
        Artist=artist,
        Title=title or target,
        CreatedByUserID=created_by_user_id,
        CreatedAt=now,
        UpdatedAt=now,
    )
    db.add(subscription)
    db.flush()

    # Activate stations immediately if subscription starts today or in the past
    if start_date <= date.today():
        from app.services.station_scheduler import activate_station_if_needed
        for sid in station_ids:
            activate_station_if_needed(db, sid)

    # Auto-create retrospective job if start date is in the past.
    retro_job = None
    today = date.today()
    if start_date < today:
        retro_job = SongDetectionJob(
            SubscriberID=subscriber_id,
            SubscriptionID=subscription.SubscriptionID,
            TrackID=track_id or None,
            ArtistFilter=artist or None,
            TitleFilter=title or None,
            StationFilter=_station_filter_json(station_ids),
            DateFrom=start_date,
            DateTo=today - timedelta(days=1),
            Status="pending",
            CreatedAt=now,
        )
        db.add(retro_job)
        db.flush()

    return subscription, retro_job


def create_keyword_subscription(
    db: Session,
    *,
    subscriber_id: int,
    keyword: str,
    station_ids: list[int],
    start_date: date,
    end_date: Optional[date],
    created_by_user_id: Optional[int],
) -> tuple[ClientSubscription, Optional[WordDetectionJob]]:
    """
    Register a single keyword subscription for live monitoring.
    Creates ONE ClientSubscription and ONE WordDetectionJob per call.
    Caller is responsible for splitting comma-separated keywords before calling.
    If StartDate is today or in the past, creates a WordDetectionJob immediately.
    Returns (subscription, job_or_None).
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise SubscriptionError("A keyword or phrase is required.")
    if len(keyword) > 500:
        raise SubscriptionError("Keyword must be 500 characters or less.")

    _validate_dates(start_date, end_date)

    now = utc_now()
    subscription = ClientSubscription(
        SubscriberID=subscriber_id,
        SubscriptionType="keyword",
        TargetValue=keyword,
        StationFilter=_station_filter_json(station_ids),
        Status="active",
        StartDate=start_date,
        EndDate=end_date,
        CreatedByUserID=created_by_user_id,
        CreatedAt=now,
        UpdatedAt=now,
    )
    db.add(subscription)
    db.flush()

    # Activate stations immediately if subscription starts today or in the past
    if start_date <= date.today():
        from app.services.station_scheduler import activate_station_if_needed
        for sid in station_ids:
            activate_station_if_needed(db, sid)

    # Create WordDetectionJob for today or past start dates
    # (pipeline needs a job row to know what to scan — not just the subscription)
    retro_job = None
    today = date.today()
    if start_date <= today:
        job_date_to = (today - timedelta(days=1)) if start_date < today else today
        retro_job = WordDetectionJob(
            SubscriberID=subscriber_id,
            SubscriptionID=subscription.SubscriptionID,
            Keyword=keyword,
            StationFilter=_station_filter_json(station_ids),
            DateFrom=start_date,
            DateTo=job_date_to,
            Status="pending",
            CreatedAt=now,
        )
        db.add(retro_job)
        db.flush()

    return subscription, retro_job


def deactivate_subscription(
    db: Session,
    user: User,
    subscription_id: int,
) -> ClientSubscription:
    sub = db.get(ClientSubscription, subscription_id)
    if sub is None:
        raise SubscriptionError("Subscription not found.")
    _assert_can_manage(user, sub.SubscriberID)
    if sub.Status == "withdrawn":
        raise SubscriptionError("Subscription is already withdrawn.")
    sub.Status = "withdrawn"
    sub.UpdatedAt = utc_now()
    db.flush()

    # Deactivate stations that no longer have any active work
    try:
        import json as _json
        station_ids = _json.loads(sub.StationFilter or "[]")
        if isinstance(station_ids, list) and station_ids:
            from app.services.station_scheduler import deactivate_station_if_idle
            for sid in station_ids:
                deactivate_station_if_idle(db, int(sid))
    except Exception:
        pass  # don't let station sync failure block subscription cancellation

    return sub


def list_subscriptions(
    db: Session,
    user: User,
    sub_type: Optional[str] = None,
    include_inactive: bool = False,
) -> list[ClientSubscription]:
    """List subscriptions visible to the user."""
    q = db.query(ClientSubscription)
    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return []
        q = q.filter(ClientSubscription.SubscriberID == user.SubscriberID)
    if sub_type:
        q = q.filter(ClientSubscription.SubscriptionType == sub_type)
    if not include_inactive:
        q = q.filter(ClientSubscription.Status == "active")
    return q.order_by(ClientSubscription.CreatedAt.desc()).all()


# ---------------------------------------------------------------------------
# Retrospective search jobs
# ---------------------------------------------------------------------------

def submit_song_job(
    db: Session,
    user: User,
    *,
    subscriber_id: int,
    subscription_id: Optional[int],
    track_id: Optional[str],
    artist_filter: Optional[str],
    title_filter: Optional[str],
    station_ids: list[int],
    date_from: date,
    date_to: date,
) -> SongDetectionJob:
    _assert_can_manage(user, subscriber_id)
    if not track_id and not title_filter and not artist_filter:
        raise SubscriptionError("Provide a TrackID, title, or artist to search for.")
    if date_from > date_to:
        raise SubscriptionError("Date from must be before date to.")

    job = SongDetectionJob(
        SubscriberID=subscriber_id,
        SubscriptionID=subscription_id,
        TrackID=track_id,
        ArtistFilter=artist_filter,
        TitleFilter=title_filter,
        StationFilter=_station_filter_json(station_ids),
        DateFrom=date_from,
        DateTo=date_to,
        Status="pending",
        CreatedAt=utc_now(),
    )
    db.add(job)
    db.flush()
    return job


def submit_word_job(
    db: Session,
    user: User,
    *,
    subscriber_id: int,
    subscription_id: Optional[int],
    keyword: str,
    station_ids: list[int],
    date_from: date,
    date_to: date,
) -> WordDetectionJob:
    _assert_can_manage(user, subscriber_id)
    keyword = (keyword or "").strip()
    if not keyword:
        raise SubscriptionError("A keyword is required.")
    if date_from > date_to:
        raise SubscriptionError("Date from must be before date to.")

    job = WordDetectionJob(
        SubscriberID=subscriber_id,
        SubscriptionID=subscription_id,
        Keyword=keyword,
        StationFilter=_station_filter_json(station_ids),
        DateFrom=date_from,
        DateTo=date_to,
        Status="pending",
        CreatedAt=utc_now(),
    )
    db.add(job)
    db.flush()
    return job


def list_jobs(
    db: Session,
    user: User,
    job_type: str,  # 'song' or 'word'
) -> list:
    if job_type == "song":
        q = db.query(SongDetectionJob)
        if user.user_type != UserType.INTERNAL:
            q = q.filter(SongDetectionJob.SubscriberID == user.SubscriberID)
        return q.order_by(SongDetectionJob.CreatedAt.desc()).all()
    else:
        q = db.query(WordDetectionJob)
        if user.user_type != UserType.INTERNAL:
            q = q.filter(WordDetectionJob.SubscriberID == user.SubscriberID)
        return q.order_by(WordDetectionJob.CreatedAt.desc()).all()


# ---------------------------------------------------------------------------
# Detection results
# ---------------------------------------------------------------------------

def _active_subscription_ids(db: Session, subscriber_id: int) -> list[int]:
    rows = (
        db.query(ClientSubscription.SubscriptionID)
        .filter(
            ClientSubscription.SubscriberID == subscriber_id,
            ClientSubscription.Status.in_(["active", "expired", "completed"]),
        )
        .all()
    )
    return [r[0] for r in rows]


def list_song_detections(
    db: Session,
    user: User,
    *,
    station_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SongDetection], int]:
    """
    Returns SongDetection rows relevant to the subscriber's active song
    subscriptions, matched at query time by Title/TargetValue + StationID
    + date range -- NOT by SubscriptionID, since the pipeline detects all
    songs regardless of subscriptions and writes SubscriptionID = NULL.
    Internal users see all rows.
    """
    import json as _json
    from app.models.detection import RecordingChunk

    q = db.query(SongDetection).join(
        RecordingChunk, RecordingChunk.ChunkID == SongDetection.ChunkID
    )

    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return [], 0

        # Get all active song subscriptions for this subscriber
        subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == user.SubscriberID,
            ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
        ).all()

        if not subs:
            return [], 0

        # Build a filter: detection matches ANY subscription by title + station + date
        from sqlalchemy import or_, and_
        sub_filters = []
        for s in subs:
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass

            conds = [
                SongDetection.Title.ilike(f"%{s.TargetValue}%"),
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
        q = q.filter(RecordingChunk.ChunkDate >= str(date_from))
    if date_to:
        q = q.filter(RecordingChunk.ChunkDate <= str(date_to))
    if artist:
        q = q.filter(SongDetection.Artist.ilike(f"%{artist}%"))
    if title:
        q = q.filter(SongDetection.Title.ilike(f"%{title}%"))

    total = q.count()
    rows = (
        q.order_by(RecordingChunk.ChunkDate.desc(), SongDetection.StartTime.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def list_word_detections(
    db: Session,
    user: User,
    *,
    station_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    keyword: Optional[str] = None,
    source: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[WordDetection], int]:
    """
    Returns WordDetection rows relevant to the subscriber's active keyword
    subscriptions, matched at query time by Keyword + StationID + date range.
    Internal users see all rows.
    """
    import json as _json
    from app.models.detection import RecordingChunk

    q = db.query(WordDetection).join(
        RecordingChunk, RecordingChunk.ChunkID == WordDetection.ChunkID
    )

    if user.user_type != UserType.INTERNAL:
        if user.SubscriberID is None:
            return [], 0

        subs = db.query(ClientSubscription).filter(
            ClientSubscription.SubscriberID == user.SubscriberID,
            ClientSubscription.SubscriptionType == "keyword",
        ).all()

        if not subs:
            return [], 0

        from sqlalchemy import or_, and_
        sub_filters = []
        for s in subs:
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass

            conds = [
                WordDetection.Keyword.ilike(f"%{s.TargetValue}%"),
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
    if source:
        q = q.filter(WordDetection.DetectionSource == source)
    if date_from:
        q = q.filter(RecordingChunk.ChunkDate >= str(date_from))
    if date_to:
        q = q.filter(RecordingChunk.ChunkDate <= str(date_to))

    total = q.count()
    rows = (
        q.order_by(RecordingChunk.ChunkDate.desc(), WordDetection.StartTime.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


def get_song_detection(
    db: Session, user: User, song_detection_id: int
) -> Optional[SongDetection]:
    """Fetch a single SongDetection. Fail-closed — None if not visible to this user."""
    import json as _json
    row = db.get(SongDetection, song_detection_id)
    if row is None:
        return None
    if user.user_type == UserType.INTERNAL:
        return row
    if user.SubscriberID is None:
        return None

    from app.models.detection import RecordingChunk
    chunk = db.get(RecordingChunk, row.ChunkID) if row.ChunkID else None
    chunk_date = str(chunk.ChunkDate) if chunk else None

    subs = db.query(ClientSubscription).filter(
        ClientSubscription.SubscriberID == user.SubscriberID,
        ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
    ).all()

    for s in subs:
        target = (s.TargetValue or "").lower()
        if row.Title and target and target in row.Title.lower():
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            station_ok = not station_ids or row.StationID in station_ids
            date_ok = (not chunk_date or (
                str(s.StartDate) <= chunk_date and
                (not s.EndDate or chunk_date <= str(s.EndDate))
            ))
            if station_ok and date_ok:
                return row
    return None


def get_word_detection(
    db: Session, user: User, word_detection_id: int
) -> Optional[WordDetection]:
    """Fetch a single WordDetection. Fail-closed."""
    import json as _json
    row = db.get(WordDetection, word_detection_id)
    if row is None:
        return None
    if user.user_type == UserType.INTERNAL:
        return row
    if user.SubscriberID is None:
        return None

    from app.models.detection import RecordingChunk
    chunk = db.get(RecordingChunk, row.ChunkID) if row.ChunkID else None
    chunk_date = str(chunk.ChunkDate) if chunk else None

    subs = db.query(ClientSubscription).filter(
        ClientSubscription.SubscriberID == user.SubscriberID,
        ClientSubscription.SubscriptionType == "keyword",
    ).all()

    for s in subs:
        target = (s.TargetValue or "").lower()
        if row.Keyword and target and target in row.Keyword.lower():
            station_ids = []
            if s.StationFilter:
                try:
                    station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            station_ok = not station_ids or row.StationID in station_ids
            date_ok = (not chunk_date or (
                str(s.StartDate) <= chunk_date and
                (not s.EndDate or chunk_date <= str(s.EndDate))
            ))
            if station_ok and date_ok:
                return row
    return None
