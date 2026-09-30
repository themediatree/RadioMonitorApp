# Addendum to Pipeline Thread — Two Items to Settle Before Web-App Migration

**From:** the web-app thread
**Context:** your commercial-registration return briefing settled the contract.
Two follow-ups need your explicit sign-off because **your** code (the puller and
the library-manager) reads what we're about to build. We don't want to migrate
the DB or build the file handoff on an assumption you'd have to work around.

Both are small. Please confirm or adjust §A and §B.

---

## §A — Staging handoff shape (you chose: staging fallback)

AUDIOPROC can't take inbound writes, so we're using the staging fallback you
offered. We've decided its exact shape and want you to confirm the puller side.

**Decision: "mirror staging" — the web app builds a tree on AUDIOREC that is an
exact mirror of the target data tree; your puller does a dumb, stateless
mirror-copy into AUDIOPROC's data tree, then clears what it copied.**

Concretely, the web app writes (one copy per assigned station — your per-station
filter requires the multi-copy, confirmed):

```
D:\RadioMonitor\staging\<StationName>\generic\<TapeID>.mp3      (generic audio)
D:\RadioMonitor\staging\<StationName>\liveread\<TapeID>.txt     (liveread text)
```

Your puller's job (future pipeline work, not blocking us): mirror-copy
`staging\<station>\<category>\*` into `C:\RadioMonitor\data\<station>\<category>\`,
then remove what it successfully copied. **No manifest, no DB read, no station
parsing** — the folder structure carries everything. The puller is brainless by
design.

**Confirm:**
1. The staging root. We propose AUDIOREC `D:\RadioMonitor\staging`, exposed to
   the puller as `\\AUDIOREC\RadioMonitor\staging` (same share style as the
   AUDIOPROC→AUDIOREC direction we already built, just reversed). Is that the
   path your puller should read, or do you want a different location?
2. That a **mirror-copy-then-clear** puller (no manifest) is what you'll build.
3. Whether the puller, or the nightly library-manager, should own the
   clearing of staging after a successful copy (so we agree who deletes).

(If you'd rather the web app write a flat file + manifest and have the puller
fan out, say so — but we think mirror-staging keeps your puller trivial and the
fan-out knowledge on our side where the campaign/station data lives.)

---

## §B — Withdrawal status column (you said: a status the library-manager honors)

You said withdrawal would be "a status the library-manager honors" by filtering
it out of the nightly rebuild. Since **your** rebuild reads it, the column name
and values must match on both sides. We're about to migrate the DB and want to
add exactly what you'll filter on.

**Proposal:** add a column to the `Commercial` table:

```
Commercial.Status   NVARCHAR(20) NOT NULL DEFAULT 'active'
                    allowed: 'active' | 'withdrawn'
```

Your library-manager's rebuild would then skip withdrawn commercials, e.g.:
```sql
... WHERE Status <> 'withdrawn'    -- or  WHERE Status = 'active'
```

**Confirm:**
1. Column on `Commercial` is fine (vs. you preferring a separate table to read)?
   `Commercial` is your table; we're proposing to add one column to it.
2. The values — just `active`/`withdrawn`, or do you also want `pending`
   (your §4 mentioned pending/active/withdrawn)? If a commercial shouldn't be
   detectable until something confirms it, `pending` matters; if registration =
   immediately-active-next-midnight, we don't need it.
3. The exact filter you'll apply, so we document it on both sides.

---

## Why this blocks the migration (briefly)

§B changes the DB schema (`Commercial.Status`), and that column exists *for*
your library-manager filter — so we want your agreement on its name/values
before we run the migration, to avoid the "one side assumed a column the other
relies on" problem we've already hit.

§A doesn't change the DB, but it defines the staging path your puller reads, so
confirming it now means we build the web-app fan-out against the path you'll
actually watch.

Please confirm §A.1–3 and §B.1–3 (or adjust). Then we migrate + build the
registration flow on settled ground.
