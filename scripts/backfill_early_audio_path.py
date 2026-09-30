#!/usr/bin/env python
"""
scripts/backfill_early_audio_path.py

One-time backfill: populates RecordingChunk.EarlyAudioPath for chunks where
the audio file has been manually copied (or already exists) under
D:\\RadioMonitor\\audio\\<station>\\<date>\\<filename>.mp3 -- the convention
confirmed correct, with NO '\\archives\\' subfolder (unlike the older
AudioPath values which include it).

Only writes EarlyAudioPath if the file is verified to exist on disk at the
expected path. Never overwrites an EarlyAudioPath that's already set.

Safe to run multiple times.

Usage:
    python scripts/backfill_early_audio_path.py [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("radiomonitor.backfill_early_audio")


def main(dry_run: bool = False) -> None:
    from app.database import SessionLocal
    from app.models.detection import RecordingChunk
    from app.models.station import Station
    from app.config import settings

    logger.info("EarlyAudioPath backfill starting (dry_run=%s)", dry_run)
    logger.info("Checking under: %s", settings.station_audio_root)

    with SessionLocal() as db:
        chunks = (
            db.query(RecordingChunk)
            .filter(RecordingChunk.EarlyAudioPath.is_(None))
            .filter(RecordingChunk.FileName.isnot(None))
            .order_by(RecordingChunk.ChunkID)
            .all()
        )

        station_map = {s.StationID: s.StationName for s in db.query(Station).all()}

        logger.info("%d chunks have NULL EarlyAudioPath -- checking disk for each", len(chunks))

        found = 0
        missing = 0
        for chunk in chunks:
            station_name = station_map.get(chunk.StationID)
            if not station_name or not chunk.ChunkDate or not chunk.FileName:
                missing += 1
                continue

            # Folder on disk uses two-digit year (YY-MM-DD), e.g. '26-06-24',
            # while RecordingChunk.ChunkDate is stored as YYYY-MM-DD.
            # Convert before building the candidate path.
            chunk_date_str = str(chunk.ChunkDate)
            if len(chunk_date_str) == 10 and chunk_date_str[4] == "-":
                # '2026-06-24' -> '26-06-24'
                folder_date = chunk_date_str[2:]
            else:
                folder_date = chunk_date_str

            # Confirmed convention: <station>\<date>\<filename>.mp3 -- NO 'archives' subfolder
            candidate = os.path.join(
                settings.station_audio_root, station_name, folder_date, chunk.FileName
            )

            if os.path.isfile(candidate):
                found += 1
                if dry_run:
                    logger.info("  [DRY RUN] Would set ChunkID=%d -> %s", chunk.ChunkID, candidate)
                else:
                    chunk.EarlyAudioPath = candidate
                    logger.info("  ChunkID=%d -> %s", chunk.ChunkID, candidate)
            else:
                missing += 1

        if not dry_run:
            db.commit()

        logger.info(
            "Backfill complete. %d files found and %s, %d not found on disk (skipped).",
            found, "would be set" if dry_run else "set", missing,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be set without committing")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
