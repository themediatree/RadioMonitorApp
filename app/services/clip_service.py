"""
Clip-serving helpers.

Detection.ClipPath stores what the pipeline wrote. To serve it to a browser we
must:
  1. Confirm the user is authorized to see the detection (handled by the route
     via scoping; this module just deals with the file path).
  2. Resolve ClipPath against the configured clips_path root.
  3. Verify the resolved path is actually UNDER that root (no traversal).
  4. Return the absolute path only if it points at a real file; else None.

We deliberately treat both relative and absolute pipeline-written paths
defensively. If the pipeline ever writes a path outside the configured root,
we refuse it rather than serve it.
"""

from __future__ import annotations

import os
import posixpath

from app.config import settings


def resolve_clip_path(stored_path: str) -> str | None:
    """
    Translate a Detection.ClipPath into a safe absolute filesystem path.
    Returns None if the path is missing, escapes the clips root, or the file
    doesn't exist.

    Accepts both relative (e.g. "5FM/2026-05-28/clip_42.mp3") and absolute
    (e.g. "D:\\RadioMonitor\\clips\\5FM\\2026-05-28\\clip_42.mp3") forms; in
    either case the result must resolve INSIDE clips_path.
    """
    if not stored_path:
        return None

    root_abs = os.path.abspath(settings.clips_path)
    # Normalize both forward and back slashes; we may be running on Windows.
    candidate = stored_path.replace("\\", os.sep).replace("/", os.sep)

    if os.path.isabs(candidate):
        full = os.path.abspath(candidate)
    else:
        full = os.path.abspath(os.path.join(root_abs, candidate))

    # Containment check: realpath of `full` must be under realpath of `root_abs`.
    # Using commonpath because startswith on raw strings is unsafe for paths.
    try:
        common = os.path.commonpath([os.path.realpath(full), os.path.realpath(root_abs)])
    except ValueError:
        # Different drives on Windows -> definitely outside the root.
        return None
    if os.path.realpath(common) != os.path.realpath(root_abs):
        return None

    if not os.path.isfile(full):
        return None
    return full


def resolve_chunk_audio_path(stored_path: str | None) -> str | None:
    """
    Resolve RecordingChunk.EarlyAudioPath (preferred) or AudioPath against
    station_audio_root, with the same containment check as resolve_clip_path.

    EarlyAudioPath values still pointing at 'C:\\...' (AUDIOPROC-local,
    not yet copied to AUDIOREC) will correctly fail the containment check
    and return None -- the caller should treat that as "not yet available"
    rather than an error.
    """
    if not stored_path:
        return None

    root_abs = os.path.abspath(settings.station_audio_root)
    candidate = stored_path.replace("\\", os.sep).replace("/", os.sep)

    if os.path.isabs(candidate):
        full = os.path.abspath(candidate)
    else:
        full = os.path.abspath(os.path.join(root_abs, candidate))

    try:
        common = os.path.commonpath([os.path.realpath(full), os.path.realpath(root_abs)])
    except ValueError:
        return None
    if os.path.realpath(common) != os.path.realpath(root_abs):
        return None

    if not os.path.isfile(full):
        return None
    return full


def resolve_generic_transcript_path(stored_path: str) -> str | None:
    """
    Same containment logic as resolve_clip_path, but rooted at
    settings.generic_transcripts_root instead of clips_path.
    Used for generic commercial karaoke transcripts (FingerprintID-keyed,
    written once by the pipeline's generic_transcriber.py).
    """
    if not stored_path:
        return None

    root_abs = os.path.abspath(settings.generic_transcripts_root)
    candidate = stored_path.replace("\\", os.sep).replace("/", os.sep)

    if os.path.isabs(candidate):
        full = os.path.abspath(candidate)
    else:
        full = os.path.abspath(os.path.join(root_abs, candidate))

    try:
        common = os.path.commonpath([os.path.realpath(full), os.path.realpath(root_abs)])
    except ValueError:
        return None
    if os.path.realpath(common) != os.path.realpath(root_abs):
        return None

    if not os.path.isfile(full):
        return None
    return full


def guess_content_type(path: str) -> str:
    """Minimal content-type for the clip kinds we serve."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
    }.get(ext, "application/octet-stream")
