# Status Sync — Web App → Pipeline Thread

**Purpose:** the web-app side of commercial registration is **built and verified
live on AUDIOREC**. This briefing consolidates everything the pipeline side now
owes against the contracts we've already settled, so you can build your half.
Nothing here is new policy — it's a checklist of what was agreed, plus proof the
web-app half is producing exactly what your code will consume.

---

## 1. What the web app now does (LIVE, verified)

A user registers a campaign + commercial through the web app. On submit, the web
app (confirmed working on AUDIOREC):

- Writes DB rows: `Campaign`, `Commercial` (Status='pending'), `CampaignCommercial`,
  `CampaignStation` — **committed before** any file is staged.
- Converts generic uploads to mp3 (ffmpeg at C:\ffmpeg\bin); keeps liveread as .txt.
- Fans out the file into the staging mirror tree, **one copy per station**, using
  **write-then-rename** (.tmp → atomic rename).

**Verified output from a live test:**
```
D:\RadioMonitor\staging\5FM\generic\SEF-PRP-093-E.mp3      (587 KB, converted)
D:\RadioMonitor\staging\HOT1027\liveread\SHP-BAR-LR_E.txt  (18 B, text kept)
```
No `.tmp` files left behind. Layout is exactly `staging\<StationName>\<category>\<TapeID>.<ext>`.
Commercials created with `Status='pending'`; existing 466 commercials remain `active`.

## 2. The settled contracts (recap — your side builds against these)

### 2a. Staging handoff (mirror-staging + dumb puller)
- Read root: `\\AUDIOREC\RadioMonitor\staging` (local `D:\RadioMonitor\staging`).
- Layout: `staging\<StationName>\<generic|liveread>\<TapeID>.<ext>`.
- StationName is **verbatim** `Station.StationName` (5FM, 947, HOT1027,
  JakarandaFM, kfm, ...). The folder name carries station + category — no
  manifest, no DB read for routing.
- **Puller (your build):** mirror-copy `staging\<station>\<category>\*`
  → `C:\RadioMonitor\data\<station>\<category>\`, **write-then-rename into data**,
  verify, then **clear the staging source it copied**. **Skip any `*.tmp` file**
  (that's a half-written staging file the web app hasn't renamed yet).

### 2b. Withdrawal + status (DB is source of truth — Option B-2)
- `Commercial.Status NVARCHAR(20) NOT NULL DEFAULT 'active'`,
  values `active | pending | withdrawn`. **Migration applied; verified live.**
- **Library-manager (your build):** the nightly rebuild resolves each file to its
  `Commercial` row by name and includes a reference **iff `Status='active'`**:
  ```sql
  SELECT 1 FROM Commercial WHERE CommercialName = ? AND Status = 'active'
  ```
  So `pending` and `withdrawn` are both excluded. "No matching row" => **skip**
  (log it; don't fingerprint an unregistered file).
- This means the rebuild now needs a DB read it didn't have before. That's the
  one new capability on your side (the loaders are pure FS scans today).

### 2c. Detectability cadence
- A commercial becomes detectable at the next **midnight** rebuild after it is
  both `active` AND its file is in the data tree. Consistent with the existing
  midnight library cadence.

## 3. What the pipeline side owes (the build checklist)

1. **Puller** (§2a): mirror-copy staging → data, write-then-rename, verify,
   clear source, skip `.tmp`. Brainless by design — no DB, no manifest.
2. **Library-manager DB read** (§2b): resolve each scanned file to its
   `Commercial` row by `CommercialName = <TapeID stem>`, include only
   `Status='active'`, skip unmatched (with a log line).
3. **(Confirm) station-aware campaign resolution** — separate, older item: your
   `db_get_campaign_id` matches commercial+date with TOP 1, ignoring station.
   With one-agency-per-campaign, the same commercial can be in two campaigns on
   different stations, so TOP 1 can attribute an airing to the wrong campaign.
   The web app resolves commercial scope itself (via CampaignCommercial +
   CampaignStation) and treats `Detection.CampaignID` as a hint only — so this
   is NOT blocking us. But making your write station-aware would let it be
   trusted. Flagging for when convenient.

## 4. What the web app still owes (so you have the full picture)

- **`pending`→`active` activation job:** a web-app nightly task flips a
  commercial to `active` when its campaign StartDate arrives, ordered to run
  BEFORE your midnight rebuild. Until this exists, pending commercials won't go
  live. (Web-app build, not yours — noted so you know the lifecycle is complete
  on our side.)
- Detection-viewing screens (uses the scoping engine; web-app build).

## 5. Questions for you

1. Are you OK to build the **puller** and the **library-manager DB read** as
   specified in §2a/§2b? Any change you'd want to the staging path or the
   `Status='active'` filter?
2. Timing: roughly when can the puller + library-manager land? It's the missing
   half — until both exist, registered commercials stage correctly but never get
   fingerprinted/detected. Helps us sequence the web-app detection-viewing work.
3. Anything you need the web app to write that it isn't writing? (e.g. a
   registration timestamp, a checksum for the puller to verify, a per-file
   marker.) The DB rows + staged file are all we currently produce.

No rush on §3.3 (station-aware CampaignID). §3.1/§3.2 are the blocking pair for
a visible end-to-end detection. Confirm §5 and we're synced.
