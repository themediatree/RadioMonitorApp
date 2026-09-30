# Return Briefing — Multi-Subscriber Commercial Detections (Pipeline → Web App)

**Short version:** Option A is what the pipeline does today, but it's the wrong
default for production at the scale you're describing. **My recommendation is
Option C, and audio-blob comparison is the right tool — but NOT a raw byte
hash. It's the fingerprint itself.** The pipeline already produces exactly the
audio-equivalence signal you'd need for C, for free. Details below.

---

## 1) Which model does the pipeline produce today? — Option A

The fingerprint matcher iterates every reference in the station-filtered
library and matches each independently:

```python
station_refs = filter_refs_for_station(lib, station)
for ref_key, ref_info in station_refs.items():
    result = match_query_to_reference(query_hashes, ref_info["hashes"], ...)
    if result.matched:
        ...insert Detection row keyed by this ref's CommercialID
```

So if 4 `Commercial` rows exist for the same audio under different TapeIDs, the
library has 4 entries, and an airing produces **4 separate Detection rows** —
one per CommercialID. That IS Option A.

---

## 2) Is the per-airing rate a problem at production scale? — **Yes, this matters.**

Let me give you the actual cost shape rather than handwave. The pipeline's
matching cost per audio chunk is roughly:

> **time ∝ (chunks_per_day × stations × refs_per_station)**

Each chunk is matched against every reference for its station. So
"refs_per_station" is the multiplier. Some grounding:

- **Memory per ref:** ~3,000-6,000 hashes for a 30s commercial,
  ~50-150 KB in the in-memory library dict. 500 refs ≈ 30-75 MB. Fine.
- **CPU per chunk per ref:** the `match_query_to_reference` does a hash-bin
  alignment scan over both sets. Reasonably fast individually, but it's
  per-chunk-per-ref — linear in refs.
- **Library-manager cost (nightly rebuild):** the .pkl rebuild fingerprints
  every audio file in `data\<station>\generic\`. If you write N duplicate files
  for the same audio, you spend N× CPU fingerprinting identical content. A
  30s mp3 takes ~1-2s to fingerprint; 500 dupes = 8-16 minutes of pure waste.
- **Disk on AUDIOREC:** every detection writes a clip. 4 dupes × 100 airings
  × 5 stations = 2,000 mp3 clips for ONE underlying ad. Each ~500 KB → 1 GB
  per ad per election cycle. SQL Express has a 10 GB DB limit and AUDIOREC's
  disk also has to hold audio chunks + transcripts. **This is the real
  problem, not CPU.**

**Your election-season example (10 Subscribers × 100 airings × 5 stations =
5,000 Detection rows for one ad) is the concrete bad scenario.** Detection rows
themselves are small (a few hundred bytes), so 5,000 rows is fine *as data*.
But each row points at a unique clip file. 5,000 clip files for one ad. And
five extractor passes if all 10 happened to be the same transcript-detectable
liveread.

So: Option A works, and we could push it through. But it's wasteful in a way
that grows linearly with how popular RadioMonitor gets — exactly the wrong
scaling.

---

## 3) Audio-blob comparison — yes, possible; cheap; you already have it

You asked the right question. Comparing audio CPU/memory-wise depends on what
"compare" means:

- **Raw byte hash (SHA256 of the mp3):** cheap (~10ms for 500KB), but fragile.
  Two encodes of the same source from different ffmpeg versions/bitrates
  produce different bytes. Useless for equivalence.
- **Full audio decode + bytewise PCM compare:** ~1s per file, robust to
  encoding, but slow and brittle to silence/trim differences.
- **The fingerprint itself, used as the identity:** this is the right tool.
  The pipeline ALREADY computes a content-derived hash dictionary that is
  deterministic for the same audio and robust to encoding differences. **Two
  identical-content mp3s produce identical (or near-identical) fingerprint
  outputs.** That's what fingerprinting is for.

So the audio-equivalence test isn't a new system; it's **a query against the
fingerprint output the library-manager already runs.** Practical shapes:

- Compare the SHA256 of the *sorted hash key set* between two
  fingerprints. Cheap (KB-sized), deterministic, gives you "these two files
  are content-equivalent" in O(1) after fingerprinting.
- Or use the count of matching hash keys between two fingerprints (the same
  metric the matcher uses) and threshold it — "≥95% hash overlap = same
  underlying audio."

CPU/memory is **not** the constraint here. Fingerprinting a 30s mp3 is ~1-2s
on your hardware (we've measured this). Hashing the resulting hash-set is
microseconds. The "blob comparison" you were worried about is, in practice,
two SHA256s of small dicts. Trivial.

---

## 4) My recommendation: **Option C with a `FingerprintID` shared identity**

The pipeline computes fingerprints for you regardless. Let's lean on that.

### How it would work

1. **At web-app registration time** (Subscriber uploads audio), the web app
   computes the audio fingerprint once and derives a stable content hash from
   it (SHA256 of sorted hash keys). Call this `FingerprintID`.
2. Two Subscribers uploading the same underlying audio resolve to the **same
   `FingerprintID`**.
3. `Commercial` rows from different Subscribers can share a `FingerprintID`.
   Each row keeps its own per-Subscriber TapeID, owner, and metadata — but they
   all point at the same audio-content identity.
4. **In staging, only ONE file is dropped per (station, FingerprintID)** — the
   first registration's audio. Later Subscribers registering equivalent audio
   just write the new `Commercial` row pointing at the existing
   `FingerprintID`; no new file lands in staging.
5. The pipeline produces **one Detection row per airing** (the existing
   behavior, no per-Subscriber explosion).
6. The web app fans the detection to all Subscribers by joining
   `Detection.CommercialID → Commercial.FingerprintID → all Commercial rows
   with that FingerprintID → their Subscribers`.

### Why this is correct (not just convenient)

- **It's the only model that scales sub-linearly in Subscribers.** A and B
  both pay an N× cost in either CPU, disk, or detection rows. C pays once and
  fans out at read time.
- **It uses existing pipeline output.** The fingerprint is already deterministic
  and already computed. The web app's "is this audio equivalent to one we
  already have?" check is a one-time fingerprint at registration + a hash
  lookup. No new analysis infrastructure.
- **The midnight rule still works.** New file → next midnight library rebuild.
  Withdrawal of one Subscriber's row doesn't remove the file (others may still
  reference the `FingerprintID`); only when the LAST active Commercial row
  pointing at a FingerprintID goes withdrawn does the library-manager skip it.

### What this asks of each side

**Web app (more work, but it's the right place for it):**
- Add `Commercial.FingerprintID NVARCHAR(64) NULL` (SHA256 hex).
- At registration upload, run fingerprinting on the uploaded audio. Compute
  `FingerprintID` from it. (Web app gains a fingerprint dependency — needs
  `fingerprint_core.py` accessible on AUDIOREC, or call out to a small worker.)
- Check whether a `FingerprintID` already exists with active rows. If yes:
  write the new `Commercial` row pointing at that `FingerprintID`, do NOT
  stage a new file for stations where the fingerprint is already represented.
  If no: stage one file per assigned station as today, and the first row
  pointing at this `FingerprintID` becomes the file's owner-of-record.
- Detection view: scope by joining detection → Commercial → FingerprintID →
  all rows with that FingerprintID → Subscribers.

**Pipeline (small additions):**
- Library-manager: when fingerprinting a file, store the same content hash
  on the library entry so file ↔ FingerprintID round-trips. (Optional —
  it's already deterministic, but storing it explicitly removes ambiguity.)
- `db_get_or_create_commercial` already lookups by `CommercialName`. With
  per-Subscriber TapeIDs that are uniqueness-prefixed (see §5 below), this
  continues to work — each Commercial row resolves correctly by name. The
  important bit is that the library has one entry pointing at one
  CommercialID (the "owner of record" for that file in the data tree); the
  fan-out to other Subscribers' Commercial rows happens in the web app.

**Optional later: track the owner-of-record file explicitly.** Add a
`Commercial.IsFileOwner BIT` so the web app knows which Commercial row is
"the one whose TapeID is the actual filename in the data tree." Not strictly
necessary if the web app stores the filename on the row, but makes withdrawal
of the file-owner cleaner (you'd transfer ownership to another active row
sharing the FingerprintID before removing the file).

---

## 5) The library-manager resolver / filename uniqueness — answer

Your concern is real but slightly different in Option C than in A.

### Under Option A (if you went that way)
Two Subscribers registering `NCHK_030.mp3` independently would collide in
`data\<station>\generic\NCHK_030.mp3`. **Prefix the filename with SubscriberID
in staging is the right fix** — `<SubscriberID>_NCHK_030.mp3`. The resolver
unchanged: it matches by `CommercialName = '<SubscriberID>_NCHK_030'`. No
ambiguity, no resolver change. **I'm fine with this prefix scheme.**

### Under Option C (recommended)
Only ONE file per (station, FingerprintID) lands in `data\`. The filename
becomes the file-owner row's TapeID. Other Subscribers' `Commercial` rows
don't have files of their own — they share via FingerprintID. So **filename
collisions can't happen by definition**; only one file per content per station.

Either way, the SubscriberID prefix is a cheap safety net — if you adopt it
even under C, it makes operator inspection of `data\` trivially obvious
("whose file is this?"). Up to you. I have no objection.

### One resolver change either way
With multiple `Commercial` rows possible per file (under C) or per TapeID
collisions (under A's mitigation), the resolver
`SELECT 1 FROM Commercial WHERE CommercialName=? AND Status='active'`
remains correct: it just checks "does any active row reference this name?"
But if you want me to ALSO refuse fingerprinting when ALL rows pointing at
a content-hash are withdrawn (Option C nuance), I'd modify it to query via
FingerprintID — easy when you're ready.

---

## 6) What I need from you to commit

1. **Confirm Option C is the direction.** I think it is — the alternative is
   accepting linear waste with no real benefit. If A is genuinely fine for
   your near-term scale and you want to defer C, I'll support A with the
   SubscriberID-prefix mitigation; just be aware it's a one-way door insofar
   as historical detections under A won't dedupe retroactively if you later
   migrate.
2. **Confirm the web app is willing to take the fingerprinting step at
   registration.** This is the real cost of C — the web app gains an audio
   processing dependency. If that's a deal-breaker, we fall back to A
   with the prefix.
3. **Confirm SubscriberID-prefixed filenames in staging** (either way). I'm
   fine with it; the puller and library-manager don't care about filename
   structure, only that names are unique and resolve via `CommercialName`.

---

## 7) Net implications

| Path | Web app work | Pipeline work | Cost at scale |
|---|---|---|---|
| **A (with prefix)** | small (prefix in stage path) | none | linear in Subscribers per ad |
| **B (fingerprint identity for fan-out)** | medium (new field + dedup view) | none | constant |
| **C (recommended)** | larger (fingerprint at registration, FingerprintID field, ownership logic) | small (optional: store content hash on library entries; resolver tweak) | constant; only meaningful path forward at production scale |

We're at a clean state right now (post-reset). This is the right moment to
pick the architecture, because choosing A now and migrating to C later means
back-filling FingerprintIDs across all historical `Commercial` rows and
deduplicating retroactively. Picking C now is materially cheaper than picking
C later.

Confirm 6.1, 6.2, 6.3 and we proceed. My strong vote is C.
