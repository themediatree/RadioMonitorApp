# Commercial Registration — File Handling Contract (SETTLED)

Authoritative record of the web-app ↔ pipeline contract for getting a
registered commercial to the pipeline. Verified against pipeline code (their
return briefing) and reconciled with the web-app side's decisions.

## The model (Model 2: mirror-staging + dumb puller)

Because AUDIOPROC cannot accept inbound writes (decided §5.1), the web app does
NOT write directly into the pipeline's `C:\RadioMonitor\data` tree. Instead:

1. **Web app fans out into a staging tree on AUDIOREC** that MIRRORS the target
   layout:
   ```
   <STAGING_ROOT>\<StationName>\<generic|liveread>\<TapeID>.<ext>
   ```
   One copy per assigned station (resolved via
   Commercial → CampaignCommercial → Campaign → CampaignStation → Station.StationName).

2. **Pipeline-side puller (future pipeline work)** does a dumb, stateless
   mirror-copy of the staging tree into the real data tree, then clears what it
   copied:
   ```
   <STAGING_ROOT>\<station>\<category>\X   →   C:\RadioMonitor\data\<station>\<category>\X
   ```
   The puller needs NO knowledge of stations, manifests, or the DB — it mirrors
   directories. Runs before (or as part of) the nightly library rebuild.

3. **Nightly library-manager (future pipeline work)** rebuilds the fingerprint
   library + reloads script cache from the data tree at 00:00. Detectability
   begins next midnight.

## Hard rules (from the pipeline, non-negotiable)

- **Folder name == `Station.StationName` verbatim** (`5FM`, `947`, `HOT1027`,
  `JakarandaFM`, `kfm`). Mismatch = silently undetected. Web app sources the
  station picker straight from the Station table; no free-typing.
- **Category split:** `generic` (audio, fingerprinted) vs `liveread` (text,
  transcript-matched). Exactly those two folder names.
- **Generic** = `.mp3` (convert on upload). **Liveread** = `.txt` (never audio).
- **TapeID** (filename stem) = the commercial's identity. Must be unique within
  a station+category. Sanitize: reject/replace `\ / : * ? " < > |`, leading/
  trailing spaces and dots. Prefer ASCII, no spaces. Web app sets the extension
  (.mp3/.txt), never trusts the client's original.

## Withdrawal (decided §5.4: status column) — FINAL

Withdrawal does NOT delete the file. `Commercial.Status` is the source of truth
(Option B-2): the pipeline's library-manager reads it and includes only
`Status = 'active'` in the nightly rebuild. Withdrawal = `UPDATE Commercial SET
Status='withdrawn'`. No file deletion, no cross-machine choreography.

### Commercial.Status — three values
`NVARCHAR(20) NOT NULL DEFAULT 'pending'`, allowed: `active` | `pending` | `withdrawn`.
- **pending**  = registered, file staged, but NOT yet live. A commercial stays
  pending until its campaign's StartDate arrives.
- **active**   = live; included in the nightly fingerprint/script rebuild.
- **withdrawn**= retired; excluded from the rebuild (file retained for audit).

Pipeline library-manager filter (canonical, documented both sides):
```sql
SELECT 1 FROM Commercial WHERE CommercialName = ? AND Status = 'active'
```
So both `pending` and `withdrawn` are excluded until flipped to `active`.
"No matching Commercial row for a file" => the manager SKIPS it (anomaly, logged).

### REQUIRED web-app scheduled job (consequence of choosing pending)
A nightly task must flip `pending` -> `active` for commercials whose campaign
StartDate has arrived (StartDate <= today). Without it, pending commercials
never go live. This is the cost of the three-value model and must be built.
Order it to run BEFORE the pipeline's midnight library rebuild so newly-active
commercials are picked up the same night.

## Atomic staging writes (pipeline §A tightening) — REQUIRED

The web app MUST write staging files write-then-rename to avoid the puller
copying a half-written file:
```
write  -> <staging>\<station>\<category>\<TapeID>.<ext>.tmp
fsync/close, then atomic rename ->
          <staging>\<station>\<category>\<TapeID>.<ext>
```
Rename within the same NTFS volume is atomic. The puller ignores `*.tmp`.
(The puller mirrors into the data tree the same way and clears staging after a
verified copy — pipeline's half, not ours.)

## Ordering rule (pipeline §B.2 edge)

The `Commercial` row (with `CommercialName = <TapeID stem>`) MUST be committed
to the DB BEFORE or AT the time the file lands (renamed into place) in staging,
so the library-manager can resolve the file to its row by name. Practically:
write DB rows first (commit), THEN write+rename the staging file.

## What the web app writes to the DB

- `Commercial` (CommercialName=TapeID, Brand, CommercialType, status, CreatedAt)
- `Campaign` (create if new; locate if the campaign id/name already exists)
- `CampaignCommercial` (link this commercial to the campaign)
- `CampaignStation` (which stations the campaign runs on) — source of truth for
  fan-out.

## "Active from next midnight" (pipeline §3c)

Detectability is midnight-gated. A commercial registered at 14:00 Tue is not
detected until 00:00 Wed onward. The web-app UI must set this expectation,
tied to campaign StartDate semantics ("active from tomorrow").

## Still owed to the pipeline thread (small addendum)

Confirm with the pipeline thread: the exact `<STAGING_ROOT>` path on AUDIOREC
that their puller will read (e.g. `D:\RadioMonitor\staging` exposed as
`\\AUDIOREC\RadioMonitor\staging`), and that the puller mirrors then clears.
Non-blocking for building the web-app form (the staging root is config).

## Web-app build scope (this phase)

- Registration form (campaign + commercial + type + upload + TapeID + brand +
  stations), with subscription/authorization checks.
- New-or-append campaign logic (a campaign can hold many commercials).
- File handling: validate, convert generic→mp3, sanitize TapeID, fan out into
  the staging mirror tree (one copy per station).
- Status field on Commercial for withdrawal.
- "Active from next midnight" messaging.
NOT this phase: the pipeline puller and library-manager (pipeline side).
