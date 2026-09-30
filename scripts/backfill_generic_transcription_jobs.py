#!/usr/bin/env python
"""
scripts/backfill_generic_transcription_jobs.py

One-time backfill: creates GenericTranscriptionJob rows for generic
commercials that were registered BEFORE the transcription job feature
was deployed (migration 017 + registration.py hook).

Safe to run multiple times -- skips any FingerprintID that already has
a job (same dedup logic as the live registration flow).

Usage:
    python scripts/backfill_generic_transcription_jobs.py [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("radiomonitor.backfill_transcription")


def main(dry_run: bool = False) -> None:
    from app.database import SessionLocal
    from app.models.campaign import Commercial
    from app.models.detection import GenericTranscriptionJob
    from app.utils.fingerprint import archive_path
    from app.config import settings

    logger.info("Generic transcription job backfill starting (dry_run=%s)", dry_run)

    with SessionLocal() as db:
        # All generic commercials with a FingerprintID, NOT withdrawn,
        # that have no existing job for that fingerprint.
        existing_fps = {
            row[0] for row in db.query(GenericTranscriptionJob.FingerprintID).all()
        }

        candidates = (
            db.query(Commercial)
            .filter(
                Commercial.CommercialType == "generic",
                Commercial.FingerprintID.isnot(None),
                Commercial.Status != "withdrawn",
            )
            .order_by(Commercial.CreatedAt)
            .all()
        )

        # Dedup by FingerprintID -- only need one job per unique audio,
        # picking the earliest-registered commercial as the reference.
        seen_fps = set()
        to_create = []
        for c in candidates:
            if c.FingerprintID in existing_fps or c.FingerprintID in seen_fps:
                continue
            seen_fps.add(c.FingerprintID)
            to_create.append(c)

        logger.info(
            "%d generic commercials found, %d unique fingerprints, "
            "%d already have jobs, %d need backfilling",
            len(candidates), len(seen_fps) + len(existing_fps),
            len(existing_fps), len(to_create),
        )

        if dry_run:
            logger.info("[DRY RUN] Would create jobs for:")
            for c in to_create:
                logger.info(
                    "  CommercialID=%d  Name=%s  FingerprintID=%s",
                    c.CommercialID, c.CommercialName, c.FingerprintID[:16] + "…",
                )
            return

        created = 0
        for c in to_create:
            job = GenericTranscriptionJob(
                FingerprintID=c.FingerprintID,
                CommercialID=c.CommercialID,
                AudioPath=archive_path(settings.audio_archive_root, c.FingerprintID),
                Status="pending",
                CreatedAt=datetime.now(),
            )
            db.add(job)
            created += 1
            logger.info(
                "Queued: CommercialID=%d Name=%s FingerprintID=%s…",
                c.CommercialID, c.CommercialName, c.FingerprintID[:16],
            )

        db.commit()
        logger.info("Backfill complete. %d jobs created.", created)

    logger.info("Pipeline will pick these up on its next 10s poll.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be created without committing")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
