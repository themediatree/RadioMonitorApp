# Briefing for the Pipeline Thread — Multi-Subscriber Commercial Detections

**From:** the web-app thread
**Purpose:** the web app is refactoring to a flat "Subscriber" model (more
detail below if useful, but largely web-app-internal). One question genuinely
needs your answer because it determines the contract between us. Short read.

## The new web-app reality (brief)

RadioMonitor has multiple **Subscriber** entities — Agents, Advertisers, Brand
owners, Radio Stations, Government, Political Parties — each paying RadioMonitor
independently for proof-of-broadcast. **Multiple Subscribers can register the
same physical commercial** (the same audio fingerprint), and **each pays
separately** for detection. Per the user: "if 4 entities registered for the same
commercial's detection, the pipeline obviously only detects it once, but ALL 4
entities will be billed for that detection + extraction."

Concretely: 4 separate `Commercial` rows could exist for the same audio, one per
Subscriber, each with its own client-chosen `CommercialName` (TapeID) — because
each Subscriber uses its own naming convention.

## The question

When 4 Subscribers each register the same audio (and the web app drops 4
separately-named mp3s into the staging mirror tree, fanning out to whichever
stations each Subscriber selected), what does the pipeline do?

### Option A — N detections per airing (one per Commercial row)
The pipeline fingerprints each `<TapeID>.mp3` independently, treats them as
4 distinct library entries, and produces 4 `Detection` rows when the audio airs
(one per `CommercialID`). Each Subscriber's "Detection.CommercialID = a
Commercial row I own" query naturally returns their own detection.
- **Pro:** trivially clean for the web app's "I see what I registered" filter.
- **Pro:** matches your existing per-station fingerprinting model (one fingerprint
  per file in the data tree).
- **Con:** N× CPU on the pipeline for the same audio when many Subscribers
  register it.
- **Con:** N× clips/extractions on disk per airing — potentially wasteful.

### Option B — 1 detection per airing (one Commercial row "wins")
The pipeline matches the airing to one of the N fingerprints (whichever wins),
writes one `Detection` row, and the web app then has to find ALL Subscribers
whose `Commercial` row maps to that fingerprint and surface the detection to
each of them.
- **Pro:** efficient on pipeline CPU and disk.
- **Con:** the web app has to compute "which other Commercial rows share this
  fingerprint with mine" — non-trivial without an explicit fingerprint identity
  field shared across the N rows.

### Option C — 1 detection per airing, web app pre-deduplicates
The web app detects that audio X is already registered by another Subscriber,
points the new Subscriber's `Commercial` row at the existing fingerprint via a
shared `FingerprintID` (or just stages only one copy and writes N `Commercial`
rows pointing to it), and the pipeline produces one detection that the web app
fans out to all relevant Subscribers via the shared FingerprintID.
- **Pro:** efficient pipeline-side AND clean web-app logic.
- **Con:** requires audio-content equivalence detection at registration time
  (hash/fingerprint comparison) and a new shared-identity field.

## What we need from you

1. **Which model is closest to what your existing pipeline does?** I suspect
   Option A given the per-station fingerprinting and the per-file fingerprint
   library entries, but I don't want to assume.
2. **Is there a per-airing detection rate concern at production scale?** I.e.
   if 10 Subscribers register the same political-party ad in election season
   and it airs 100 times across 5 stations, that's 5,000 Detection rows for
   one ad. Manageable, or a problem?
3. **Library-manager resolver — small but real implication of Q2 answer:**
   the resolver currently does
   `SELECT 1 FROM Commercial WHERE CommercialName=? AND Status='active'`.
   With per-Subscriber TapeID uniqueness (the new web-app decision), two
   Subscribers could legitimately register `NCHK_030.mp3`. Today those would
   collide as duplicate filenames in the same `data\<station>\generic\` folder,
   so we can't even reach the resolver. Two ways out:
   - **prefix the filename with SubscriberID** in staging
     (`<sub>_NCHK_030.mp3`), or
   - keep TapeID-only filenames and have the resolver tolerate multiple matches
     (return all active rows with that name).

   Filename prefix is cleaner — no per-Subscriber ambiguity at the file layer.
   Tell us if either way is a problem for you.

## What we'll do with your answer

- **A:** smallest change. Web app writes N copies to staging (already does for
  multi-station fan-out); the per-Subscriber TapeID + filename-prefix avoids
  filename collisions in `data\<station>\generic\`.
- **B:** non-trivial — we'd add a "fingerprint identity" shared field on
  `Commercial` so the web app can fan one detection to N Subscribers.
- **C:** more web-app work (content-equivalence at registration), but cleanest
  long-term.

We'd prefer A if your pipeline isn't pushing back on the CPU/disk side. Confirm
which it is and whether the filename-prefix change is OK, and we'll proceed.
