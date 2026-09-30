# Briefing for the Pipeline Thread — Commercial Registration Contract

**From:** the web-app thread
**Purpose:** agree the contract for how a commercial registered through the web
app reaches the pipeline for detection. This replaces the *simulation* approach
(pipeline derived the station from the filename prefix) with a production
approach (web app records station assignment in the DB; pipeline reads it).

This is the item flagged as "6(1)" in the web-app's commercial-registration
design — explicitly called out as differing from the testing logic. Please read
and confirm/correct the points in §4, then we build both sides to match.

This doc is self-contained — no other web-app context needed.

---

## 1. What the web app will do (new)

A user registers a campaign + commercial through a web form. On submit, the web
app will:
1. Create/locate the Campaign, create the Commercial record, and record which
   **station(s)** the commercial is assigned to (in the database).
2. Receive the uploaded file:
   - **Generic**: an audio commercial (~30s). Convert to mp3 if needed.
   - **Liveread**: a text script for a DJ to read.
3. Place the file where the pipeline expects to find it for processing.

Steps 1–2 are squarely web-app work. **Step 3, and how the pipeline learns
about the new commercial, is the contract we need to agree.**

## 2. What changes from the testing/simulation setup

- **Testing:** the pipeline read the characters before the first underscore in
  the filename (e.g. `5FM-CHECKERS`) to decide which station folder the file
  belonged to. This was a simulation shortcut.
- **Production:** the commercial's station assignment lives in the **database**
  (written by the web app at registration). The pipeline should **query the DB**
  for which station(s) a commercial is registered to, and derive folder paths
  from that — NOT from the filename. Filenames become client-defined Tape IDs
  with no meaning to the pipeline (e.g. `NCHK_030_1735_E`, `DIST_30_202_Z`).

## 3. What the web app proposes to provide

### 3a. Database: station assignment
The web app owns these tables (already exist or being added):
- `Commercial` (CommercialID, CommercialName/TapeID, Brand, CommercialType
  ['generic'|'liveread'], ...)
- `Campaign`, `CampaignCommercial` (campaign↔commercial many-to-many)
- `CampaignStation` (campaign↔station: which stations a campaign runs on)

**Question:** is "which stations a commercial is assigned to" best read by the
pipeline from `CampaignStation` (via the campaign), or do you want a direct
`CommercialStation` assignment the pipeline can query without joining through
campaigns? The web app can provide either. (A direct commercial→station view may
be simpler for the pipeline; tell us what's easiest to query.)

### 3b. File placement
The web app will convert generic audio to mp3 and place it at a UNC path for the
pipeline. The design doc references:
```
\\AUDIOPROC\RadioMonitor\audio\generic\00-00-00\...
```
**Questions:**
- What is `00-00-00` — a date (registration date? air date?), a placeholder, or
  something else? What's the exact directory convention you want?
- Different root for `liveread` (text) vs `generic` (audio)? e.g.
  `...\audio\liveread\...` vs `...\audio\generic\...`?
- Naming of the placed file — keep the client's Tape ID as the filename? Any
  characters the pipeline cannot tolerate in a filename (so the web app can
  sanitise/validate at upload)?

## 4. The handshake — how the pipeline learns of a new commercial

This is the core unknown. When the web app registers a commercial and drops its
file, how should the pipeline find out?
- **Option A — pipeline polls the filesystem** (watches the UNC folder for new
  files), then looks up the DB for station assignment.
- **Option B — pipeline polls the DB** (e.g. a "pending" flag / status column on
  Commercial) and pulls files it hasn't processed.
- **Option C — the web app signals the pipeline** (a queue, a trigger, an API
  call).
- Something else you already do?

Tied to this: your established **midnight rebuild cadence** for the fingerprint
library — does a newly registered commercial become detectable at the next
midnight rebuild (consistent with how samples currently enter the library), or
do you need it picked up sooner? The web app assumes the midnight cadence unless
you say otherwise.

## 5. What we need back

1. **§3a:** should the pipeline read station assignment from `CampaignStation`
   (via campaign), or do you want a direct `CommercialStation` table? Which is
   easiest for you to query?
2. **§3b:** the exact UNC directory convention (what `00-00-00` is), whether
   generic/liveread split by root, filename rules, and forbidden characters.
3. **§4:** the handshake — how the pipeline discovers a newly registered
   commercial, and whether the midnight cadence governs when it becomes
   detectable.
4. Anything the web app must write (a status column, a "ready" flag, a
   registration timestamp) for your processing to work.

## 6. Why this blocks the build

The web-app registration form is mostly ready to build, but the **file
placement and station-assignment contract** must match what the pipeline will
actually read and watch. We don't want to invent a UNC convention or a handshake
the pipeline doesn't implement — so we're settling this contract before building
the registration flow end-to-end.

Please report on §5 and we'll build both sides to match.
