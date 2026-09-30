"""
Application settings.

Reads from environment variables (and a .env file in development).
Builds the SQLAlchemy connection URL from individual fields or accepts a
full override via DATABASE_URL.

Usage:
    from app.config import settings
    print(settings.app_name)
"""

from functools import lru_cache
from typing import Literal, Optional
from urllib.parse import quote_plus

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = "RadioMonitor"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = True
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:8000"
    base_url: str = "http://localhost:8000"

    # --- Database ---
    db_driver: str = "ODBC Driver 18 for SQL Server"
    db_server: str = r"localhost\SQLEXPRESS"
    db_name: str = "RadioMonitor"
    db_trusted_connection: str = "no"        # AUDIOREC uses a SQL login
    db_user: str = "radiomonitor_user"
    db_password: str = ""
    db_encrypt: str = "yes"                   # ODBC 18 defaults to Encrypt=yes
    db_trust_server_certificate: str = "yes"  # self-signed cert on AUDIOREC
    database_url: Optional[str] = None       # if set, takes precedence

    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800

    # --- Auth ---
    jwt_secret: str = Field(default="CHANGE_ME", min_length=8)
    jwt_algorithm: str = "HS256"

    @property
    def jwt_secret_is_weak(self) -> bool:
        return self.jwt_secret in ("CHANGE_ME",) or len(self.jwt_secret) < 32
    jwt_access_token_minutes: int = 60
    jwt_refresh_token_days: int = 14
    session_cookie_name: str = "rm_session"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    password_min_length: int = 10

    # --- Paths (all local to AUDIOREC) ---
    pipeline_data_root: str = r"D:\RadioMonitor"
    clips_path: str = r"D:\RadioMonitor\clips"
    audio_archive_root: str = r"D:\RadioMonitor\audio_archive"
    station_audio_root: str = r"D:\RadioMonitor\audio"
    # Root for full station recording chunks (RecordingChunk.AudioPath /
    # EarlyAudioPath), distinct from audio_archive_root which holds
    # registered commercial fingerprints.
    generic_transcripts_root: str = r"D:\RadioMonitor\generic_transcripts"
    bulk_upload_staging_root: str = r"D:\RadioMonitor\bulk_staging"
    transcription_requests_root: str = r"D:\RadioMonitor\transcription_requests"
    audio_path: str = r"D:\RadioMonitor\audio"
    transcripts_path: str = r"D:\RadioMonitor\transcripts"
    # Web-app-uploaded commercial samples go to a STAGING tree on AUDIOREC,
    # mirroring the pipeline's target layout: <staging>\<station>\<category>\<TapeID>.<ext>
    # The web app CANNOT write to AUDIOPROC's data tree directly (decided: no
    # inbound share to AUDIOPROC), so it fans out into staging here, and a
    # pipeline-side puller mirror-copies staging -> C:\RadioMonitor\data on
    # AUDIOPROC before the nightly library rebuild. See
    # COMMERCIAL_REGISTRATION_CONTRACT.md. The staging root is config so it can
    # point at whatever share the pipeline puller watches.
    samples_staging_root: str = r"D:\RadioMonitor\staging"  # + \<station>\<category>

    # ffmpeg for converting uploaded generic audio to mp3. Default "ffmpeg"
    # resolves via PATH (confirmed present on AUDIOREC at C:\ffmpeg\bin). Set an
    # absolute path here if PATH resolution is ever unreliable.
    ffmpeg_path: str = "ffmpeg"

    # --- Email (placeholder for Phase 2) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@radiomonitor.example"
    smtp_use_tls: bool = True

    # ------------------------------------------------------------------
    # Computed: SQLAlchemy connection URL
    # ------------------------------------------------------------------
    @computed_field  # type: ignore[misc]
    @property
    def sqlalchemy_url(self) -> str:
        """
        If DATABASE_URL is explicitly set, use it. Otherwise build from parts.
        We build a pyodbc-compatible URL using URL-encoded ODBC connection
        string fragments so it works with the SQL Server driver.
        """
        if self.database_url:
            return self.database_url

        parts: list[str] = [
            f"DRIVER={{{self.db_driver}}}",
            f"SERVER={self.db_server}",
            f"DATABASE={self.db_name}",
            f"Encrypt={self.db_encrypt}",
            f"TrustServerCertificate={self.db_trust_server_certificate}",
        ]
        if self.db_trusted_connection.lower() == "yes":
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.db_user}")
            parts.append(f"PWD={self.db_password}")

        odbc_str = ";".join(parts)
        return f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"

    @computed_field  # type: ignore[misc]
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # ------------------------------------------------------------------
    # Sample directory resolution (Phase 6)
    # ------------------------------------------------------------------
    def sample_dir(self, station: str, category: str) -> str:
        r"""
        Build the on-disk STAGING directory where a client-uploaded sample
        belongs, mirroring the pipeline's target layout:

            generic  -> <staging_root>\<station>\generic
            liveread -> <staging_root>\<station>\liveread

        The web app fans out one copy per assigned station into this staging
        tree; a pipeline-side puller mirror-copies it into AUDIOPROC's real
        data tree. `station` must be exactly Station.StationName and a single
        safe path segment. Names containing path separators, '..', or that are
        absolute are REJECTED (not silently sanitized). Raises ValueError on a
        bad category or unsafe station name.

        Uses Windows path semantics (ntpath); targets are on AUDIOREC's D:\.
        """
        import ntpath

        category = category.lower().strip()
        if category not in ("generic", "liveread"):
            raise ValueError(f"Unknown sample category: {category!r}")

        seg = station.strip()
        if (
            not seg
            or seg in (".", "..")
            or "/" in seg
            or "\\" in seg
            or ":" in seg
        ):
            raise ValueError(f"Unsafe station name: {station!r}")

        return ntpath.join(self.samples_staging_root, seg, category)


@lru_cache
def get_settings() -> Settings:
    """Cache so we don't re-parse the env on every import."""
    return Settings()


# Module-level convenience -- import this in most places.
settings = get_settings()
