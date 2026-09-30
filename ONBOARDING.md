# ONBOARDING — RadioMonitor Web App

**Read this first.** This is the single entry point for picking up the project.
It tells you the lay of the land, the current state, the lessons banked, and
exactly which other docs to read in what order to be productive. Budget 10-15
minutes; the project has 10+ sessions of accumulated decisions, but they're all
written down — you don't have to rediscover them.

---

## 1. What RadioMonitor is, in one paragraph

RadioMonitor is a SaaS proof-of-broadcast platform for radio commercials. A
**pipeline** (running on AUDIOPROC, owned by a separate "pipeline thread")
records radio streams, fingerprints audio, and detects when registered
commercials air. A **web app** (this codebase, running on AUDIOREC) lets paying
**Subscribers** register commercials to be detected, then see proof-of-broadcast
for those airings (audio clip + metadata). Multiple Subscribers can pay
independently for proof of the same airing. The pipeline detects once; the
web app fans out visibility to all paying Subscribers.

## 2. The two threads — who owns what

This project has been built across two parallel "threads" (Claude conversations):

- **Web-app thread** (this thread is its successor): owns this codebase, the
  database schema for Subscribers/Campaigns/Commercials/Users, the registration
  UI, the detection-viewing UI, scoping/authorization, audit, invitations.
- **Pipeline thread** (separate): owns the audio pipeline on AUDIOPROC —
  recording, fingerprinting, the `Detection`/`Recording*`/`Transcript` tables,
  the `data\` tree and `.pkl` library, the puller (staging→data mirror copy),
  the nightly library-manager rebuild.

Cross-thread decisions are settled by **written contracts** (the `PIPELINE_*`
docs in this repo) — never by guessing or assuming. If you're about to assume
something about what the pipeline does, stop and check the docs; if it's not
there, write a briefing and ask.

## 3. Current state — read this carefully

### Where the project is RIGHT NOW
- The system was **fully reset to a clean baseline** (both servers) in the last
  session. Coordinated procedure: pipeline cleared detections/recordings/data
  tree first; web app cleared tenants/campaigns/commercials/staging second.
- The user **then registered some tenants** under the OLD model (6 clients +
  invited Oracle Sun agency user) to test the UI.
- They hit a confusing dropdown bug, paused, and we realised the **entity model
  was wrong**. After careful discussion and a pipeline-thread consultation, the
  decisions below were made. **No code has been written yet against these
  decisions.**

### The decisions waiting for implementation (this is what you'll build)
1. **Flat Subscriber model** — single `Subscriber` table replacing
   `Client`/`Agency`/`StationAccount`. Seven types: `Agent`, `Advertiser`,
   `Brand Owner`, `Radio Station`, `Government`, `Political Party`, `Other`
   (with a description field). User has one `SubscriberID` FK. No more
   "agency manages client" relationship.
2. **Option C dedup** (FingerprintID) — the web app fingerprints uploaded
   audio at registration, computes a content-derived `FingerprintID`, dedups
   against existing active Commercial rows. Multiple Subscribers registering
   the same audio share a `FingerprintID`; only ONE file lands in staging
   (the "file-owner row"); pipeline produces one Detection per airing; web
   app fans out visibility to all Subscribers sharing the FingerprintID.
3. **Per-Subscriber TapeID uniqueness** with `<SubscriberID>_<TapeID>.<ext>`
   filename prefix in staging.
4. **T&C confirmation** at registration — checkbox affirming lawful
   ownership/authority; logged to AuditLog with version + timestamp + user.
5. **"Advertiser" in UI, `ClientID`/`Subscriber` in code** — labels are a
   UX concern; the new code-level concept is `Subscriber`.

### What's been done and works (don't redo this)
- Authentication (login/logout/session cookie/JWT), invitation flow,
  password policy, audit log, the `XACT_ABORT`/`QUOTED_IDENTIFIER` migration
  discipline, ffmpeg-based mp3 conversion on AUDIOREC, the staging mirror
  fan-out with write-then-rename atomicity, the `pending→active` activation
  job, the detection-viewing screens (list + detail + in-browser clip player
  with path-traversal protection).
- The pipeline thread has built and confirmed: the staging puller
  (mirror-copy-then-clear, skips `*.tmp`) and the nightly library-manager
  (DB-driven `Status='active'` filter). Both pending their final deploy on
  AUDIOPROC.

### What needs replacing (don't extend; replace)
The current code has `Agency`/`Client`/`StationAccount` as separate tables,
`AgencyClient` as a link table, three nullable FKs on `User`, three sets of
near-identical admin CRUD screens, and scoping logic that infers
"manages clients" from campaign membership. **All of that is wrong under the
new model.** The refactor replaces it; see §6.

## 4. The lessons banked — please respect these

These are hard-won. Each one came from a real failure earlier in the project.

- **Sandbox is Python 3.12 + SQLite. Server is Python 3.14 + SQL Server.**
  Tests passing in the sandbox is necessary but NOT sufficient. Things that
  pass on SQLite and fail on SQL Server: `IS 1` for boolean filters (use
  `== True` which renders `= 1`), implicit type coercions, autoincrement
  on `BIGINT` PKs. Always compile boolean filters against the SQL Server
  dialect in tests as a safety net.
- **Every `sqlcmd` script starts with `SET QUOTED_IDENTIFIER ON; SET
  XACT_ABORT ON;`.** Without QUOTED_IDENTIFIER, deletes against tables with
  filtered indexes or computed columns fail. Without XACT_ABORT, a failed
  multi-statement script leaves a half-done mess.
- **Trust the live database over your memory of dumps.** I have been wrong
  about Station counts (16 vs the real 6), `Commercial.Brand` defaults
  (NULL not empty), the existence of test rows, etc. When the user pastes
  current counts that conflict with what I think, the user is right.
- **Never assume cross-thread contracts.** When something spans the
  web-app/pipeline boundary, write a `PIPELINE_*_BRIEFING.md`, hand it over,
  wait for a written response, settle it. Three full cycles of this in the
  project; all three were essential.
- **Commit DB rows BEFORE the file lands.** The pipeline library-manager
  resolves staged files to their `Commercial` row by name. Files must never
  appear before their row is committed. The registration route follows this
  ordering rule explicitly.
- **Write-then-rename for any file the pipeline reads.** Staging writes use
  `.tmp` → `os.replace`. The pipeline's puller skips `*.tmp`. No half-written
  files ever visible.
- **Deploy verification is non-negotiable.** After every zip deploy, run
  `Select-String` against the changed file to confirm the new code landed.
  Stale-file deploys have wasted hours.

## 5. The system — concrete facts you'll need

- **AUDIOPROC**: pipeline (Python 3.11). Owns `C:\RadioMonitor\` tree.
- **AUDIOREC**: Windows Server 2025, SQL Server 2025 Express
  (`AUDIOREC\SQLEXPRESS`), Python 3.14 64-bit, web app at
  `D:\RadioMonitorApp\`. Media root `D:\RadioMonitor\`. ffmpeg at
  `C:\ffmpeg\bin\ffmpeg.exe` (on PATH).
- **DB**: `RadioMonitor`. Login `radiomonitor_user` / `radad123` (weak,
  flagged for production hardening). ODBC Driver 18, `Encrypt=yes`,
  `TrustServerCertificate=yes`.
- **Real station roster** (StationID → StationName, all `IsActive=1`):
  4→5FM, 5→947, 6→generic, 7→HOT1027, 8→JakarandaFM, 9→kfm.
  Note: `generic` (id 6) is a pipeline catch-all, not a real broadcaster —
  exclude it from station pickers in registration UI.
- **Subscription plans** (`SubscriptionPlanConfig.PlanCode`, 4 rows kept
  through the reset): standard plan-tier set. Plans carry the feature flags
  `AllowSpectrumAnalysis` / `AllowTranscription` / `AllowSongDetection` /
  `AllowWordDetection`.
- **Deploy workflow** (reliable, after early stale-file pain):
  ```
  Remove-Item "$HOME\Downloads\rm_phase1b" -Recurse -Force -ErrorAction SilentlyContinue
  Expand-Archive -Path "$HOME\Downloads\RadioMonitorApp_phase1.zip" -DestinationPath "$HOME\Downloads\rm_phase1b" -Force
  $src = (Get-ChildItem "$HOME\Downloads\rm_phase1b" -Recurse -Filter "main.py" | Select-Object -First 1).DirectoryName
  Copy-Item "$src\*" "D:\RadioMonitorApp\" -Recurse -Force
  ```
  `.env`/`.venv` are not in the zip and survive the copy. Always verify a
  changed file with `Select-String` afterwards.

## 6. The refactor — phases, in order

This is what you'll build. Each phase is one focused work session.

### Phase R1 — Schema migration (clean-slate, NOT data-migrate)
- Migration `007`:
    - New `Subscriber` table (seven types + Other description, optional
      `StationID` FK for Radio Station type).
    - On `Commercial`: add `SubscriberID INT NOT NULL FK`,
      `DisplayTapeID NVARCHAR(100) NOT NULL`,
      `FingerprintID NVARCHAR(64) NULL`. `CommercialName` keeps its
      column type but its CONTENT becomes the prefixed form
      `<SubscriberID>_<DisplayTapeID>` (the staged filename stem).
      **No `IsFileOwner` column** — under decision (ii) the on-disk
      filename is the implicit owner; see
      `FINGERPRINT_IDENTITY_DESIGN.md` §5.
    - New constraints: `UQ_Commercial_Subscriber_TapeID
      UNIQUE(SubscriberID, DisplayTapeID)`,
      `IX_Commercial_FingerprintID`.
    - On `User`: add `SubscriberID INT NULL FK`. New
      `CK_User_Ownership` ("Internal AND SubscriberID NULL, OR
      external AND SubscriberID NOT NULL"). Reduce `UserType` to three
      values: `internal`, `subscriber_admin`, `subscriber_user`. Update
      `CK_User_Type`.
    - On `Campaign`: drop `ClientID`/`AgencyID` columns; add
      `SubscriberID INT NOT NULL FK`.
- **Clean-slate data:** truncate all transactional tables (Campaign,
  Commercial, CampaignCommercial, CampaignStation, AuditLog,
  UserInvitation, ClientSubscription, ApiKey, and all User rows EXCEPT
  the internal admin). Drop `AgencyClient`, then drop
  `Client`/`Agency`/`StationAccount` tables. Preserved reference data:
  `Station` (6 rows), `SubscriptionPlanConfig` (4 rows).
- Internal admin: keep the existing internal admin row (compatible with
  the new constraint as-is); just ensure `SubscriberID` is NULL.
- Apply, verify with `INFORMATION_SCHEMA` dump, paste verification
  output.
- Note for R2: withdrawal of a file-owner with active siblings requires
  the web app to restage a sibling's file (Option α, see
  `FINGERPRINT_IDENTITY_DESIGN.md` §5). This requires an audio archive
  directory the web app maintains: `D:\RadioMonitor\audio_archive\
  <FingerprintID>.mp3`. R1 doesn't need to create the directory (R2's
  registration code does), but the design rests on this.

### Phase R2 — Models + service layer
- New `app/models/subscriber.py`. Update `User`/`Campaign`/`Commercial`.
- Update scoping (`app/services/scoping.py`) — new visibility query joins
  `Detection → Commercial → FingerprintID → siblings → my Subscriber`.
- Update `registration_service.py` to fingerprint uploads, compute
  `FingerprintID`, dedup, decide file-owner.
- Add fingerprint helper (`app/utils/fingerprint.py`) wrapping pipeline's
  `fingerprint_core.py`. Coordinate import location with pipeline thread.
- Withdrawal: file-owner transfer logic.
- Rewrite tests to match the new model. Scenario 3 collapses; new dedup +
  ownership-transfer tests added.

### Phase R3 — Routes + templates
- Single set of `/admin/subscribers` CRUD screens replacing the three old
  ones. Type dropdown with seven values. Description field shown for Other.
- Registration form: pick Subscriber (internal only; non-internal implicit);
  T&C checkbox with versioned text recorded in AuditLog; fingerprint dedup
  message ("this audio is already registered — your record is being added
  alongside" or similar).
- "Advertiser" labels in templates wherever the UI used to say "Client".
- Detection-viewing already works against the scoping engine; verify it
  still does under the new model.

### Phase R4 — Suite green + deploy + live smoke test
- Full pytest green including new dialect-safety tests for the new joins.
- Deploy migration `007`, deploy zip, verify with `Select-String`.
- Live smoke: create a Subscriber, invite a user, register a commercial,
  register the SAME audio under a different Subscriber, confirm one file in
  staging, two Commercial rows sharing FingerprintID, file-owner flag set
  correctly.
- Tell the pipeline thread we've landed; they finalise their puller +
  library-manager deploy on AUDIOPROC; first real end-to-end run.

## 7. Read these next, in this order

1. **`ENTITY_MODEL.md`** — the new Subscriber model in detail. The "what" of
   the refactor.
2. **`FINGERPRINT_IDENTITY_DESIGN.md`** — Option C: FingerprintID flow,
   file-owner semantics, dedup at registration, withdrawal-with-transfer.
   The "how" of the refactor's hardest part.
3. **`AUTHORIZATION.md`** (v0.3 after the refactor docs land) — the
   authorization story end-to-end.
4. **`COMMERCIAL_REGISTRATION_CONTRACT.md`** — the file-handling contract
   with the pipeline (staging path, write-then-rename, ordering). Already
   settled; don't change without coordinating.
5. **`PIPELINE_MULTI_SUBSCRIBER_RESPONSE.md`** (the pipeline's own response
   document, uploaded into the conversation) — gives you the pipeline-side
   reasoning behind Option C and the FingerprintID design. Worth reading
   for context on *why* C was chosen over A.
6. **`PIPELINE_SYNC_STATUS.md`** + **`PIPELINE_REGISTRATION_ADDENDUM.md`** —
   the prior settled contracts on staging/puller/library-manager.
7. **`DEFERRED.md`** — items deliberately postponed; nothing to do, just
   be aware of them.
8. **`TESTING_GUIDE.md`** — what's testable now, what isn't built yet.
   Useful for shaping your "verification" mindset.

The code itself (`app/models/`, `app/services/`, `app/routes/`, `tests/`) is
the final reference, but reading those docs in order first means it reads as
"the model I expect" rather than "what's this?".

## 8. How to start the new session

After uploading this onboarding doc + the latest zip:

1. Read this doc fully.
2. Read §7's referenced docs.
3. Skim `app/models/`, `app/services/scoping.py`, `app/services/
   registration_service.py`, `app/routes/registration.py`,
   `app/routes/detections.py` to ground the docs in code.
4. Confirm understanding with the user — *briefly* — including: "I see
   we're about to do the Subscriber refactor in phases R1-R4. Should I start
   with R1 (the migration), or is there context I'm missing?"
5. Build R1 first. Migration. Single session.

Do not start writing code before doing 1-3. The temptation will be strong;
resist it. This project has paid for assumed-context mistakes more than once.

## 9. The user

The user is the project owner. They've been excellent at:
- Pausing when something feels off (the validation question, the entity
  model realignment — both saved us substantial wasted work).
- Coordinating with the pipeline thread cleanly.
- Asking "what do I share with the pipeline thread?" before each cross-
  boundary decision.

They will not always know technical details; ask clear questions rather than
guessing. They prefer brief answers, no padding, no salesman tone. They value
honesty about uncertainty over confident-sounding-but-wrong.

They are based in South Africa (Johannesburg). Some stations are SA stations.
Don't be confused if they reference dates in DD/MM format.

---

That's the map. Welcome to the project — there's substantial work ahead, but
it's well-documented work, and the previous sessions have laid careful
foundations. Take it phase by phase.
