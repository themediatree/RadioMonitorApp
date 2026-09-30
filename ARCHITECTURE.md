# RadioMonitor — Architecture & Pipeline Contract

This document records the agreed contract between the **pipeline** (on AUDIOPROC)
and the **web app** (on AUDIOREC). It is the source of truth for how the two
systems interact. Update it whenever that contract changes.

---

## Machines

| | AUDIOPROC | AUDIOREC |
|---|---|---|
| Role | Pipeline (record/transcribe/detect), 24/7 | DB + file storage + web app |
| OS | — | Windows Server 2025 |
| DB | (client) | SQL Server 2025 Express, instance `AUDIOREC\SQLEXPRESS` |
| Media root | `C:\RadioMonitor\` (7-day local cache) | `D:\RadioMonitor\` (permanent) |
| Web app | — | `D:\RadioMonitorApp\` |

**DB connection:** SQL login `radiomonitor_user`, ODBC Driver 18,
`Encrypt=yes`, `TrustServerCertificate=yes`. The web app connects to
`localhost\SQLEXPRESS` (same box). The pipeline connects to
`AUDIOREC\SQLEXPRESS` (over the network).

---

## Storage contract (critical)

1. The pipeline writes **all** media output to AUDIOREC over a UNC share
   (`\\AUDIOREC\RadioMonitor\...`).
2. **Atomicity guarantee:** a file is fully written and verified on disk at its
   final `D:\` location *before* the corresponding DB row is committed.
3. The DB stores **full `D:\` paths** (e.g. `D:\RadioMonitor\clips\...`).
4. The web app runs on AUDIOREC and reads those `D:\` paths **directly** —
   no translation, no cross-network read, no "file not there yet" race.

Consequence for the web app: any row it can query points to a file that
exists locally. Clip serving (Phase 4) is a plain local file read (with a
path-traversal guard to ensure the resolved path stays under the configured
media root).

---

## Tables the web app READS (owned by the pipeline)

- **Detection** — commercial detections (fingerprint + transcript-extractor).
  Has `ClipPath` (`D:\`) → playable clip.
- **SongDetection** — has `ClipPath` (`D:\`).
- **WordDetection** — has `ClipPath` (`D:\`).
- **RecordingChunk** — `AudioPath` (`D:\`) → 60s source chunk.
- **Transcript** — `JsonPath`, `TextPath` (`D:\`).
- **ChunkStatus** — per-chunk processing state (5 stage timestamps +
  2 movement timestamps). Usable for pipeline-health / monitoring views.
- **Station**, **Commercial** — reference data.

The web app must treat all of the above as **read-only**. It never writes
pipeline tables from a request handler.

---

## Tables the web app OWNS

The Phase 1 migration created these (User, Client, Agency, AgencyClient,
Campaign, ClientSubscription, SubscriptionPlanConfig, UserInvitation,
AuditLog, ImpersonationSession, etc.). The web app reads and writes them.

### Coming in Phase 6 — `CommercialSample` (NOT YET CREATED)

The control plane for client-uploaded samples. Proposed columns:

| Column | Notes |
|---|---|
| SampleID | PK |
| CampaignID | FK → Campaign |
| CommercialName | display name |
| Category | `generic` or `liveread` |
| AudioPath | full `D:\` path under `D:\RadioMonitor\data\<station>\{generic,liveread}\` |
| Status | `pending` / `active` / `withdrawn` / `failed` |
| UploadedByUserID | FK → User |
| UploadedAt | timestamp |
| FingerprintedAt | set by the pipeline's library-manager once it fingerprints |

To be created via Alembic in Phase 6, designed alongside the upload UI.

---

## Sample-management contract (Phase 6)

1. A client uploads a generic commercial or live-read script via the web app.
2. The web app validates the file, saves the audio under
   `D:\RadioMonitor\data\<station>\generic\` or
   `D:\RadioMonitor\data\<station>\liveread\` (per-station, by category),
   and inserts a `CommercialSample` row with `Status = pending`.
3. **The web app does NOT compute fingerprints.** No audio libraries in the
   web tier.
4. A pipeline-side **library-manager** module reads `CommercialSample`,
   computes fingerprints for `pending` samples, sets `FingerprintedAt`, and
   rebuilds the matcher's fingerprint library.
5. Withdrawing/cancelling a campaign sets the relevant rows to `withdrawn`;
   the library-manager drops them from the library on its next rebuild.
   (Archive, don't hard-delete — preserve the evidence chain for historical
   detections.)

### The midnight rule (hard business constraint)

Any campaign/commercial change — add, cancel, withdraw — **takes effect at
midnight, never immediately.** The library-manager rebuilds once nightly
(~00:00) and the matching modules reload once nightly. No mid-day hot reload,
no per-loop polling.

**The web app must surface this clearly in the UI** — e.g. "Changes take
effect at midnight" next to any campaign/sample edit control. A sample
uploaded at 14:00 goes live at the next 00:00.

---

## Validated pipeline status (as of handoff)

Full 5-station / 775-chunk run, no accuracy regression
(26/30 live-reads, 126/134 songs). All paths `D:\`, all files on AUDIOREC,
ChunkStatus clean. Storage architecture complete and tested.
