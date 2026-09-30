"""
app/services/station_scheduler.py

Comprehensive midnight scheduler that:

1. Flips Campaign.IsActive → False when EndDate has passed
2. Flips Commercial.IsActive/Status when all its campaigns have ended
3. Flips ClientSubscription.Status → 'expired' when EndDate has passed
4. Keeps Station.IsActive in sync with ALL 5 services:
      - Campaign (via CampaignStation)
      - ClientSubscription (songs + keywords)
      - TranscriptionRequest
      - SongDetectionJob
      - WordDetectionJob
5. Retries pending TranscriptionRequests

Called:
  - Daily at 00:01 SAST via APScheduler
  - At app startup in production
  - Immediately when a service is created/cancelled (activate/deactivate helpers)
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("radiomonitor")


# ---------------------------------------------------------------------------
# 1. Expire campaigns whose EndDate has passed
# ---------------------------------------------------------------------------

def expire_campaigns(db: Session) -> list[str]:
    """Sets Campaign.IsActive=0 where EndDate < today."""
    today = date.today()
    rows = db.execute(text("""
        UPDATE Campaign
        SET    IsActive  = 0,
               UpdatedAt = SYSDATETIME()
        OUTPUT INSERTED.Name
        WHERE  IsActive  = 1
          AND  EndDate   IS NOT NULL
          AND  EndDate   < :today
    """), {"today": today}).fetchall()
    names = [r[0] for r in rows]
    if names:
        logger.info(f"[SCHEDULER] Campaigns expired ({len(names)}): {names}")
    db.commit()
    return names


# ---------------------------------------------------------------------------
# 2. Expire commercial when ALL its campaigns are inactive/ended
# ---------------------------------------------------------------------------

def expire_commercials(db: Session) -> list[str]:
    """
    Sets Commercial.IsActive=0 and Status='completed' when every campaign
    that uses the commercial has ended (IsActive=0 or EndDate < today).
    """
    today = date.today()
    rows = db.execute(text("""
        UPDATE Commercial
        SET    IsActive  = 0,
               Status    = 'completed'
        OUTPUT INSERTED.CommercialName
        WHERE  IsActive  = 1
          AND  CommercialID NOT IN (
              SELECT DISTINCT cc.CommercialID
              FROM   CampaignCommercial cc
              JOIN   Campaign c ON c.CampaignID = cc.CampaignID
              WHERE  c.IsActive = 1
                AND  (c.EndDate IS NULL OR c.EndDate >= :today)
          )
          AND  CommercialID IN (
              SELECT DISTINCT CommercialID FROM CampaignCommercial
          )
    """), {"today": today}).fetchall()
    names = [r[0] for r in rows]
    if names:
        logger.info(f"[SCHEDULER] Commercials expired ({len(names)}): {names}")
    db.commit()
    return names


# ---------------------------------------------------------------------------
# 3. Expire subscriptions whose EndDate has passed
# ---------------------------------------------------------------------------

def expire_subscriptions(db: Session) -> int:
    """Sets ClientSubscription.Status='expired' where EndDate < today."""
    today = date.today()
    result = db.execute(text("""
        UPDATE ClientSubscription
        SET    Status    = 'expired',
               UpdatedAt = SYSDATETIME()
        WHERE  Status    = 'active'
          AND  EndDate   IS NOT NULL
          AND  EndDate   < :today
    """), {"today": today})
    count = result.rowcount
    if count:
        logger.info(f"[SCHEDULER] Subscriptions expired: {count}")
    db.commit()
    return count


# ---------------------------------------------------------------------------
# 4. Station IsActive sync — checks ALL 5 services
# ---------------------------------------------------------------------------

def _get_active_station_ids(db: Session) -> set[int]:
    """
    Returns set of StationIDs that should be IsActive=1 today,
    considering all 5 services.
    """
    today = date.today()
    active = set()

    # ── A: Campaigns (via CampaignStation) ──
    rows = db.execute(text("""
        SELECT DISTINCT cs.StationID
        FROM   CampaignStation cs
        JOIN   Campaign c ON c.CampaignID = cs.CampaignID
        WHERE  c.IsActive  = 1
          AND  c.StartDate <= :today
          AND  (c.EndDate IS NULL OR c.EndDate >= :today)
    """), {"today": today}).fetchall()
    active.update(r[0] for r in rows)

    # ── B: ClientSubscription songs + keywords (StationFilter JSON) ──
    sub_rows = db.execute(text("""
        SELECT StationFilter
        FROM   ClientSubscription
        WHERE  Status    = 'active'
          AND  StartDate <= :today
          AND  (EndDate IS NULL OR EndDate >= :today)
          AND  StationFilter IS NOT NULL
    """), {"today": today}).fetchall()
    for (sf,) in sub_rows:
        try:
            ids = json.loads(sf or "[]")
            if isinstance(ids, list):
                active.update(int(i) for i in ids if i)
        except (json.JSONDecodeError, ValueError):
            pass

    # ── C: TranscriptionRequest ──
    rows = db.execute(text("""
        SELECT DISTINCT StationID
        FROM   TranscriptionRequest
        WHERE  Status   IN ('pending', 'ready', 'processing')
          AND  DateFrom <= :today
          AND  DateTo   >= :today
    """), {"today": today}).fetchall()
    active.update(r[0] for r in rows)

    # ── D: SongDetectionJob ──
    rows = db.execute(text("""
        SELECT DISTINCT sf.StationID
        FROM   SongDetectionJob j
        CROSS APPLY (
            SELECT value AS StationID
            FROM   OPENJSON(j.StationFilter)
            WHERE  j.StationFilter IS NOT NULL
        ) sf
        WHERE  j.Status NOT IN ('completed', 'failed', 'cancelled')
          AND  j.DateFrom <= :today
          AND  j.DateTo   >= :today
    """), {"today": today}).fetchall()
    active.update(r[0] for r in rows if r[0])

    # ── E: WordDetectionJob ──
    rows = db.execute(text("""
        SELECT DISTINCT sf.StationID
        FROM   WordDetectionJob j
        CROSS APPLY (
            SELECT value AS StationID
            FROM   OPENJSON(j.StationFilter)
            WHERE  j.StationFilter IS NOT NULL
        ) sf
        WHERE  j.Status NOT IN ('completed', 'failed', 'cancelled')
          AND  j.DateFrom <= :today
          AND  j.DateTo   >= :today
    """), {"today": today}).fetchall()
    active.update(r[0] for r in rows if r[0])

    return active


def sync_station_active_flags(db: Session) -> dict:
    """
    Recalculates IsActive for every station based on today's date,
    checking all 5 services. Returns {"activated": [...], "deactivated": [...]}.
    """
    should_be_active = _get_active_station_ids(db)

    all_stations = db.execute(text("""
        SELECT StationID, StationName, IsActive FROM Station
    """)).fetchall()

    activated   = []
    deactivated = []

    for station_id, station_name, is_active in all_stations:
        should = station_id in should_be_active

        if should and not is_active:
            db.execute(text("UPDATE Station SET IsActive=1 WHERE StationID=:id"),
                       {"id": station_id})
            activated.append(station_name)
            logger.info(f"[SCHEDULER] Station ACTIVATED: {station_name} (ID={station_id})")

        elif not should and is_active:
            db.execute(text("UPDATE Station SET IsActive=0 WHERE StationID=:id"),
                       {"id": station_id})
            deactivated.append(station_name)
            logger.info(f"[SCHEDULER] Station DEACTIVATED: {station_name} (ID={station_id})")

    db.commit()

    logger.info(
        f"[SCHEDULER] Station sync complete — "
        f"activated: {activated or 'none'}, deactivated: {deactivated or 'none'}"
    )
    return {"activated": activated, "deactivated": deactivated}


# ---------------------------------------------------------------------------
# 5. Full midnight run — call all of the above in order
# ---------------------------------------------------------------------------

def run_midnight_tasks(db: Session) -> dict:
    """
    Master function called by the scheduler at 00:01 SAST.
    Runs all lifecycle tasks in the correct order.
    """
    logger.info("[SCHEDULER] Starting midnight tasks")

    # Step 1: Expire campaigns first (affects commercial expiry check)
    expired_campaigns = expire_campaigns(db)

    # Step 2: Expire commercials whose campaigns all ended
    expired_commercials = expire_commercials(db)

    # Step 3: Expire subscriptions
    expired_subs = expire_subscriptions(db)

    # Step 4: Retry pending transcription requests
    from app.services.transcription_service import retry_pending_requests
    transcription_result = retry_pending_requests(db)

    # Step 5: Sync station active flags (after everything else is updated)
    station_result = sync_station_active_flags(db)

    logger.info(
        f"[SCHEDULER] Midnight tasks complete — "
        f"campaigns expired: {len(expired_campaigns)}, "
        f"commercials expired: {len(expired_commercials)}, "
        f"subscriptions expired: {expired_subs}, "
        f"transcriptions resolved: {transcription_result.get('processed', 0)}, "
        f"stations activated: {len(station_result['activated'])}, "
        f"stations deactivated: {len(station_result['deactivated'])}"
    )

    return {
        "expired_campaigns":    expired_campaigns,
        "expired_commercials":  expired_commercials,
        "expired_subs":         expired_subs,
        "transcriptions":       transcription_result,
        "stations":             station_result,
    }


# ---------------------------------------------------------------------------
# Immediate helpers — called when services are created/cancelled
# ---------------------------------------------------------------------------

def activate_station_if_needed(db: Session, station_id: int) -> bool:
    """
    Called when any service is registered on a station.
    Activates immediately if the service starts today or in the past.
    """
    active_ids = _get_active_station_ids(db)
    if station_id in active_ids:
        db.execute(text("UPDATE Station SET IsActive=1 WHERE StationID=:id"),
                   {"id": station_id})
        db.flush()
        logger.info(f"[SCHEDULER] Station {station_id} activated immediately")
        return True
    return False


def deactivate_station_if_idle(db: Session, station_id: int) -> bool:
    """
    Called when a service is cancelled on a station.
    Deactivates only if no other active services remain.
    """
    active_ids = _get_active_station_ids(db)
    if station_id not in active_ids:
        db.execute(text("UPDATE Station SET IsActive=0 WHERE StationID=:id"),
                   {"id": station_id})
        db.flush()
        logger.info(f"[SCHEDULER] Station {station_id} deactivated (no remaining active work)")
        return True
    return False
