"""
Detection models -- the facts the pipeline produces.

Four detection tables (pipeline writes; web app reads):
  Detection       -- commercial airings
  SongDetection   -- song airings (Shazam-identified)
  WordDetection   -- keyword airings (transcript-scanned)
  RecordingChunk  -- audio chunk metadata (date/time of each recorded segment)

Two subscription tables (web app writes; pipeline reads every 60s):
  ClientSubscription  -- active song/keyword subscriptions
  SongDetectionJob    -- retrospective song search jobs
  WordDetectionJob    -- retrospective word search jobs
"""

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Detection(Base):
    """A commercial airing. Scoped through CampaignID -> Campaign."""

    __tablename__ = "Detection"

    DetectionID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ChunkID: Mapped[int] = mapped_column(BigInteger, nullable=False)
    CommercialID: Mapped[int] = mapped_column(
        ForeignKey("Commercial.CommercialID"), nullable=False
    )
    StationID: Mapped[int] = mapped_column(ForeignKey("Station.StationID"), nullable=False)
    Category: Mapped[str] = mapped_column(String(20), nullable=False)
    StartTimeSec: Mapped[float] = mapped_column(Float, nullable=False)
    EndTimeSec: Mapped[float] = mapped_column(Float, nullable=False)
    MatchRatio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ClipPath: Mapped[str] = mapped_column(String(500), nullable=False)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    CampaignID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Campaign.CampaignID"), nullable=True
    )
    MatchScore: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    DetectionMethod: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    WordTimestampPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Set to 1 when subscriber explicitly accepts an out-of-spot detection into
    # their main list. Accepted detections still show the orange "Out of spot"
    # badge but appear in the main detections list and reports.
    IsAccepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    commercial = relationship("Commercial")
    campaign = relationship("Campaign")

    def __repr__(self) -> str:
        return (f"<Detection id={self.DetectionID} commercial={self.CommercialID} "
                f"station={self.StationID}>")


class ClientSubscription(Base):
    """
    Song / keyword subscriptions. Web app writes; pipeline reads every 60s.
    SubscriptionType values: 'song_track', 'song_title', 'keyword'
    StationFilter: JSON array of StationIDs or NULL (all stations)
    """
    __tablename__ = "ClientSubscription"

    SubscriptionID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=False
    )
    SubscriptionType: Mapped[str] = mapped_column(String(50), nullable=False)
    TargetValue: Mapped[str] = mapped_column(String(500), nullable=False)
    StationFilter: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    StartDate: Mapped[date] = mapped_column(Date, nullable=False)
    EndDate: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    Artist: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    Title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    CreatedByUserID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("User.UserID"), nullable=True
    )
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    UpdatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    Source: Mapped[str] = mapped_column(String(10), nullable=False, default="app")

    subscriber = relationship("Subscriber")
    schedules = relationship("SubscriptionSchedule", back_populates="subscription", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return (f"<ClientSubscription id={self.SubscriptionID} "
                f"type={self.SubscriptionType} value={self.TargetValue!r}>")


class SongDetection(Base):
    """A song airing identified by Shazam. Pipeline detects all songs;
    Subscribers scope via SubscriptionID at read time."""

    __tablename__ = "SongDetection"

    SongDetectionID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ChunkID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    StationID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Station.StationID"), nullable=True
    )
    Artist: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    Title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    TrackID: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ClipPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    StartTime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    EndTime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    DetectedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    SubscriptionID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ClientSubscription.SubscriptionID"), nullable=True
    )
    DetectionSource: Mapped[str] = mapped_column(
        String(20), nullable=False, default="live"
    )
    JobID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("SongDetectionJob.JobID"), nullable=True
    )

    subscription = relationship("ClientSubscription")

    def __repr__(self) -> str:
        return f"<SongDetection id={self.SongDetectionID} title={self.Title!r}>"


class WordDetection(Base):
    """A keyword airing. Pipeline writes one row per subscription per hit."""

    __tablename__ = "WordDetection"

    WordDetectionID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ChunkID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    StationID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Station.StationID"), nullable=True
    )
    Keyword: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ClipPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    StartTime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    EndTime: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ContextBeforeSec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ContextAfterSec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    DetectionTime: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    SubscriptionID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ClientSubscription.SubscriptionID"), nullable=True
    )
    DetectionSource: Mapped[str] = mapped_column(
        String(20), nullable=False, default="live"
    )
    JobID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("WordDetectionJob.JobID"), nullable=True
    )
    WordTimestampPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    subscription = relationship("ClientSubscription")

    def __repr__(self) -> str:
        return f"<WordDetection id={self.WordDetectionID} keyword={self.Keyword!r}>"


class SongDetectionJob(Base):
    """Retrospective song search job. Web app creates; pipeline processes."""

    __tablename__ = "SongDetectionJob"

    JobID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=False
    )
    SubscriptionID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ClientSubscription.SubscriptionID"), nullable=True
    )
    TrackID: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ArtistFilter: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    TitleFilter: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    StationFilter: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    DateFrom: Mapped[date] = mapped_column(Date, nullable=False)
    DateTo: Mapped[date] = mapped_column(Date, nullable=False)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    StartedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CompletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ResultCount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ErrorMessage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    subscriber = relationship("Subscriber")

    def __repr__(self) -> str:
        return f"<SongDetectionJob id={self.JobID} status={self.Status}>"


class WordDetectionJob(Base):
    """Retrospective word/phrase search job. Web app creates; pipeline processes."""

    __tablename__ = "WordDetectionJob"

    JobID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    SubscriberID: Mapped[int] = mapped_column(
        ForeignKey("Subscriber.SubscriberID"), nullable=False
    )
    SubscriptionID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ClientSubscription.SubscriptionID"), nullable=True
    )
    Keyword: Mapped[str] = mapped_column(String(500), nullable=False)
    StationFilter: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    DateFrom: Mapped[date] = mapped_column(Date, nullable=False)
    DateTo: Mapped[date] = mapped_column(Date, nullable=False)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    StartedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CompletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ResultCount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ErrorMessage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    subscriber = relationship("Subscriber")

    def __repr__(self) -> str:
        return f"<WordDetectionJob id={self.JobID} keyword={self.Keyword!r}>"


class RecordingChunk(Base):
    """Audio chunk written by the pipeline recorder."""
    __tablename__ = "RecordingChunk"

    ChunkID: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    StationID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Station.StationID"), nullable=True
    )
    FileName: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ChunkDate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    StartTime: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    DurationSec: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    AudioPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    EarlyAudioPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class Transcript(Base):
    """Audio transcript written by the pipeline's Whisper transcriber."""
    __tablename__ = "Transcript"

    TranscriptID: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ChunkID: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    JsonPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    TextPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    EarlyJsonPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    EarlyTextPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    FullText: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    WordCount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    CreatedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GenericTranscriptionJob(Base):
    """
    Generic commercial transcription job. Web app inserts one row per
    FingerprintID at registration time (deduped -- only if no transcript
    already exists). Pipeline polls Status='pending' every 10s.

    Input:  D:\\RadioMonitor\\audio_archive\\<FingerprintID>.mp3
    Output: D:\\RadioMonitor\\generic_transcripts\\<FingerprintID>.json/.txt
    """
    __tablename__ = "GenericTranscriptionJob"

    JobID: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    FingerprintID: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    CommercialID: Mapped[Optional[int]] = mapped_column(
        ForeignKey("Commercial.CommercialID"), nullable=True
    )
    AudioPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    Status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    JsonPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    TextPath: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    CreatedAt: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    StartedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    CompletedAt: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ErrorMessage: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    commercial = relationship("Commercial")

    def __repr__(self) -> str:
        return (f"<GenericTranscriptionJob id={self.JobID} "
                f"fp={self.FingerprintID[:8]}… status={self.Status}>")
