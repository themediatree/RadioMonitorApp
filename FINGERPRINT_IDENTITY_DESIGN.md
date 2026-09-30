# Fingerprint Identity Design — Option C

**Status:** decided with the pipeline thread (see
`PIPELINE_MULTI_SUBSCRIBER_RESPONSE.md` uploaded in the prior conversation),
not yet implemented.

**Read this AFTER `ENTITY_MODEL.md`.**

This doc explains: how multiple Subscribers can register the same audio
without the pipeline producing duplicate detections, and how the web app
fans out detection visibility to all of them.

---

## 1. The problem we're solving

In the real product, multiple Subscribers will pay independently for proof
of broadcast of the **same** commercial — e.g. Oracle Sun the agency, Pick
& Pay the advertiser, and Clover Milk the brand owner all want detections
for the Clover Milk 30-second ad. Each registers it independently with
their own Tape ID, brand metadata, station selection, and billing.

The naive approach: each registration writes a separate file into the
pipeline's `data\` tree. The pipeline fingerprints each independently, treats
them as N library entries, and produces N detection rows per airing. That's
Option A.

It works, but it does not scale. The pipeline thread quantified the cost:
N× CPU at library-rebuild time, N× disk for clip extractions per airing
(critical given SQL Express's 10GB limit + AUDIOREC's shared disk). At
election-season scale (10 Subscribers × 100 airings × 5 stations = 5,000
clip files for one ad), it becomes a real production problem.

The solution: **deduplicate at registration by audio content**, share one
file across Subscribers, fan out detection visibility in the web app.

## 2. The core mechanism — fingerprint as content identity

The pipeline's existing fingerprinter computes, for any 30-second mp3:

- A dictionary of hash keys derived from the audio's spectrogram peaks.
- Output is **deterministic** for the same audio content (modulo encoding
  differences, which the algorithm is robust to).
- Two mp3s of the same source ad — encoded at different bitrates, even —
  produce nearly-identical hash sets.

The **FingerprintID** is the SHA256 of the sorted hash keys from this output.
Two identical-content uploads yield the same FingerprintID. Different audio
yields different FingerprintIDs. It's a content-addressable identity for
the audio.

Quantitatively, per the pipeline thread:

- Fingerprinting a 30-second mp3: 1-2 seconds on the production hardware.
- Computing SHA256 of the hash set: microseconds.
- So the per-registration cost: ~1-2 seconds. Acceptable for a UI submit.

## 3. The flow at registration time

When a Subscriber uploads a commercial:

```
1. Web app receives the file. Validates type/size. Converts to mp3 if needed.
2. Web app calls fingerprint_core.fingerprint_audio(mp3_path)
   → produces the hash dict.
3. Web app computes FingerprintID = sha256(json(sorted(hash_dict.keys())))
4. Web app queries:
       SELECT TOP 1 CommercialID, IsFileOwner, ...
       FROM Commercial
       WHERE FingerprintID = ? AND Status IN ('active','pending')
       ORDER BY CommercialID
5. Two cases:
   A) MATCH FOUND -- the audio is already registered by someone.
      → This Subscriber's new Commercial row is created with
        FingerprintID = ?, CommercialName = <SubID>_<DisplayTapeID>.
        It is a "follower" row in the sense that no file is staged for it
        (the existing file-owner already has the audio in staging / data).
        File-owner identity is implicit: the file-owner is the row whose
        CommercialName matches the on-disk filename stem. See §5 for
        withdrawal flow.
      → Subscriber gets visibility via the FingerprintID join (§4).
   B) NO MATCH -- this audio is new.
      → This Subscriber's row is created with FingerprintID = ?,
        CommercialName = <SubID>_<DisplayTapeID>.
      → File is staged to <staging>\<station>\<category>\
                          <SubID>_<DisplayTapeID>.<ext> for each station the
        Subscriber selected. Write-then-rename, atomic.
      → The original audio is also archived to <audio_archive>\
        <FingerprintID>.mp3 (see §5) so it can be re-staged later if this
        row withdraws while siblings remain active.
```

The user sees a small notice in case (A): "this audio is already known to
RadioMonitor; your registration is being added alongside the existing one."
This is a feature, not a bug — they need to know they're not the first.

## 4. The scoping query — visibility via FingerprintID

An external user sees a Detection iff there exists a Commercial that the
pipeline can attribute the airing to, AND that Commercial shares a
FingerprintID with another Commercial owned by the user's Subscriber.

In SQL (one form, illustrative):

```sql
SELECT d.*
FROM Detection d
JOIN Commercial c_detected ON c_detected.CommercialID = d.CommercialID
JOIN Commercial c_mine     ON c_mine.FingerprintID = c_detected.FingerprintID
                          AND c_mine.SubscriberID = :my_subscriber_id
                          AND c_mine.Status = 'active'
WHERE c_detected.FingerprintID IS NOT NULL
```

In SQLAlchemy terms in `scoping.py`, the `commercial_filter(user)` for
external users becomes:

```python
my_sub_id = user.SubscriberID

# Alias the Commercial table for clarity: c_det is the detection's commercial,
# c_mine is one of my Subscriber's commercials sharing the same fingerprint.
c_det = aliased(Commercial)
c_mine = aliased(Commercial)

return exists().where(
    and_(
        c_det.CommercialID == Detection.CommercialID,
        c_det.FingerprintID.isnot(None),
        c_mine.FingerprintID == c_det.FingerprintID,
        c_mine.SubscriberID == my_sub_id,
        c_mine.Status == 'active',          # render = 'active' on SQL Server
    )
)
```

Internal users get `true()`. Stations get the same FingerprintID join (a
Radio Station Subscriber sees airings they registered, same as any other
Subscriber — see `ENTITY_MODEL.md` §1 on why stations are just another type).

**Dialect safety:** the new query uses string equality (`'active'`),
not boolean, so the `IS 1` problem doesn't apply here. But the suite must
still compile this query against `mssql.dialect()` to catch any other
dialect surprises in joins/aliases.

## 5. File-owner semantics — withdrawal under Option α

There is **no** `IsFileOwner` column. Under contract decision (ii) (see
`PIPELINE_FILENAME_ADDENDUM_RESPONSE.md`), `Commercial.CommercialName` IS the
on-disk filename stem (the prefixed form `<SubID>_<DisplayTapeID>`).
Therefore the file's "owner" at any moment is simply: *the Commercial row
whose `CommercialName` byte-equals the on-disk filename and whose
`Status='active'`.* The library-manager's resolver
(`WHERE CommercialName=? AND Status='active'`) makes this concrete: if no
active row matches the on-disk filename, the file goes dormant at next
midnight rebuild. No explicit ownership column or transfer logic needed at
the DB level.

But there IS still a withdrawal scenario that requires web-app action,
called Option α by the pipeline thread:

### The scenario

Subscriber A and Subscriber B both registered the same audio (same
`FingerprintID`). A's file is the one in staging/data tree:
`SUB-A_<TapeID_A>.mp3`. A withdraws. Without intervention:

- A's row is `Status='withdrawn'`.
- The on-disk file `SUB-A_<TapeID_A>.mp3` has no active row matching it
  anymore (A's row, which matched, is withdrawn).
- Library-manager skips it at next midnight; the file goes dormant.
- B is still paying for detections but has no file in the tree — so the
  audio is never fingerprinted and B sees nothing.

This would silently break B's subscription. Unacceptable.

### Option α — restage a sibling's file on file-owner withdrawal

When the web app withdraws a row, it must check: was this the file-owner
row (i.e. is there a file in staging/data whose stem equals this row's
`CommercialName`), and are there any active sibling rows (same
`FingerprintID`, `Status='active'`, different `SubscriberID`)?

If yes to both, the web app must **stage one of the active siblings' file**.
Concretely:

```python
def withdraw_commercial(db, commercial):
    commercial.Status = 'withdrawn'

    # Find any active siblings sharing this FingerprintID.
    siblings = (db.query(Commercial)
                .filter(Commercial.FingerprintID == commercial.FingerprintID,
                        Commercial.SubscriberID != commercial.SubscriberID,
                        Commercial.Status == 'active')
                .order_by(Commercial.CreatedAt)  # oldest sibling first
                .all())
    if not siblings:
        return  # last one out; file rightly goes dormant

    # Pick the oldest active sibling and stage its file for every station it
    # is assigned to. The pipeline's puller picks it up within seconds; the
    # next midnight rebuild fingerprints it under that sibling's CommercialName.
    chosen = siblings[0]
    _stage_sibling_file(chosen)
```

Notes:

- The web app **already has** the audio bytes when it accepts the
  registration upload. We could store them at registration time as a
  `Commercial.AudioBlob` (varbinary(max)) so we can re-emit later. But that
  duplicates data. Alternative: keep the original upload in a long-lived
  `audio_archive\<FingerprintID>.mp3` directory the web app owns, separate
  from staging — used only as the source for any re-stage operation.
  **Recommendation: the archive directory** — single source per content,
  small overhead, simple ops.
- The deterministic sibling pick (oldest active) means the same withdrawal
  always elects the same successor — predictable, testable.
- The brief one-day gap (between withdrawal and the next midnight rebuild)
  during which no detections fire for B is acceptable per the pipeline
  thread's confirmation. Withdrawal is not time-critical.

### Audio-archive directory

To enable re-staging, the web app maintains:

```
D:\RadioMonitor\audio_archive\<FingerprintID>.mp3
```

written once at the first registration of any audio (when no
`FingerprintID` match exists yet). Every Subscriber registering the same
audio thereafter does NOT write to this archive (the file is already there).
Withdrawal-driven restage reads from this archive and writes a per-Subscriber
filename into staging. The archive is the canonical content store; staging
is the per-Subscriber delivery layer.

The archive is NEVER read by the pipeline — it's web-app-internal. The
pipeline still only reads from `data\` (via staging).

### What is NEVER deleted

Files in `data\` stay forever — the library-manager's `Status='active'`
filter is what controls detection, not file presence. This is the
audit-preserving design we agreed.

## 6. The pipeline's side — small but real changes

The pipeline thread will (their work, not ours):

- Library-manager: when fingerprinting a file at rebuild time, optionally
  store the same FingerprintID on the library entry. Lets file ↔
  FingerprintID round-trip explicitly. They flagged this as optional;
  worth doing for diagnostics.
- The resolver query stays:
  `SELECT 1 FROM Commercial WHERE CommercialName=? AND Status='active'`.
  Under the new model this may return multiple rows (different Subscribers
  with the same TapeID via per-Subscriber uniqueness), but the filename
  prefix `<SubscriberID>_<TapeID>` makes filenames unique by construction —
  so in practice the resolver still finds exactly one match per file.

The puller is unchanged. Write-then-rename, mirror-copy-then-clear, skip
`*.tmp`. The contract from `COMMERCIAL_REGISTRATION_CONTRACT.md` stands.

## 7. Tests that must exist (R2 deliverable)

This is the trickiest area of the refactor; tests are not optional.

1. **Fingerprint helper determinism** — same audio bytes → same
   FingerprintID, across two independent invocations.
2. **Dedup at registration** — register audio X as Subscriber A; register
   the same audio X as Subscriber B; expect two Commercial rows, same
   FingerprintID, one file in staging (named with A's prefix). B has no
   staged file of its own.
3. **No-dedup when audio differs** — two genuinely different mp3s produce
   different FingerprintIDs and two files staged.
4. **Visibility via FingerprintID** — both A and B see the same Detection
   when the pipeline writes one.
5. **Visibility scoping holds for unrelated Subscribers** — Subscriber C,
   who didn't register this audio, sees nothing.
6. **Withdrawal of follower** — B withdraws; A's file in staging untouched;
   A still sees detections.
7. **Withdrawal of file-owner with siblings (Option α restage)** — A
   withdraws while B is active; web app restages B's file (named with B's
   prefix) into the same staging path; the original A-named file goes
   dormant at next rebuild; B still sees detections via the FingerprintID
   join.
8. **Withdrawal of last** — the only active row withdrawn; no sibling exists;
   no restage attempted; library-manager skips the file at next rebuild
   (status filter); no detections fire after.
9. **Audio archive** — first registration of an audio writes its mp3 to
   `audio_archive\<FingerprintID>.mp3`; subsequent registrations of the same
   audio do NOT re-write it; withdrawal-driven restage reads from it.
10. **Dialect safety** — the new scoping query compiles to valid T-SQL.

The Scenario 3 tests from the prior session collapse to (4)+(5)+(6), which
is genuinely simpler than what we had before.

## 8. Performance notes

- **Registration latency:** dominated by fingerprinting (~1-2s). The
  user-visible wait is acceptable for a one-time submit.
- **Scoping query cost:** the FingerprintID join is on an indexed column
  (`CREATE INDEX IX_Commercial_FingerprintID ON Commercial(FingerprintID)`
  in R1). Should remain fast even at thousands of rows.
- **Storage:** one file per FingerprintID per station, not per Subscriber
  per station. Linear in distinct audio content, not in Subscribers.

## 9. Open considerations

- **Fingerprint algorithm changes:** if the pipeline ever changes its
  fingerprinting algorithm, existing `FingerprintID`s are stale and dedup
  breaks. Probably never happens in practice (fingerprinters are stable),
  but worth knowing. A version field on `FingerprintID` (e.g. `v1:abcd1234`)
  would let us detect mixed-version stores. Defer until needed.
- **Near-matches:** two mp3s that are 95% the same content but not
  byte-identical might produce DIFFERENT FingerprintIDs (depending on the
  algorithm's tolerance). If this proves to be a real problem (clients
  uploading slightly different cuts of the same ad), we'd switch to a
  fuzzy-match identity scheme. Not a launch concern.
- **The fingerprint code dependency:** the web app needs access to
  `fingerprint_core.py` (or equivalent). The R2 plan: copy a stable snapshot
  into the web-app repo as `app/utils/fingerprint.py`, document its
  provenance, and coordinate with the pipeline thread on updates. Alternative
  considered and rejected: run fingerprinting via a subprocess call out to
  a pipeline-side worker — adds latency, deployment complexity, and a
  cross-server hop for what is ultimately a pure CPU function. Direct
  import (after copying the file) is simpler.
