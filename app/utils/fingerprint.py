"""
app/utils/fingerprint.py

Thin wrapper around fingerprint_core for use at commercial registration time.

Responsibilities:
  - Call fingerprint_core.fingerprint_audio(mp3_path) to get the hash dict.
  - Compute a stable FingerprintID = SHA256(sorted hash keys as JSON).
  - Maintain the audio archive: D:\\RadioMonitor\\audio_archive\\<FingerprintID>.mp3
    so we can re-stage a sibling's file on withdrawal (Option α).

The fingerprinting step takes ~1-2s per 30s mp3 on production hardware.
That's acceptable for a one-time registration submit.

See FINGERPRINT_IDENTITY_DESIGN.md for the full design rationale.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_fingerprint_id(mp3_path: str) -> tuple[str, float]:
    """
    Fingerprint the audio at mp3_path and return (FingerprintID, duration_seconds).

    FingerprintID = SHA256 hex of the JSON-serialised sorted hash keys.
    Two encodes of the same source audio produce the same FingerprintID
    (because the peak positions and hash keys are content-derived, not byte-derived).

    Raises RuntimeError if fingerprinting fails (bad file, ffmpeg unavailable, etc.)
    """
    try:
        from app.utils.fingerprint_core import fingerprint_audio
        hashes, duration = fingerprint_audio(mp3_path)
    except Exception as e:
        raise RuntimeError(f"Fingerprinting failed: {e}") from e

    # Stable key: sort the hash tuples (each is (f1, f2, dt)) then JSON-encode.
    sorted_keys = sorted(str(k) for k in hashes.keys())
    digest = hashlib.sha256(json.dumps(sorted_keys).encode()).hexdigest()
    return digest, duration


def archive_path(archive_root: str, fingerprint_id: str) -> str:
    """Canonical audio archive path for a given FingerprintID."""
    return os.path.join(archive_root, f"{fingerprint_id}.mp3")


def write_to_archive(source_mp3: str, archive_root: str, fingerprint_id: str) -> str:
    """
    Copy source_mp3 into the audio archive (write-then-rename for atomicity).
    Returns the archive path. No-op if the archive file already exists.
    """
    dest = archive_path(archive_root, fingerprint_id)
    if os.path.exists(dest):
        return dest  # already archived by a prior registration of the same audio
    os.makedirs(archive_root, exist_ok=True)
    tmp = dest + ".tmp"
    try:
        shutil.copy2(source_mp3, tmp)
        os.replace(tmp, dest)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError(f"Failed to write audio archive: {e}") from e
    return dest


def stage_from_archive(
    archive_root: str,
    fingerprint_id: str,
    station_names: list[str],
    commercial_name: str,
    *,
    sample_dir_fn,
) -> list[str]:
    """
    Re-stage a sibling's audio file from the archive when the file-owner row
    withdraws (Option α). Reads from archive, writes to each station's staging
    folder using write-then-rename.

    commercial_name: the full prefixed filename stem (e.g. "42_TAPEID")
    sample_dir_fn: callable(station_name, category) -> directory path
    """
    src = archive_path(archive_root, fingerprint_id)
    if not os.path.exists(src):
        raise RuntimeError(
            f"Audio archive missing for FingerprintID {fingerprint_id!r}; "
            "cannot re-stage sibling."
        )
    written = []
    for station_name in station_names:
        directory = sample_dir_fn(station_name, "generic")
        os.makedirs(directory, exist_ok=True)
        dest = os.path.join(directory, f"{commercial_name}.mp3")
        tmp = dest + ".tmp"
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
            written.append(dest)
        except OSError as e:
            for p in written:
                try:
                    os.remove(p)
                except OSError:
                    pass
            raise RuntimeError(f"Re-stage failed for {station_name}: {e}") from e
    return written
