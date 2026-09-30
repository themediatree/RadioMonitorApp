"""
fingerprint_core.py
====================
Shared fingerprinting engine. Shazam-style constellation hashing.

Used by:
  - build_fingerprint_library.py (to ingest reference audio)
  - audio_fingerprint_detector.py (to match chunks against library)

Pure numpy/scipy. No external deps beyond what the transcriber already uses.

Algorithm:
  1. Convert audio to mono 8kHz PCM (handles any input format via ffmpeg)
  2. Compute log-magnitude spectrogram
  3. Find local-maximum spectrogram peaks above threshold
  4. For each peak (anchor), pair with next N peaks (targets) within time window
  5. Hash each (anchor_freq, target_freq, delta_time) tuple
  6. Store hashes with anchor times

Matching:
  - For each shared hash between query and reference:
    offset = query_time - reference_time
  - Correct match has many hashes agreeing on the same offset
  - Return offset with highest hash count (if above threshold)
"""

import os
import subprocess
import tempfile
import numpy as np
from collections import defaultdict

# ============================================================
# Tunable parameters
# ============================================================
SAMPLE_RATE = 8000             # resample everything to this
NFFT = 512                     # FFT window size
HOP = 256                      # FFT hop size (frames per second = SAMPLE_RATE / HOP = 31.25)
PEAK_NEIGHBORHOOD = 20         # local-max filter size
PEAK_AMP_MIN = 10              # min magnitude (dB) to be a peak
FAN_VALUE = 15                 # target peaks per anchor
MIN_HASH_TIME_DELTA = 0
MAX_HASH_TIME_DELTA = 200      # frames (~6.4s)

# Anti-noise: a hash that appears at 500+ different times is noise
MAX_HASH_OCCURRENCES = 500


# ============================================================
# Audio loading (via ffmpeg, no pydub needed)
# ============================================================
def load_audio_as_pcm(audio_path, sr=SAMPLE_RATE):
    """
    Decode audio to mono float32 at `sr` samples/sec, using ffmpeg.
    Returns (samples, duration_seconds).
    """
    with tempfile.NamedTemporaryFile(
        suffix=".pcm", delete=False, dir=tempfile.gettempdir()
    ) as tmp:
        pcm_path = tmp.name

    try:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", audio_path,
            "-ac", "1",
            "-ar", str(sr),
            "-f", "s16le",
            pcm_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr}")

        samples = np.fromfile(pcm_path, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            raise RuntimeError(f"empty audio after decode: {audio_path}")
        samples /= np.max(np.abs(samples) + 1e-9)
        duration = len(samples) / sr
        return samples, duration
    finally:
        try:
            os.remove(pcm_path)
        except OSError:
            pass


# ============================================================
# Fingerprinting
# ============================================================
def compute_spectrogram(samples, nfft=NFFT, hop=HOP):
    """Log-magnitude spectrogram. Shape: (n_frames, nfft/2 + 1)."""
    from numpy.lib.stride_tricks import sliding_window_view
    if len(samples) < nfft:
        samples = np.pad(samples, (0, nfft - len(samples)))
    windows = sliding_window_view(samples, nfft)[::hop]
    windows = windows * np.hanning(nfft)
    fft = np.fft.rfft(windows, axis=1)
    mag = 20 * np.log10(np.abs(fft) + 1e-9)
    return mag


def find_peaks(spec, neighborhood=PEAK_NEIGHBORHOOD, amp_min=PEAK_AMP_MIN):
    """Local maxima in the spectrogram. Returns list of (time_idx, freq_idx), sorted by time."""
    from scipy.ndimage import maximum_filter
    local_max = maximum_filter(spec, size=neighborhood) == spec
    mask = local_max & (spec > amp_min)
    t_idx, f_idx = np.where(mask)
    peaks = list(zip(t_idx.tolist(), f_idx.tolist()))
    peaks.sort()
    return peaks


def generate_hashes(peaks, fan_value=FAN_VALUE,
                    min_dt=MIN_HASH_TIME_DELTA, max_dt=MAX_HASH_TIME_DELTA):
    """
    For each peak (anchor), pair with next `fan_value` peaks, produce hash tuples.
    Yields (hash_key, anchor_time).
    """
    n = len(peaks)
    for i in range(n):
        t1, f1 = peaks[i]
        for j in range(1, fan_value + 1):
            k = i + j
            if k >= n:
                break
            t2, f2 = peaks[k]
            dt = t2 - t1
            if dt < min_dt or dt > max_dt:
                continue
            yield (f1, f2, dt), t1


def fingerprint_audio(audio_path):
    """
    Full pipeline: load audio, compute spectrogram, find peaks, generate hashes.
    Returns (hashes_dict, duration_seconds)
      hashes_dict: {hash_key: [anchor_time_frame, ...]}
    """
    samples, duration = load_audio_as_pcm(audio_path)
    spec = compute_spectrogram(samples)
    peaks = find_peaks(spec)
    hashes = defaultdict(list)
    for h, t in generate_hashes(peaks):
        hashes[h].append(t)
    return dict(hashes), duration


# ============================================================
# Matching
# ============================================================
def match_query_to_reference(query_hashes, ref_hashes,
                             min_aligned=30, time_quantile=0.05):
    """
    Match a query's hashes against a single reference.

    Args:
      query_hashes: {hash_key: [time_frames]}
      ref_hashes:   {hash_key: [time_frames]}
      min_aligned: reject matches with fewer aligned hashes
      time_quantile: trim this fraction of outlier matches at each end
                     when computing match start/end

    Returns None if no match, or dict:
      offset_frames: int (query_time - ref_time at best offset)
      aligned_hashes: int (count at that offset)
      query_start_frame: int (first matching query time, after outlier trim)
      query_end_frame:   int (last matching query time, after outlier trim)
      ref_start_frame:   int (first matching ref time)
      ref_end_frame:     int (last matching ref time)
    """
    # Build offset histogram + record (query_time, ref_time) pairs at best offset
    offsets = defaultdict(int)
    offset_pairs = defaultdict(list)

    for h, q_times in query_hashes.items():
        if h not in ref_hashes:
            continue
        # Anti-noise: skip hashes that are suspiciously common
        if len(q_times) > MAX_HASH_OCCURRENCES or len(ref_hashes[h]) > MAX_HASH_OCCURRENCES:
            continue
        r_times = ref_hashes[h]
        for qt in q_times:
            for rt in r_times:
                off = qt - rt
                offsets[off] += 1
                offset_pairs[off].append((qt, rt))

    if not offsets:
        return None

    best_offset, best_count = max(offsets.items(), key=lambda x: x[1])
    if best_count < min_aligned:
        return None

    # For time-range refinement, look at pairs at the best offset (with small tolerance)
    # Real matches cluster around an offset but may have 1-frame jitter
    relevant_offsets = {best_offset - 1, best_offset, best_offset + 1}
    pairs = []
    for off in relevant_offsets:
        pairs.extend(offset_pairs.get(off, []))

    pairs.sort()  # sort by query time
    n_pairs = len(pairs)
    if n_pairs < 3:
        return None

    # Trim outlier matches from each end
    trim = max(1, int(n_pairs * time_quantile))
    trimmed = pairs[trim:-trim] if n_pairs > 2 * trim else pairs

    q_times = [p[0] for p in trimmed]
    r_times = [p[1] for p in trimmed]

    return {
        "offset_frames": best_offset,
        "aligned_hashes": best_count,
        "query_start_frame": min(q_times),
        "query_end_frame": max(q_times),
        "ref_start_frame": min(r_times),
        "ref_end_frame": max(r_times),
    }


def frames_to_seconds(frames, hop=HOP, sr=SAMPLE_RATE):
    return frames * hop / sr


def seconds_to_frames(seconds, hop=HOP, sr=SAMPLE_RATE):
    return int(seconds * sr / hop)
