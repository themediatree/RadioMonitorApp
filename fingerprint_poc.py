"""
fingerprint_poc.py
==================
One-shot test: does a known reference audio match a known broadcast chunk?

Usage:
    python fingerprint_poc.py <reference_audio> <chunk_audio>

Example:
    python fingerprint_poc.py ^
        "C:\\RadioMonitor\\data\\5FM\\generic\\5FM_DISCHEM-E.mp3" ^
        "C:\\RadioMonitor\\audio\\5FM\\26-04-29\\archives\\5FM_26-04-29_081002.mp3"

Uses the SAME fingerprint engine as audio_fingerprint_detector.py, so results
are directly comparable. If this POC finds a match but production doesn't,
the bug is in the production pipeline (not the algorithm). If neither finds
a match, the bug is in the data/algorithm/threshold combination.

Output explains:
  - How many hashes the reference and chunk produced
  - Strongest match found
  - Where in the chunk the reference appears (if anywhere)
  - Whether the score would pass production threshold (30) or even the weak
    diagnostic threshold (10)
"""

import os
import sys

# Use the same engine the production detector uses
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from fingerprint_core import (
        fingerprint_audio,
        match_query_to_reference,
        frames_to_seconds,
    )
except ImportError:
    print("ERROR: fingerprint_core.py must be in the same folder as this script.")
    print("Copy fingerprint_core.py from your C:\\RadioMonitor\\ folder.")
    sys.exit(1)


PRODUCTION_THRESHOLD = 30
WEAK_DIAGNOSTIC_THRESHOLD = 10


def main():
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <reference_audio> <chunk_audio>")
        print()
        print("Both arguments must be paths to .mp3 files.")
        sys.exit(1)

    ref_path = sys.argv[1]
    chunk_path = sys.argv[2]

    if not os.path.exists(ref_path):
        print(f"ERROR: Reference not found: {ref_path}")
        sys.exit(1)
    if not os.path.exists(chunk_path):
        print(f"ERROR: Chunk not found: {chunk_path}")
        sys.exit(1)

    # Fingerprint the reference
    print(f"Fingerprinting reference: {os.path.basename(ref_path)}")
    try:
        ref_hashes, ref_dur = fingerprint_audio(ref_path)
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)
    print(f"  duration: {ref_dur:.2f}s")
    print(f"  unique hashes: {len(ref_hashes)}")
    print(f"  total occurrences: {sum(len(v) for v in ref_hashes.values())}")
    print()

    # Fingerprint the chunk
    print(f"Fingerprinting chunk: {os.path.basename(chunk_path)}")
    try:
        chunk_hashes, chunk_dur = fingerprint_audio(chunk_path)
    except Exception as e:
        print(f"  FAIL: {e}")
        sys.exit(1)
    print(f"  duration: {chunk_dur:.2f}s")
    print(f"  unique hashes: {len(chunk_hashes)}")
    print(f"  total occurrences: {sum(len(v) for v in chunk_hashes.values())}")
    print()

    # Match (use the diagnostic threshold = 10 to surface even weak matches)
    print("Running match (using weak threshold of 10 to see anything)...")
    result = match_query_to_reference(
        chunk_hashes, ref_hashes,
        min_aligned=WEAK_DIAGNOSTIC_THRESHOLD,
    )

    print()
    print("=" * 70)
    if result is None:
        print("RESULT: NO MATCH AT ALL")
        print()
        print("Even at the weakest threshold (10 hashes), no temporal alignment")
        print("between reference and chunk hashes was found. This means:")
        print()
        print("  - The reference audio and the chunk audio share fewer than 10")
        print("    aligning hashes at any single time offset.")
        print()
        print("Likely explanations:")
        print("  1. The commercial does NOT actually air in this chunk")
        print("  2. The aired version differs significantly from the reference")
        print("     (different mix, edit, or station-applied processing)")
        print("  3. The reference audio is corrupt or wrong file")
        print("  4. The chunk audio is corrupt or silent")
        print("=" * 70)
        sys.exit(0)

    score = result["aligned_hashes"]
    offset_s = frames_to_seconds(result["offset_frames"])
    aired_start = max(0, offset_s)
    aired_end = offset_s + ref_dur

    print(f"RESULT: MATCH FOUND")
    print()
    print(f"  Score (aligned hashes): {score}")
    print(f"  Production threshold:   {PRODUCTION_THRESHOLD}")
    print(f"  Status: {'WOULD DETECT in production' if score >= PRODUCTION_THRESHOLD else 'BELOW production threshold (would not detect)'}")
    print()
    print(f"  Match offset: {offset_s:.2f}s")
    print(f"  -> Reference appears in chunk from {aired_start:.2f}s to {aired_end:.2f}s")
    if offset_s < 0:
        print(f"     (Started {-offset_s:.2f}s BEFORE this chunk -- partial overlap)")
    if aired_end > chunk_dur:
        print(f"     (Extends {aired_end - chunk_dur:.2f}s AFTER this chunk -- continues into next)")
    print()
    print(f"  Confidence indicators:")
    print(f"    - {score} hashes aligned at one offset = strong evidence of real match")
    print(f"    - As a sanity check: {score} / {len(ref_hashes)} = "
          f"{100.0*score/max(1,len(ref_hashes)):.1f}% of reference hashes aligned")
    print("=" * 70)


if __name__ == "__main__":
    main()
