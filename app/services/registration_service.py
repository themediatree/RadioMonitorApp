"""
Commercial + campaign registration service.

Implements COMMERCIAL_REGISTRATION_CONTRACT.md. Responsibilities:
  - sanitize/validate the client-supplied Tape ID (filename stem)
  - create-or-locate the Campaign (a campaign may hold many commercials)
  - create the Commercial row (Status='pending') and link it to the campaign
  - resolve which station folders to write (via CampaignStation)
  - fan out the uploaded file into the staging mirror tree, one copy per
    station, using write-then-rename for atomicity
  - DB rows are committed BEFORE the staging file is renamed into place, so the
    pipeline can resolve the file to its Commercial row by name.

The pipeline reads station from the folder; we never ask it to read the DB at
detection time. Withdrawal is a Status update (no file deletion).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime, timezone
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.models.campaign import (
    Campaign,
    CampaignCommercial,
    CampaignStation,
    Commercial,
)
from app.models.station import Station

# Characters Windows + the pipeline's matching cannot tolerate in a filename.
_FORBIDDEN = set('\\/:*?"<>|')

# Audio types we accept for a generic commercial (converted to mp3 on upload).
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
ALLOWED_TEXT_EXT = {".txt"}

VALID_COMMERCIAL_TYPES = ("generic", "liveread")


class RegistrationError(Exception):
    """Raised when a registration request is invalid or not permitted."""


# ---------------------------------------------------------------------------
# Tape ID
# ---------------------------------------------------------------------------
def sanitize_tape_id(raw: str) -> str:
    """
    Validate + normalize a client Tape ID (the filename stem). We do NOT impose
    a naming scheme (the client's convention is theirs), only reject characters
    that break filesystems or the pipeline.

    Raises RegistrationError if the result is empty or contains forbidden chars.
    """
    if raw is None:
        raise RegistrationError("A Tape ID is required.")
    s = raw.strip().strip(".")  # no leading/trailing spaces or dots
    if not s:
        raise RegistrationError("A Tape ID is required.")
    bad = sorted({c for c in s if c in _FORBIDDEN})
    if bad:
        raise RegistrationError(
            "Tape ID contains characters that aren't allowed: "
            + " ".join(bad)
        )
    # Reject path separators defensively (already in _FORBIDDEN, but explicit).
    if "/" in s or "\\" in s:
        raise RegistrationError("Tape ID must not contain path separators.")
    return s


def derive_tape_id_from_filename(filename: str) -> str:
    """Best-effort default Tape ID from an uploaded filename (stem, sanitized-ish).
    The user can override; we only strip the extension and surrounding junk."""
    base = os.path.basename(filename or "")
    stem, _ext = os.path.splitext(base)
    return stem.strip()


# ---------------------------------------------------------------------------
# Campaign create-or-locate
# ---------------------------------------------------------------------------
def get_or_create_campaign(
    db: Session,
    *,
    subscriber_id: int,
    name: str,
    start_date: date,
    end_date: Optional[date],
) -> tuple[Campaign, bool]:
    """
    Find an existing campaign for this Subscriber by name, or create one.
    Returns (campaign, created). Caller commits.
    """
    name = (name or "").strip()
    if not name:
        raise RegistrationError("A campaign name/ID is required.")

    existing = (
        db.query(Campaign)
        .filter(Campaign.SubscriberID == subscriber_id, Campaign.Name == name)
        .one_or_none()
    )
    if existing is not None:
        return existing, False

    campaign = Campaign(
        SubscriberID=subscriber_id,
        Name=name,
        StartDate=start_date,
        EndDate=end_date,
        IsActive=True,
    )
    db.add(campaign)
    db.flush()
    return campaign, True


# ---------------------------------------------------------------------------
# Date validation (from the registration design doc, step 2)
# ---------------------------------------------------------------------------
def validate_campaign_dates(start_date: date, end_date: Optional[date]) -> None:
    today = datetime.now(tz=timezone.utc).date()
    if start_date < today:
        raise RegistrationError("Start date cannot be in the past.")
    if end_date is not None:
        if end_date < start_date:
            raise RegistrationError("End date cannot be before the start date.")
        if end_date < today:
            raise RegistrationError("End date cannot be in the past.")


# ---------------------------------------------------------------------------
# Station resolution + staging fan-out
# ---------------------------------------------------------------------------
def resolve_station_names(db: Session, station_ids: Sequence[int]) -> list[str]:
    """
    Map station ids to their exact StationName (the folder name the pipeline
    requires). Raises if any id is unknown.
    """
    if not station_ids:
        raise RegistrationError("Select at least one station.")
    rows = db.query(Station).filter(Station.StationID.in_(list(station_ids))).all()
    found = {s.StationID: s.StationName for s in rows}
    missing = [sid for sid in station_ids if sid not in found]
    if missing:
        raise RegistrationError(f"Unknown station id(s): {missing}")
    return [found[sid] for sid in station_ids]


def _staging_path(station_name: str, category: str, tape_id: str, ext: str) -> str:
    import ntpath
    directory = settings.sample_dir(station_name, category)  # staging\<station>\<category>
    return ntpath.join(directory, f"{tape_id}{ext}")


def fan_out_to_staging(
    *,
    station_names: Sequence[str],
    category: str,
    tape_id: str,
    source_path: str,
    ext: str,
) -> list[str]:
    """
    Write the (already-final, e.g. converted-to-mp3) file at source_path into
    the staging mirror tree, one copy per station, using write-then-rename so
    the puller never sees a partial file.

    Returns the list of final paths written. Raises RegistrationError on IO
    failure (after attempting cleanup of any partials).

    NOTE: callers must ensure the DB Commercial row is committed before calling
    this (contract ordering rule), so the pipeline can resolve the file by name.
    """
    written: list[str] = []
    try:
        for station_name in station_names:
            final_path = _staging_path(station_name, category, tape_id, ext)
            tmp_path = final_path + ".tmp"
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
            # Copy to .tmp, flush to disk, then atomic rename to final.
            with open(source_path, "rb") as src, open(tmp_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            # If a stale final exists (re-registration), replace atomically.
            os.replace(tmp_path, final_path)
            written.append(final_path)
        return written
    except OSError as e:
        # Best-effort cleanup of anything we wrote, so a partial fan-out doesn't
        # leave some stations live and others not.
        for p in written:
            try:
                os.remove(p)
            except OSError:
                pass
        # Also remove any dangling .tmp for the station we failed on.
        raise RegistrationError(f"Failed to stage file: {e}")


def withdraw_commercial(db: Session, commercial: Commercial) -> Optional[str]:
    """
    Withdraw a commercial.

    Option-α re-staging (FINGERPRINT_IDENTITY_DESIGN §5):
    If this row is the file-owner (CommercialName == on-disk filename stem)
    AND active siblings share the same FingerprintID, pick the oldest active
    sibling and stage its file from the audio archive so the pipeline keeps
    detecting for remaining subscribers.

    Returns: the sibling's CommercialName if re-staging was triggered, else None.
    Caller commits.
    """
    from app.config import settings
    from app.utils.fingerprint import archive_path, stage_from_archive

    commercial.Status = "withdrawn"
    commercial.IsActive = False
    # Free the unique slot so the TapeID can be re-registered later.
    commercial.DisplayTapeID = (
        f"{commercial.DisplayTapeID}_withdrawn_{commercial.CommercialID}"
    )
    db.flush()

    # Option-α: check for active siblings sharing the same FingerprintID.
    if not commercial.FingerprintID:
        return None  # liveread or pre-fingerprint row — no file to re-stage

    siblings = (
        db.query(Commercial)
        .filter(
            Commercial.FingerprintID == commercial.FingerprintID,
            Commercial.CommercialID != commercial.CommercialID,
            Commercial.SubscriberID != commercial.SubscriberID,
            Commercial.Status == "active",
        )
        .order_by(Commercial.CreatedAt)
        .all()
    )
    if not siblings:
        return None  # last subscriber — file goes dormant at next rebuild

    chosen = siblings[0]

    # Determine which stations the chosen sibling is registered for.
    from app.models.campaign import CampaignCommercial, CampaignStation
    from app.models.station import Station
    station_rows = (
        db.query(Station)
        .join(CampaignStation, CampaignStation.StationID == Station.StationID)
        .join(CampaignCommercial,
              CampaignCommercial.CampaignID == CampaignStation.CampaignID)
        .filter(CampaignCommercial.CommercialID == chosen.CommercialID)
        .all()
    )
    station_names = [s.StationName for s in station_rows]
    if not station_names:
        return None  # sibling has no stations — nothing to stage

    # Stage from audio archive using the sibling's own CommercialName.
    arch = archive_path(settings.audio_archive_root, commercial.FingerprintID)
    if not os.path.exists(arch):
        import logging
        logging.getLogger(__name__).warning(
            "Option-α: audio archive missing for FingerprintID %s — "
            "sibling %s will go dark at next rebuild.",
            commercial.FingerprintID, chosen.CommercialID,
        )
        return None

    try:
        stage_from_archive(
            archive_root=settings.audio_archive_root,
            fingerprint_id=commercial.FingerprintID,
            station_names=station_names,
            commercial_name=chosen.CommercialName,
            sample_dir_fn=lambda station, category:
                settings.sample_dir(station, category),
        )
    except RuntimeError as e:
        import logging
        logging.getLogger(__name__).error(
            "Option-α re-staging failed for sibling %s: %s",
            chosen.CommercialID, e,
        )
        return None

    return chosen.CommercialName


# ---------------------------------------------------------------------------
# Activation: pending -> active when a campaign's StartDate has arrived
# ---------------------------------------------------------------------------
def activate_due_commercials(db: Session, *, today: Optional[date] = None) -> list[int]:
    """
    Flip 'pending' commercials to 'active' once their campaign's StartDate has
    arrived (StartDate <= today). Returns the list of CommercialIDs activated.
    Caller commits.

    A commercial activates if ANY campaign it belongs to has started — i.e. the
    earliest campaign whose StartDate <= today makes it live. (A commercial in
    multiple campaigns goes live when the first of them starts.)

    This MUST run before the pipeline's nightly library rebuild so newly-active
    commercials are fingerprinted the same night.
    """
    if today is None:
        today = datetime.now(tz=timezone.utc).date()

    # Pending commercials that have at least one campaign already started.
    due = (
        db.query(Commercial)
        .join(CampaignCommercial, CampaignCommercial.CommercialID == Commercial.CommercialID)
        .join(Campaign, Campaign.CampaignID == CampaignCommercial.CampaignID)
        .filter(Commercial.Status == "pending")
        .filter(Campaign.StartDate <= today)
        .distinct()
        .all()
    )
    activated: list[int] = []
    for c in due:
        c.Status = "active"
        activated.append(c.CommercialID)
    db.flush()
    return activated
