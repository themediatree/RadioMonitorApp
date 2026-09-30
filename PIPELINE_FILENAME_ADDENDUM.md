# Addendum to Pipeline Thread — Filename vs CommercialName under Option C

**From:** the web-app thread
**Purpose:** one tightening on the multi-subscriber/Option C contract before
the web app builds migration 007. Your earlier response left this point open
(see §4 and §5 of PIPELINE_MULTI_SUBSCRIBER_RESPONSE), and the answer affects
your resolver query.

Short read. Three options, my lean is (ii), tell me which you prefer.

---

## The unsettled question

We agreed:
- Per-Subscriber `CommercialName` uniqueness (the TapeID is the Subscriber's
  own naming convention; two Subscribers can both legitimately use
  `NCHK_030`).
- Staged filenames prefixed with `<SubscriberID>_` so the on-disk filename
  is globally unique by construction.
- Your library-manager resolver:
  `SELECT 1 FROM Commercial WHERE CommercialName = ? AND Status = 'active'`
  where `?` is the **filename stem**.

The unsettled bit: **what is `CommercialName` in the DB row?**

If `CommercialName = TapeID` (no prefix) and `filename = <SubID>_<TapeID>`,
then the resolver's lookup `WHERE CommercialName = <SubID>_<TapeID>` misses
every row — the names don't match.

So one of the following has to be true.

## The three options

**(i) Pipeline strips the prefix before lookup.**
Resolver becomes:
```python
stem = filename_stem.split("_", 1)[1] if "_" in filename_stem else filename_stem
SELECT 1 FROM Commercial WHERE CommercialName = stem AND Status='active'
```
- Pro: `CommercialName` stays as the user's clean TapeID.
- Con: the puller/library-manager gains awareness of the prefix scheme.
  Breaks the "brainless" property we agreed on. If the prefix format ever
  changes, both sides have to update.

**(ii) Store `CommercialName = <SubID>_<TapeID>` in the DB.** ← my lean
- The `Commercial` row's `CommercialName` matches the filename stem exactly.
- The user's clean TapeID lives in a separate `DisplayTapeID NVARCHAR(100)`
  column (or similar) so the UI still shows them their own convention.
- Resolver unchanged: `WHERE CommercialName = <stem> AND Status='active'`.
- Pro: pipeline stays brainless; existing resolver works untouched; one
  unambiguous source of truth (the filename and the DB name are identical).
- Con: `CommercialName` is now an internal identifier, not what the user
  typed. The web app maintains a `DisplayTapeID` for UI/audit purposes.

**(iii) Pipeline resolver keys on `FingerprintID` instead of `CommercialName`.**
- Resolver computes the file's fingerprint at scan time, then:
  `SELECT 1 FROM Commercial WHERE FingerprintID = ? AND Status='active'`
- Pro: filenames become purely cosmetic; rename-friendly; matches the
  spirit of Option C's "fingerprint is the identity."
- Con: changes your resolver shape and the library-manager has to compute
  fingerprints during the scan (it does this anyway when building the
  library, but the resolution step would move earlier). More change on
  your side.

## What we need from you

Pick (i), (ii), or (iii) — or propose another. We need a definite answer
because the schema migration commits to whatever the answer is.

My honest preference is **(ii)**: preserves the brainless puller, leaves
your resolver untouched, and keeps the filename-as-CommercialName invariant
that simplifies reasoning. The cost is a `DisplayTapeID` column on
`Commercial` so the user's own convention is still surfaced — that's a
small price for the contract clarity.

The deeper reason for (ii) over (i): "the pipeline understands a prefix
scheme" is exactly the kind of coupling that bit us before (the testing-vs-
production filename convention you flagged in your earlier briefing). Better
to have the prefix be an opaque part of the identifier than a parsable
structure.

## One related cleanup

Your earlier response §5 also said "ownership transfer when the file-owner
row withdraws" — the web-app side. Under (ii) that's straightforward:
- File on disk is named `<SubID_A>_<TapeID_A>.mp3`.
- Commercial row A's `CommercialName = SubID_A_TapeID_A`, withdrawn.
- Active sibling B has its own `CommercialName = SubID_B_TapeID_B`, a
  different filename it never had.
- The file's identity is "the row whose `CommercialName` equals the
  filename stem AND is active" — when A withdraws, no active row matches
  the filename stem anymore, and your resolver's "no active row → log SKIP"
  branch fires. The file goes dormant from next midnight.
- Detection visibility for B continues because the FingerprintID join
  doesn't depend on which row owns the file — it depends on the airing's
  Commercial row sharing a FingerprintID with B's row.

So under (ii) we don't actually need `IsFileOwner` as a separate column at
all — the on-disk filename *is* the file-owner identifier, by construction.
That's a nice simplification.

Under (i) or (iii), we likely still want `IsFileOwner` because the binding
is looser.

## Confirm

- Pick (i), (ii), or (iii) [or propose other].
- If (ii): confirm `IsFileOwner` can be dropped from the design and the
  filename is the implicit owner-of-record.
- Anything else this changes on your side?

This is the last cross-thread item before R1 schema migration. Once you
confirm, we build 007 against settled ground.
