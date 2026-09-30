"""
Audio helpers -- convert an uploaded generic commercial to mp3 via ffmpeg.

ffmpeg is confirmed present on AUDIOREC (C:\\ffmpeg\\bin\\ffmpeg.exe, on PATH).
The path is configurable (settings.ffmpeg_path) so it isn't fragile. If ffmpeg
is somehow unavailable at runtime, conversion raises a clear AudioError rather
than producing a broken file.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from app.config import settings


class AudioError(Exception):
    """Raised when audio conversion fails or ffmpeg is unavailable."""


def ffmpeg_available() -> bool:
    """True if the configured ffmpeg can be located."""
    exe = settings.ffmpeg_path
    # Absolute path that exists, or resolvable on PATH.
    if os.path.isabs(exe):
        return os.path.isfile(exe)
    return shutil.which(exe) is not None


def convert_to_mp3(source_path: str, dest_path: str, *, timeout: int = 120) -> str:
    """
    Convert any ffmpeg-readable audio at source_path to mp3 at dest_path.
    Returns dest_path on success. Raises AudioError on failure.

    Writes to a temp file then renames, so a failed/partial conversion never
    leaves a half-written mp3 at dest_path.
    """
    if not ffmpeg_available():
        raise AudioError(
            "ffmpeg is not available on this server; cannot convert audio. "
            "Set settings.ffmpeg_path or install ffmpeg."
        )

    tmp_dest = dest_path + ".tmp"
    cmd = [
        settings.ffmpeg_path,
        "-y",                 # overwrite tmp if present
        "-i", source_path,
        "-vn",                # no video
        "-acodec", "libmp3lame",
        "-q:a", "2",          # good VBR quality (~190 kbps)
        "-f", "mp3",          # explicit: output is mp3 (the .tmp ext hides it)
        tmp_dest,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        _safe_remove(tmp_dest)
        raise AudioError("Audio conversion timed out.")
    except OSError as e:
        _safe_remove(tmp_dest)
        raise AudioError(f"Could not run ffmpeg: {e}")

    if proc.returncode != 0 or not os.path.isfile(tmp_dest):
        _safe_remove(tmp_dest)
        # ffmpeg writes diagnostics to stderr; surface the tail for debugging.
        tail = (proc.stderr or b"").decode("utf-8", "replace")[-300:]
        raise AudioError(f"ffmpeg failed to convert audio. {tail}")

    os.replace(tmp_dest, dest_path)
    return dest_path


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
