# Briefing for the Pipeline Thread — Detection Visibility Decision

**From:** the web-app thread
**Purpose:** decide whether the *pipeline* or the *web app* should compute
"who is allowed to see each detection." This decision sits across the
pipeline/web-app boundary, so we need the pipeline thread's input before the
web app commits to a schema.

This doc is self-contained — you don't need any other web-app context to act
on it. Please read the question at the end and report back.

---

## 1. Background: the web app needs to scope detections per viewer

The web app is becoming multi-tenant. Five kinds of organization will log in
and each must see only the detections relevant to them:

| Viewer | Should see, in one line |
|---|---|
| Internal (RadioMonitor staff) | everything |
| Client (e.g. Pick & Pay — a retailer/advertiser) | detections for campaigns it runs |
| Agency (e.g. Oracle Sun — manages campaigns for clients) | detections for campaigns it manages |
| Brand (e.g. Tiger Brands — owns products) | detections for its products, across all retailers |
| Station (e.g. 5FM) | detections that aired on it |

A single detection ("Clover Milk ad on 5FM at 14:32") can legitimately be seen
by several of these at once, for different reasons — the retailer that ran it,
the brand that owns the product, the agency managing the campaign, the station
that aired it, and internal staff. But NOT by unrelated tenants (a competing
retailer, an agency that doesn't manage that campaign).

## 2. What already exists in the database (built, but EMPTY)

The migrated DB already contains two tables that appear designed for exactly
this, but they have **zero rows** — never wired up:

**`DetectionVisibility`** — looks like a precomputed "who can see what" index:
```
VisibilityID (bigint, PK)
ClientID        → Client
DetectionSource (nvarchar 20)   -- which detection table: 'commercial'/'song'/'word'?
DetectionID     (bigint)        -- the id within that source table
SubscriptionID  → ClientSubscription
StationID       → Station
DetectedAt      (datetime2)
```

**`ClientSubscription`** — looks like the *rules* that drive visibility:
```
SubscriptionID (PK)
ClientID        → Client
SubscriptionType (nvarchar 50)
TargetValue      (nvarchar 1000)
StationFilter    (nvarchar 1000)
TimeFilter       (nvarchar 1000)
Description, IsActive, CreatedAt
```

Our reading: the original design intended *a client defines subscription rules
→ something matches each detection against those rules → writes matching
(detection, client) rows into `DetectionVisibility` → the web app reads that
index cheaply.* But it was never populated, so we don't know if the pipeline
was meant to do the matching, or the web app, or a separate job.

Note: these tables are **client-only** today. They have no concept of agency,
brand, or station as a viewer. If we keep this pattern, it needs generalizing
to all five viewpoints.

## 3. The two architectural options

**Option A — Precompute at write time (pipeline populates visibility).**
As the pipeline writes each detection, it also matches the detection against
the active subscription rules and writes the resulting visibility rows. The web
app then reads a cheap pre-built index. Fast reads; scales well. Cost: the
pipeline grows a new responsibility (rule-matching) and must keep visibility
rows correct as subscriptions change.

**Option B — Compute at read time (web app filters).**
The pipeline keeps doing exactly what it does now (detect + write detections,
nothing about visibility). The web app computes scope on every query by joining
detections → campaigns → the viewer's tenancy and filtering. No pipeline
change. Cost: heavier reads, scope logic lives entirely in the web app.

## 4. CONFIRM THIS FIRST — the load-bearing assumption

**The entire decision below rests on one claim the web-app thread believes but
needs the pipeline thread to verify against the actual pipeline code:**

> **CLAIM: The pipeline detects generically. When it writes a detection, it
> records "commercial #47 was detected on 5FM at 14:32" — it does NOT know or
> record which client, campaign, agency, or brand subscribed to or cares about
> that detection.**

Please confirm or correct this by checking the pipeline code directly:

- When `audio_fingerprint_detector.py` / `audio_extractor.py` write a row to
  `Detection`, do they set only generic fields (CommercialID, StationID,
  timestamps, clip path) — or do they ever look up / write a ClientID,
  CampaignID, or subscription reference?
- Same for `song_detector.py` → `SongDetection` and `word_detector.py` →
  `WordDetection`: purely generic, or client/campaign-aware?
- Note: the `Detection` table HAS a nullable `CampaignID` column. **Is the
  pipeline writing it, or leaving it NULL?** (The web-app thread believes it's
  left NULL — the migration even verifies "all NULL" — but confirm.)

**If the claim is TRUE** (pipeline is generic) → proceed to §5, the web app
owns all visibility, pipeline unchanged. This is what we're betting on.

**If the claim is FALSE** (pipeline already knows/writes client or campaign
info) → STOP and report what it actually writes. The whole visibility design
changes, because the pipeline would already be doing part of the scoping work,
and we'd build with that instead of around it.

## 4b. Then report on these (needed regardless)

1. **Does the pipeline read anything from `ClientSubscription` or write
   anything to `DetectionVisibility`?** (Both are empty — we believe not — but
   confirm.)
2. **Does the commercial fingerprint library already tie a detected commercial
   to a campaign/client**, or is a "commercial" just an audio sample with no
   ownership attached?
3. **The midnight rule** you established for the sample library — would the same
   nightly cadence apply to any visibility matching, or would it need to be
   real-time as detections arrive?

## 5. The decision — largely resolved, one open question

**Update since first draft:** the web-app thread confirmed the pipeline detects
**generically** (it writes "commercial #47 on 5FM," with no notion of which
client/campaign subscribed). That resolves the main fork:

> **The web app will own detection visibility (Option B). The pipeline needs NO
> changes.** It keeps detecting generically and writing detections. The web app
> resolves scope by following the links it already owns:
>
> ```
> Detection (has CommercialID, StationID)
>   → CampaignCommercial → Campaign (Client, Product/Brand, Agency)
>     → all five viewpoints resolve from here
> ```
>
> `DetectionVisibility` may later be used as a web-app-populated *cache* if
> read-time joins get slow — but the pipeline does not write it.

This is the cleanest outcome: the pipeline produces facts, the web app
interprets them.

### The one remaining question: songs and words

Commercial detections link to campaigns via `CommercialID → CampaignCommercial
→ Campaign`. But **`SongDetection` and `WordDetection` have no `CommercialID`** —
a song has artist/title, a word has a keyword. There's no path from those to a
campaign or subscription in the current schema.

The scenario suggests tenants may care about songs/words (a brand monitoring
its jingle; a client monitoring mentions of its name). So we need to know:

1. **Should song/word detections be visible to tenants at all**, or are they
   internal-only / a separate product?
2. If tenants should see them, **how does a song or word detection tie to a
   tenant?** Options the pipeline thread should weigh:
   - By a text/title rule a tenant subscribes to (e.g. client subscribes to the
     keyword "Pick & Pay"; any WordDetection of that keyword becomes visible to
     them) — this is what `ClientSubscription.TargetValue`/`StationFilter`/
     `TimeFilter` look designed for.
   - By some link we'd add from song/word detections to a campaign.
   - Not at all (internal-only).
3. **Does the pipeline's word/song detection already work from a configurable
   list** (the `word_detector_config.json` / `song_config.json` from the
   pipeline doc)? If so, who owns that list — and could tenant subscriptions
   feed it (the way commercial samples feed the fingerprint library)?

## 6. Why this is (still) blocking

The web app is about to expand its schema (Brand, Product, Station accounts,
per-campaign agency links). The commercial-detection scoping path is now clear.
But how songs/words scope to tenants determines whether we need extra linking
tables or a subscription-rule-matching mechanism — so we need the pipeline
thread's answer on §5 before finalizing the schema.

Please report: (1) findings on §4, and (2) your answer on the songs/words
question in §5.
