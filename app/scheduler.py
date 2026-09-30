"""
app/scheduler.py

APScheduler setup for RadioMonitor.
Import and call setup_scheduler(app) in main.py lifespan.

Usage in main.py:

    from contextlib import asynccontextmanager
    from app.scheduler import setup_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_scheduler()
        yield
        shutdown_scheduler()

    app = FastAPI(lifespan=lifespan)
"""

import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("radiomonitor")
_scheduler: BackgroundScheduler | None = None


def _run_midnight_sync():
    """Job function — runs in background thread at midnight."""
    from app.database import SessionLocal
    from app.services.station_scheduler import run_midnight_tasks
    db = SessionLocal()
    try:
        run_midnight_tasks(db)
    except Exception as e:
        logger.error(f"[MIDNIGHT] Tasks failed: {e}", exc_info=True)
    finally:
        db.close()


def _run_transcription_retry():
    """Retry pending transcription requests — runs every 5 minutes."""
    from app.database import SessionLocal
    from app.services.transcription_service import retry_pending_requests
    db = SessionLocal()
    try:
        result = retry_pending_requests(db)
        if result["processed"] > 0:
            logger.info(
                f"[TRANSCRIPTION] Auto-retry: {result['processed']} resolved, "
                f"{result['still_pending']} still pending"
            )
    except Exception as e:
        logger.error(f"[TRANSCRIPTION] Auto-retry failed: {e}", exc_info=True)
    finally:
        db.close()


def setup_scheduler():
    """Start APScheduler with midnight station sync job. Call at app startup."""
    global _scheduler
    from app.config import settings

    _scheduler = BackgroundScheduler(timezone="Africa/Johannesburg")

    # Midnight: full station sync + campaign expiry + transcription retry
    _scheduler.add_job(
        _run_midnight_sync,
        CronTrigger(hour=0, minute=1, timezone="Africa/Johannesburg"),
        id="midnight_station_sync",
        name="Midnight station IsActive sync",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Every 5 minutes: retry pending transcription requests
    # Runs in all environments — transcriptions can arrive any time of day
    _scheduler.add_job(
        _run_transcription_retry,
        CronTrigger(minute="*/5", timezone="Africa/Johannesburg"),
        id="transcription_retry",
        name="Transcription pending retry",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    _scheduler.start()
    logger.info("[SCHEDULER] Started — midnight sync at 00:01 SAST, transcription retry every 5 min")

    # Run transcription retry immediately on startup to catch anything missed
    _run_transcription_retry()

    # Only run full midnight sync on startup in production
    if settings.is_production:
        _run_midnight_sync()
        logger.info("[SCHEDULER] Startup sync complete")
    else:
        logger.info("[SCHEDULER] Startup sync skipped (non-production)")


def shutdown_scheduler():
    """Stop scheduler cleanly on app shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Stopped")
