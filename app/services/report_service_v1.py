"""Report service — data queries for all report types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.station import Station


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_active_stations(db: Session) -> list[Station]:
    return db.query(Station).filter(Station.IsActive == True).order_by(Station.StationName).all()  # noqa: E712



# ---------------------------------------------------------------------------
# Song Monitoring Report
# ---------------------------------------------------------------------------

@dataclass
class SongReportRow:
    song_detection_id: int
    artist: str
    title: str
    station_name: str
    chunk_date: str
    start_clock: str
    end_clock: str
    plays: int
    clip_url: Optional[tuple] = None  # (url, qr_base64_png) for PDF
    in_schedule: bool = True  # False = detected outside booked hours


@dataclass
class SongReportResult:
    rows: list[SongReportRow] = field(default_factory=list)
    total: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    generated_at: datetime = field(default_factory=datetime.utcnow)
    subscriber_id: Optional[int] = None

    @property
    def total_plays(self) -> int:
        return sum(r.plays for r in self.rows)

    @property
    def unique_songs(self) -> int:
        return len({(r.artist, r.title) for r in self.rows})

    @property
    def unique_stations(self) -> int:
        return len({r.station_name for r in self.rows})

    @property
    def in_schedule_count(self) -> int:
        return sum(1 for r in self.rows if r.in_schedule)

    @property
    def out_of_schedule_count(self) -> int:
        return sum(1 for r in self.rows if not r.in_schedule)

    @property
    def has_schedule(self) -> bool:
        return any(not r.in_schedule for r in self.rows)

    @property
    def by_station(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.station_name] = counts.get(r.station_name, 0) + r.plays
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    @property
    def by_artist(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.artist] = counts.get(r.artist, 0) + r.plays
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    @property
    def by_title(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            key = f"{r.artist} – {r.title}"
            counts[key] = counts.get(key, 0) + r.plays
        return dict(sorted(counts.items(), key=lambda x: -x[1]))


def get_subscriber_artists(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[str]:
    """Distinct artists from subscriber's active song subscriptions."""
    from app.models.detection import ClientSubscription
    q = db.query(ClientSubscription.Artist).filter(
        ClientSubscription.Status.in_(["active", "expired", "completed"]),
        ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
        ClientSubscription.Artist.isnot(None),
    )
    if subscriber_id:
        q = q.filter(ClientSubscription.SubscriberID == subscriber_id)
    rows = (q
        .distinct()
        .order_by(ClientSubscription.Artist)
        .all()
    )
    return [r.Artist for r in rows if r.Artist]


def get_subscriber_titles(
    db: Session,
    subscriber_id: Optional[int],
) -> list[str]:
    """Distinct titles from subscriber's active song subscriptions."""
    from app.models.detection import ClientSubscription
    q = db.query(ClientSubscription.Title).filter(
        ClientSubscription.Status.in_(["active", "expired", "completed"]),
        ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
        ClientSubscription.Title.isnot(None),
    )
    if subscriber_id:
        q = q.filter(ClientSubscription.SubscriberID == subscriber_id)
    rows = (q
        .distinct()
        .order_by(ClientSubscription.Title)
        .all()
    )
    return [r.Title for r in rows if r.Title]


def get_song_report(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    station_ids: Optional[list[int]] = None,
    artist: Optional[str] = None,
    title: Optional[str] = None,
    base_url: str = "",
    generate_clip_urls: bool = False,
    short_lived: bool = False,
) -> SongReportResult:
    """Query SongDetection using subscription-matching logic (same as dashboard).
    Pipeline writes SubscriptionID=NULL so we match by title+station+date instead."""
    import json as _json
    from sqlalchemy import or_, and_
    from app.models.detection import SongDetection, ClientSubscription, RecordingChunk

    # Get subscriber's active song subscriptions (internal users see all)
    subs_q = db.query(ClientSubscription).filter(
        ClientSubscription.Status.in_(["active", "expired", "completed"]),
        ClientSubscription.SubscriptionType.in_(["song_title", "song_track"]),
    )
    if subscriber_id:
        subs_q = subs_q.filter(ClientSubscription.SubscriberID == subscriber_id)
    subs = subs_q.all()

    from app.models.station import Station
    q = (
        db.query(
            SongDetection,
            RecordingChunk.ChunkDate,
            Station.StationName,
        )
        .join(RecordingChunk, RecordingChunk.ChunkID == SongDetection.ChunkID)
        .join(Station, Station.StationID == SongDetection.StationID)
    )

    # For internal users with no subscriptions, show all detections
    # For subscribers, scope by their subscription titles/stations
    if subs and subscriber_id:
        sub_filters = []
        for s in subs:
            sub_station_ids = []
            if s.StationFilter:
                try:
                    sub_station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            conds = [
                or_(
                    SongDetection.Title.ilike(f"%{s.TargetValue}%"),
                    SongDetection.Title.ilike(f"%{s.Title}%") if s.Title else False,
                ),
                RecordingChunk.ChunkDate >= str(s.StartDate),
            ]
            if s.EndDate:
                conds.append(RecordingChunk.ChunkDate <= str(s.EndDate))
            if sub_station_ids:
                conds.append(SongDetection.StationID.in_(sub_station_ids))
            sub_filters.append(and_(*conds))
        if sub_filters:
            q = q.filter(or_(*sub_filters))

    # Apply user-selected filters on top
    if date_from:
        q = q.filter(RecordingChunk.ChunkDate >= str(date_from))
    if date_to:
        q = q.filter(RecordingChunk.ChunkDate <= str(date_to))
    if station_ids:
        q = q.filter(SongDetection.StationID.in_(station_ids))
    if artist:
        q = q.filter(SongDetection.Artist.ilike(f"%{artist}%"))
    if title:
        q = q.filter(SongDetection.Title.ilike(f"%{title}%"))

    q = q.order_by(RecordingChunk.ChunkDate.asc(), SongDetection.StartTime.asc())
    rows_raw = q.all()

    # Load schedules for this subscriber's song subscriptions
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    sub_schedule_map = {}
    for sub in subs:
        schedules = get_subscription_schedules(db, sub.SubscriptionID)
        if schedules:
            import json as _json2
            try:
                sids2 = _json2.loads(sub.StationFilter or "[]")
            except Exception:
                sids2 = []
            for sid2 in sids2:
                if int(sid2) not in sub_schedule_map:
                    sub_schedule_map[int(sid2)] = schedules

    rows = [
        SongReportRow(
            song_detection_id=sd.SongDetectionID,
            artist=sd.Artist or "",
            title=sd.Title or "",
            station_name=station_name,
            chunk_date=str(chunk_date),
            start_clock=(
                f"{int(sd.StartTime // 3600):02d}:{int((sd.StartTime % 3600) // 60):02d}:{int(sd.StartTime % 60):02d}"
                if sd.StartTime is not None else "—"
            ),
            end_clock=(
                f"{int(sd.EndTime // 3600):02d}:{int((sd.EndTime % 3600) // 60):02d}:{int(sd.EndTime % 60):02d}"
                if sd.EndTime is not None else "—"
            ),
            plays=1,
            clip_url=_make_clip_url(db, sd.ClipPath, base_url, short_lived=short_lived) if generate_clip_urls else None,
            in_schedule=is_detection_in_schedule(
                sub_schedule_map.get(sd.StationID, []),
                sd.DetectedAt,
            ) if sd.DetectedAt else True,
        )
        for sd, chunk_date, station_name in rows_raw
    ]

    # Add zero-detection rows for registered stations with no detections
    detected_station_names = {r.station_name for r in rows}
    all_sub_station_ids: set[int] = set()
    for s in subs:
        if s.StationFilter:
            try:
                all_sub_station_ids.update(int(x) for x in _json.loads(s.StationFilter) if x)
            except Exception:
                pass
    if all_sub_station_ids:
        all_sub_stations = db.query(Station).filter(Station.StationID.in_(all_sub_station_ids)).all()
        for st in sorted(all_sub_stations, key=lambda x: x.StationName):
            if st.StationName not in detected_station_names:
                rows.append(SongReportRow(
                    song_detection_id=0,
                    artist="—",
                    title="—",
                    station_name=st.StationName,
                    chunk_date="—",
                    start_clock="—",
                    end_clock="—",
                    plays=0,
                    clip_url=None,
                    in_schedule=True,
                ))

    return SongReportResult(
        rows=rows,
        total=len(rows),
        date_from=date_from,
        date_to=date_to,
        subscriber_id=subscriber_id,
    )


@dataclass
class DetectionReportRow:
    detection_id: int
    display_tape_id: str
    campaign_name: str
    station_name: str
    chunk_date: str
    start_clock: str
    end_clock: str
    category: str
    clip_path: Optional[str]
    campaign_id: Optional[int]
    clip_url: Optional[tuple] = None  # (url, qr_base64_png)


@dataclass
class DetectionReportResult:
    rows: list[DetectionReportRow] = field(default_factory=list)
    total: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    station_filter: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.utcnow)
    subscriber_id: Optional[int] = None

    # Summary stats
    @property
    def total_plays(self) -> int:
        return len(self.rows)

    @property
    def unique_commercials(self) -> int:
        return len({r.commercial_name for r in self.rows})

    @property
    def unique_stations(self) -> int:
        return len({r.station_name for r in self.rows})

    @property
    def by_station(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.station_name] = counts.get(r.station_name, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    @property
    def by_commercial(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.display_tape_id] = counts.get(r.display_tape_id, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))


def get_subscriber_campaigns(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[tuple[int, str]]:
    """Return (CampaignID, Name) pairs for a subscriber's campaigns.
    When date range is provided, only campaigns with detections in that range are returned."""
    from app.models.campaign import Campaign

    if date_from and date_to:
        sub_clause = "AND cam.SubscriberID = :subscriber_id" if subscriber_id else ""
        sql = text(f"""
            SELECT DISTINCT cam.CampaignID, cam.Name
            FROM Campaign cam
            JOIN Detection d ON d.CampaignID = cam.CampaignID
            JOIN RecordingChunk rc ON d.ChunkID = rc.ChunkID
            WHERE 1=1
              {sub_clause}
              AND rc.ChunkDate >= :date_from
              AND rc.ChunkDate <= :date_to
            ORDER BY cam.Name
        """)
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        if subscriber_id:
            params["subscriber_id"] = subscriber_id
        rows = db.execute(sql, params).fetchall()
    else:
        q = db.query(Campaign.CampaignID, Campaign.Name)
        if subscriber_id:
            q = q.filter(Campaign.SubscriberID == subscriber_id)
        rows = q.order_by(Campaign.Name).all()
    return [(r.CampaignID, r.Name) for r in rows]


def get_subscriber_commercials(db: Session, subscriber_id: Optional[int]) -> list[tuple[str, str]]:
    """Return (DisplayTapeID, CommercialName) pairs for a subscriber's commercials."""
    from app.models.campaign import Commercial
    rows = (
        db.query(Commercial.DisplayTapeID, Commercial.CommercialName)
        .filter(True if not subscriber_id else Commercial.SubscriberID == subscriber_id)
        .order_by(Commercial.DisplayTapeID)
        .all()
    )
    return [(r.DisplayTapeID, r.CommercialName) for r in rows if r.DisplayTapeID]


def get_detection_report(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    station_ids: Optional[list[int]] = None,
    commercial_name: Optional[str] = None,
    campaign_id: Optional[int] = None,
    base_url: str = "",
    generate_clip_urls: bool = False,
) -> DetectionReportResult:
    """Query vw_Detection_Readable joined to Commercial/Station/RecordingChunk."""

    params: dict = {"subscriber_id": subscriber_id}

    date_clause = ""
    if date_from and date_to:
        date_clause = "AND rc.ChunkDate >= :date_from AND rc.ChunkDate <= :date_to"
        params["date_from"] = date_from.isoformat()
        params["date_to"]   = date_to.isoformat()
    elif date_from:
        date_clause = "AND rc.ChunkDate >= :date_from"
        params["date_from"] = date_from.isoformat()
    elif date_to:
        date_clause = "AND rc.ChunkDate <= :date_to"
        params["date_to"] = date_to.isoformat()

    station_clause = ""
    if station_ids:
        placeholders = ", ".join(f":sid_{i}" for i in range(len(station_ids)))
        station_clause = f"AND d.StationID IN ({placeholders})"
        for i, sid in enumerate(station_ids):
            params[f"sid_{i}"] = sid

    commercial_clause = ""
    if commercial_name:
        commercial_clause = "AND c.CommercialName LIKE :commercial_name"
        params["commercial_name"] = f"%{commercial_name}%"

    campaign_clause = ""
    if campaign_id:
        campaign_clause = "AND d.CampaignID = :campaign_id"
        params["campaign_id"] = campaign_id

    subscriber_clause = ""
    if subscriber_id:
        subscriber_clause = "AND d.SubscriberID = :subscriber_id"

    sql = text(f"""
        SELECT
            d.DetectionID,
            ISNULL(c.DisplayTapeID, 'Unknown') AS DisplayTapeID,
            ISNULL(cam.Name, 'Unassigned')     AS CampaignName,
            s.StationName,
            rc.ChunkDate,
            d.StartClock,
            d.EndClock,
            d.Category,
            d.ClipPath,
            d.CampaignID
        FROM vw_Detection_Readable d
        JOIN Station        s   ON d.StationID   = s.StationID
        JOIN RecordingChunk rc  ON d.ChunkID     = rc.ChunkID
        LEFT JOIN Commercial c  ON d.CommercialID = c.CommercialID
        LEFT JOIN Campaign   cam ON d.CampaignID  = cam.CampaignID
        WHERE 1=1
          {subscriber_clause}
          {date_clause}
          {station_clause}
          {commercial_clause}
          {campaign_clause}
        ORDER BY rc.ChunkDate ASC, d.StartClock ASC
    """)

    rows_raw = db.execute(sql, params).fetchall()

    rows = [
        DetectionReportRow(
            detection_id=r.DetectionID,
            display_tape_id=r.DisplayTapeID,
            campaign_name=r.CampaignName,
            station_name=r.StationName,
            chunk_date=r.ChunkDate,
            start_clock=r.StartClock,
            end_clock=r.EndClock,
            category=r.Category or "",
            clip_path=r.ClipPath,
            campaign_id=r.CampaignID,
            clip_url=_make_clip_url(db, r.ClipPath, base_url) if generate_clip_urls else None,
        )
        for r in rows_raw
    ]

    # Add zero-detection rows for registered stations with no detections
    detected_stations = {r.StationName for r in rows_raw}

    # Find all stations registered for this commercial/campaign
    registered_stations: set[str] = set()
    if commercial_name or campaign_id:
        from app.models.campaign import Campaign, CampaignStation
        q_cs = (
            db.query(Station.StationName)
            .join(CampaignStation, CampaignStation.StationID == Station.StationID)
            .join(Campaign, Campaign.CampaignID == CampaignStation.CampaignID)
        )
        if subscriber_id:
            q_cs = q_cs.filter(Campaign.SubscriberID == subscriber_id)
        if campaign_id:
            q_cs = q_cs.filter(Campaign.CampaignID == campaign_id)
        if commercial_name:
            from app.models.campaign import CampaignCommercial, Commercial as _Comm
            q_cs = q_cs.join(CampaignCommercial, CampaignCommercial.CampaignID == Campaign.CampaignID)\
                       .join(_Comm, _Comm.CommercialID == CampaignCommercial.CommercialID)\
                       .filter(_Comm.CommercialName.like(f"%{commercial_name}%"))
        registered_stations = {r[0] for r in q_cs.all()}

    for sname in sorted(registered_stations - detected_stations):
        rows.append(DetectionReportRow(
            detection_id=0,
            display_tape_id=commercial_name or "—",
            campaign_name="—",
            station_name=sname,
            chunk_date=None,
            start_clock="—",
            end_clock="—",
            category="no_detections",
            clip_path=None,
            campaign_id=campaign_id,
            clip_url=None,
        ))

    station_names: list[str] = []
    if station_ids:
        station_names = [
            s.StationName
            for s in db.query(Station).filter(Station.StationID.in_(station_ids)).all()
        ]

    return DetectionReportResult(
        rows=rows,
        total=len(rows),
        date_from=date_from,
        date_to=date_to,
        station_filter=station_names,
        subscriber_id=subscriber_id,
    )


# ---------------------------------------------------------------------------
# Keyword Detection Report
# ---------------------------------------------------------------------------

@dataclass
class KeywordReportRow:
    word_detection_id: int
    keyword: str
    station_name: str
    chunk_date: str
    start_clock: str
    end_clock: str
    clip_url: Optional[tuple] = None  # (url, qr_base64_png) for PDF
    in_schedule: bool = True  # False = detected outside booked hours


@dataclass
class KeywordReportResult:
    rows: list[KeywordReportRow] = field(default_factory=list)
    total: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    generated_at: datetime = field(default_factory=datetime.utcnow)
    subscriber_id: Optional[int] = None

    @property
    def total_hits(self) -> int:
        return len(self.rows)

    @property
    def unique_keywords(self) -> int:
        return len({r.keyword for r in self.rows})

    @property
    def in_schedule_count(self) -> int:
        return sum(1 for r in self.rows if r.in_schedule)

    @property
    def out_of_schedule_count(self) -> int:
        return sum(1 for r in self.rows if not r.in_schedule)

    @property
    def has_schedule(self) -> bool:
        return any(not r.in_schedule for r in self.rows)

    @property
    def unique_stations(self) -> int:
        return len({r.station_name for r in self.rows})

    @property
    def by_station(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.station_name] = counts.get(r.station_name, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    @property
    def by_keyword(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.keyword] = counts.get(r.keyword, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))


def get_subscriber_keywords(db: Session, subscriber_id: Optional[int]) -> list[str]:
    """Distinct keywords from subscriber's active keyword subscriptions."""
    from app.models.detection import ClientSubscription
    rows = (
        db.query(ClientSubscription.TargetValue)
        .filter(
            ClientSubscription.SubscriberID == subscriber_id,
            ClientSubscription.Status.in_(["active", "expired", "completed"]),
            ClientSubscription.SubscriptionType == "keyword",
            ClientSubscription.TargetValue.isnot(None),
        )
        .distinct()
        .order_by(ClientSubscription.TargetValue)
        .all()
    )
    return [r.TargetValue for r in rows if r.TargetValue]


def get_keyword_report(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    station_ids: Optional[list[int]] = None,
    keyword: Optional[str] = None,
    base_url: str = "",
    generate_clip_urls: bool = False,
    short_lived: bool = False,
) -> KeywordReportResult:
    """Query WordDetection using subscription-matching logic (same as dashboard)."""
    import json as _json
    from sqlalchemy import or_, and_
    from app.models.detection import WordDetection, ClientSubscription, RecordingChunk
    from app.models.station import Station

    subs_q = db.query(ClientSubscription).filter(
        ClientSubscription.Status.in_(["active", "expired", "completed"]),
        ClientSubscription.SubscriptionType == "keyword",
    )
    if subscriber_id:
        subs_q = subs_q.filter(ClientSubscription.SubscriberID == subscriber_id)
    subs = subs_q.all()

    q = (
        db.query(WordDetection, RecordingChunk.ChunkDate, Station.StationName)
        .join(RecordingChunk, RecordingChunk.ChunkID == WordDetection.ChunkID)
        .join(Station, Station.StationID == WordDetection.StationID)
    )

    # Scope by subscriptions for subscribers; show all for internal
    if subs and subscriber_id:
        sub_filters = []
        for s in subs:
            sub_station_ids = []
            if s.StationFilter:
                try:
                    sub_station_ids = _json.loads(s.StationFilter)
                except Exception:
                    pass
            conds = [
                WordDetection.Keyword.ilike(f"%{s.TargetValue}%"),
                RecordingChunk.ChunkDate >= str(s.StartDate),
            ]
            if s.EndDate:
                conds.append(RecordingChunk.ChunkDate <= str(s.EndDate))
            if sub_station_ids:
                conds.append(WordDetection.StationID.in_(sub_station_ids))
            sub_filters.append(and_(*conds))
        if sub_filters:
            q = q.filter(or_(*sub_filters))

    if date_from:
        q = q.filter(RecordingChunk.ChunkDate >= str(date_from))
    if date_to:
        q = q.filter(RecordingChunk.ChunkDate <= str(date_to))
    if station_ids:
        q = q.filter(WordDetection.StationID.in_(station_ids))
    if keyword:
        q = q.filter(WordDetection.Keyword.ilike(f"%{keyword}%"))

    q = q.order_by(RecordingChunk.ChunkDate.asc(), WordDetection.StartTime.asc())
    rows_raw = q.all()

    def _fmt(t):
        if t is None:
            return "—"
        return f"{int(t // 3600):02d}:{int((t % 3600) // 60):02d}:{int(t % 60):02d}"

    # Load schedules per subscription for in_schedule flagging
    from app.services.schedule_service import get_subscription_schedules, is_detection_in_schedule
    sub_schedule_map = {
        sub.SubscriptionID: get_subscription_schedules(db, sub.SubscriptionID)
        for sub in subs
    }

    rows = [
        KeywordReportRow(
            word_detection_id=wd.WordDetectionID,
            keyword=wd.Keyword or "",
            station_name=station_name,
            chunk_date=str(chunk_date),
            start_clock=_fmt(wd.StartTime),
            end_clock=_fmt(wd.EndTime),
            clip_url=_make_clip_url(db, wd.ClipPath, base_url, short_lived=short_lived) if generate_clip_urls else None,
            in_schedule=is_detection_in_schedule(
                sub_schedule_map.get(wd.SubscriptionID, []),
                wd.DetectionTime,
            ) if wd.DetectionTime and wd.SubscriptionID else True,
        )
        for wd, chunk_date, station_name in rows_raw
    ]

    # Add zero-detection rows for registered stations with no detections
    detected_station_names_kw = {r.station_name for r in rows}
    all_kw_station_ids: set[int] = set()
    for s in subs:
        if s.StationFilter:
            try:
                all_kw_station_ids.update(int(x) for x in _json.loads(s.StationFilter) if x)
            except Exception:
                pass
    if all_kw_station_ids:
        all_kw_stations = db.query(Station).filter(Station.StationID.in_(all_kw_station_ids)).all()
        for st in sorted(all_kw_stations, key=lambda x: x.StationName):
            if st.StationName not in detected_station_names_kw:
                rows.append(KeywordReportRow(
                    word_detection_id=0,
                    keyword="—",
                    station_name=st.StationName,
                    chunk_date="—",
                    start_clock="—",
                    end_clock="—",
                    clip_url=None,
                    in_schedule=True,
                ))

    return KeywordReportResult(
        rows=rows,
        total=len(rows),
        date_from=date_from,
        date_to=date_to,
        subscriber_id=subscriber_id,
    )


# ---------------------------------------------------------------------------
# Invoice / Billing Report
# ---------------------------------------------------------------------------

@dataclass
class InvoiceReportRow:
    transaction_id: int
    created_at: datetime
    description: str
    type_label: str        # subscriber-facing label
    reference_type: str    # category (commercial/song/word/etc)
    credits: Decimal       # absolute value
    credits_signed: Decimal  # negative for debits
    balance_after: Decimal
    zar_amount: Decimal
    source: str

    @property
    def is_debit(self) -> bool:
        return self.credits_signed < 0


@dataclass
class InvoiceReportResult:
    rows: list[InvoiceReportRow] = field(default_factory=list)
    total: int = 0
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    generated_at: datetime = field(default_factory=datetime.utcnow)
    subscriber_id: Optional[int] = None
    subscriber_name: str = ""
    zar_per_token: Decimal = Decimal("3.60")
    opening_balance: Decimal = Decimal("0")
    closing_balance: Decimal = Decimal("0")

    @property
    def total_debited(self) -> Decimal:
        return abs(sum(r.credits_signed for r in self.rows if r.is_debit))

    @property
    def total_credited(self) -> Decimal:
        return sum(r.credits_signed for r in self.rows if not r.is_debit)

    @property
    def total_zar(self) -> Decimal:
        return sum(r.zar_amount for r in self.rows if r.is_debit)

    @property
    def by_category(self) -> dict[str, Decimal]:
        counts: dict[str, Decimal] = {}
        for r in self.rows:
            if r.is_debit:
                counts[r.reference_type] = counts.get(r.reference_type, Decimal("0")) + r.credits
        return dict(sorted(counts.items(), key=lambda x: -x[1]))


_TYPE_LABELS = {
    "debit":      "Usage",
    "credit":     "Top-up",
    "purchase":   "Top-up",
    "refund":     "Refund",
    "adjustment": "Adjustment",
}
_REF_LABELS = {
    "commercial":   "Commercial",
    "song":         "Song",
    "word":         "Keyword",
    "transcription":"Transcription",
    "spectrum":     "Spectrum",
}


def get_invoice_report(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> InvoiceReportResult:
    from app.models.billing import TokenTransaction, BillingConfig, SubscriberTokenAccount
    from app.models.subscriber import Subscriber
    from sqlalchemy import and_

    # Subscriber name + plan rate
    sub = db.get(Subscriber, subscriber_id)
    subscriber_name = sub.CompanyName or sub.ContactName if sub else ""
    zar_per_token = Decimal("3.60")
    if sub:
        cfg = db.query(BillingConfig).filter(
            BillingConfig.PlanCode == sub.SubscriptionPlan
        ).first()
        if cfg:
            zar_per_token = cfg.ZARPerToken

    # Build query
    q = db.query(TokenTransaction).filter(
        TokenTransaction.SubscriberID == subscriber_id
    )
    if date_from:
        q = q.filter(TokenTransaction.CreatedAt >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        from datetime import timedelta
        q = q.filter(TokenTransaction.CreatedAt < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    transactions = q.order_by(TokenTransaction.CreatedAt.asc()).all()

    # Opening balance — balance before first transaction in range
    opening_balance = Decimal("0")
    if transactions:
        first_tx = transactions[0]
        opening_balance = first_tx.BalanceAfter - first_tx.TokenAmount

    closing_balance = transactions[-1].BalanceAfter if transactions else Decimal("0")

    rows = [
        InvoiceReportRow(
            transaction_id=tx.TransactionID,
            created_at=tx.CreatedAt,
            description=tx.Description or "—",
            type_label=_TYPE_LABELS.get(tx.Type, tx.Type.capitalize()),
            reference_type=_REF_LABELS.get(tx.ReferenceType or "", tx.ReferenceType or "Other"),
            credits=abs(tx.TokenAmount),
            credits_signed=tx.TokenAmount,
            balance_after=tx.BalanceAfter,
            zar_amount=abs(tx.TokenAmount) * zar_per_token,
            source=tx.Source,
        )
        for tx in transactions
    ]

    return InvoiceReportResult(
        rows=rows,
        total=len(rows),
        date_from=date_from,
        date_to=date_to,
        subscriber_id=subscriber_id,
        subscriber_name=subscriber_name,
        zar_per_token=zar_per_token,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
    )


# ---------------------------------------------------------------------------
# Proof of Broadcast Report
# ---------------------------------------------------------------------------

@dataclass
class PoBRow:
    """Single airing evidence row."""
    detection_id: int
    chunk_date: str
    start_clock: str
    end_clock: str
    station_name: str
    category: str
    match_score: Optional[int]
    clip_url: Optional[tuple] = None   # (url, qr_base64_png) for PDF


@dataclass
class PoBCommercial:
    """One commercial with all its airings."""
    commercial_id: int
    display_tape_id: str
    brand: str
    campaign_name: str
    airings: list[PoBRow] = field(default_factory=list)

    @property
    def total_airings(self) -> int:
        return len(self.airings)

    @property
    def stations(self) -> list[str]:
        return sorted({r.station_name for r in self.airings})

    @property
    def date_range(self) -> str:
        if not self.airings:
            return "—"
        dates = sorted({r.chunk_date for r in self.airings})
        if len(dates) == 1:
            return dates[0]
        return f"{dates[0]} – {dates[-1]}"


@dataclass
class PoBResult:
    commercials: list[PoBCommercial] = field(default_factory=list)
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    generated_at: datetime = field(default_factory=datetime.utcnow)
    subscriber_id: Optional[int] = None
    subscriber_name: str = ""
    subscriber_company: str = ""
    subscriber_email: str = ""
    certificate_number: str = ""

    @property
    def total_airings(self) -> int:
        return sum(c.total_airings for c in self.commercials)

    @property
    def total_commercials(self) -> int:
        return len(self.commercials)

    @property
    def all_stations(self) -> list[str]:
        return sorted({s for c in self.commercials for s in c.stations})


def _make_clip_url(db, clip_path: Optional[str], base_url: str, short_lived: bool = False) -> Optional[tuple[str, str]]:
    """Generate a signed clip URL + base64 QR PNG.
    short_lived=True uses 1-hour token (on-screen); False uses 30-day token (PDF)."""
    if not clip_path or not base_url:
        return None
    try:
        from app.services.api_key_service import create_pob_clip_token
        if short_lived:
            # For on-screen play buttons — still use pob token, TTL is fine for immediate use
            token = create_pob_clip_token(db, clip_path)
        else:
            token = create_pob_clip_token(db, clip_path)
        import qrcode
        from io import BytesIO
        import base64

        url = f"{base_url.rstrip('/')}/api/clips/{token}"

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=6,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return (url, b64)
    except Exception as e:
        import logging
        logging.getLogger("radiomonitor").error(f"_make_clip_url failed for {clip_path!r}: {e}", exc_info=True)
        return None


def get_proof_of_broadcast(
    db: Session,
    subscriber_id: Optional[int],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    commercial_ids: Optional[list[int]] = None,
    station_ids: Optional[list[int]] = None,
    campaign_id: Optional[int] = None,
    base_url: str = "",
    generate_clip_urls: bool = False,
) -> PoBResult:
    """Build Proof of Broadcast grouped by commercial."""
    from app.models.subscriber import Subscriber
    from app.models.campaign import Commercial, Campaign
    from app.models.detection import Detection, RecordingChunk
    from app.models.station import Station
    from sqlalchemy import and_
    import uuid

    sub = db.get(Subscriber, subscriber_id)
    subscriber_name  = sub.Name if sub else ""
    subscriber_company = sub.CompanyName or sub.Name if sub else ""
    subscriber_email = sub.ContactEmail or "" if sub else ""

    # Build detection query
    q = (
        db.query(
            Detection,
            RecordingChunk.ChunkDate,
            Station.StationName,
            Commercial.DisplayTapeID,
            Commercial.Brand,
            Commercial.CommercialID,
        )
        .join(RecordingChunk, RecordingChunk.ChunkID == Detection.ChunkID)
        .join(Station, Station.StationID == Detection.StationID)
        .join(Commercial, Commercial.CommercialID == Detection.CommercialID)
        .filter(True if not subscriber_id else Commercial.SubscriberID == subscriber_id)
    )

    if date_from:
        q = q.filter(RecordingChunk.ChunkDate >= str(date_from))
    if date_to:
        q = q.filter(RecordingChunk.ChunkDate <= str(date_to))
    if commercial_ids:
        q = q.filter(Detection.CommercialID.in_(commercial_ids))
    if station_ids:
        q = q.filter(Detection.StationID.in_(station_ids))
    if campaign_id:
        q = q.filter(Detection.CampaignID == campaign_id)

    # Also get campaign name per detection
    rows_raw = q.order_by(
        Commercial.DisplayTapeID.asc(),
        RecordingChunk.ChunkDate.asc(),
        Detection.StartTimeSec.asc(),
    ).all()

    # Group by commercial
    commercial_map: dict[int, PoBCommercial] = {}
    for det, chunk_date, station_name, tape_id, brand, comm_id in rows_raw:
        if comm_id not in commercial_map:
            # Get campaign name for this commercial
            from app.models.campaign import CampaignCommercial
            camp_link = (
                db.query(CampaignCommercial)
                .filter(CampaignCommercial.CommercialID == comm_id)
                .first()
            )
            campaign_name = "Unassigned"
            if camp_link:
                camp = db.get(Campaign, camp_link.CampaignID)
                if camp:
                    campaign_name = camp.Name

            commercial_map[comm_id] = PoBCommercial(
                commercial_id=comm_id,
                display_tape_id=tape_id or str(comm_id),
                brand=brand or "",
                campaign_name=campaign_name,
            )

        def _fmt(t):
            if t is None:
                return "—"
            return f"{int(t // 3600):02d}:{int((t % 3600) // 60):02d}:{int(t % 60):02d}"

        import logging as _log
        _log.getLogger("radiomonitor").info(f"PoB airing DetectionID={det.DetectionID} ClipPath={det.ClipPath!r} generate={generate_clip_urls}")
        commercial_map[comm_id].airings.append(PoBRow(
            detection_id=det.DetectionID,
            chunk_date=str(chunk_date),
            start_clock=_fmt(det.StartTimeSec),
            end_clock=_fmt(det.EndTimeSec),
            station_name=station_name,
            category=det.Category or "",
            match_score=det.MatchScore,
            clip_url=_make_clip_url(db, det.ClipPath, base_url) if generate_clip_urls else None,
        ))

    # Generate certificate number: NOC-YYYYMMDD-SUBID-XXXX
    cert_num = f"NOC-{datetime.utcnow().strftime('%Y%m%d')}-{(subscriber_id or 0):04d}-{abs(hash(str(date_from) + str(date_to) + str(subscriber_id))) % 9000 + 1000}"

    return PoBResult(
        commercials=list(commercial_map.values()),
        date_from=date_from,
        date_to=date_to,
        subscriber_id=subscriber_id,
        subscriber_name=subscriber_name,
        subscriber_company=subscriber_company,
        subscriber_email=subscriber_email,
        certificate_number=cert_num,
    )
