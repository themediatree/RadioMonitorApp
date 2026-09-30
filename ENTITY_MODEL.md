# Entity Model — The Subscriber Design

**Status:** decided, not yet implemented. Supersedes the old
Agency/Client/StationAccount tripartite model.

**Read this BEFORE** `FINGERPRINT_IDENTITY_DESIGN.md` and before any code.

---

## 1. What RadioMonitor models — and what it doesn't

RadioMonitor does NOT book ads on radio stations. It does NOT mediate the
relationship between advertisers and stations. It does NOT track who buys
airtime from whom.

RadioMonitor sells **proof-of-broadcast as a service**. Any entity who wants
proof of broadcast for a commercial (their own, or one they're authorised to
detect) can pay RadioMonitor to register that commercial and receive detections
when it airs.

The product reality: **each paying entity is a Subscriber**. They register
commercials. They see their own detections. They pay independently. There is
no "agency manages client" relationship in the system because that
relationship is irrelevant to what RadioMonitor sells.

## 2. The Subscriber concept

A **Subscriber** is any paying entity. The seven recognised types:

| Type | Real-world example | Why they subscribe |
|---|---|---|
| **Agent** | A media agency like Oracle Sun | Tracks airtime they booked on behalf of advertisers |
| **Advertiser** | Pick & Pay, Shoprite, FNB | Tracks airtime they booked directly with stations |
| **Brand Owner** | Clover Milk, Tiger Brands, Cadbury | Tracks brand mentions/advertising independently of who booked them |
| **Radio Station** | A community radio station | Offers proof-of-broadcast to their own advertisers as a service |
| **Government** | Department of Health, SARS | Tracks public-info / departmental campaigns |
| **Political Party** | ANC, DA, EFF | Tracks election campaigning (highly relevant in SA midterm elections) |
| **Other** | Anything not above | Free-text description required |

"Other" exists so the model can absorb future types without a schema change
(emergency services, NGOs, etc.). A required description column captures
what the subscriber actually is.

## 3. Database shape

### `Subscriber` table

```sql
CREATE TABLE Subscriber (
    SubscriberID         INT IDENTITY PRIMARY KEY,
    Name                 NVARCHAR(255)  NOT NULL,
    Slug                 NVARCHAR(120)  NOT NULL UNIQUE,
    SubscriberType       NVARCHAR(30)   NOT NULL,
    OtherDescription     NVARCHAR(255)  NULL,  -- required iff Type='Other'
    ContactEmail         NVARCHAR(255)  NULL,
    ContactPhone         NVARCHAR(50)   NULL,
    SubscriptionPlan     NVARCHAR(50)   NOT NULL FK -> SubscriptionPlanConfig.PlanCode,
    SubscriberStatus     NVARCHAR(20)   NOT NULL DEFAULT 'active',
    StationID            INT            NULL FK -> Station.StationID,  -- iff Type='Radio Station'
    SubscriptionStart    DATETIME2      NULL,
    SubscriptionEnd      DATETIME2      NULL,
    CreatedAt            DATETIME2      NOT NULL DEFAULT SYSDATETIME(),
    UpdatedAt            DATETIME2      NOT NULL DEFAULT SYSDATETIME(),

    CONSTRAINT CK_Subscriber_Type CHECK (
        SubscriberType IN ('Agent','Advertiser','Brand Owner','Radio Station',
                           'Government','Political Party','Other')),
    CONSTRAINT CK_Subscriber_Status CHECK (
        SubscriberStatus IN ('active','expired_grace','archived','cancelled')),
    CONSTRAINT CK_Subscriber_Other_Description CHECK (
        (SubscriberType = 'Other' AND OtherDescription IS NOT NULL AND LEN(OtherDescription) > 0)
        OR (SubscriberType <> 'Other')),
    CONSTRAINT CK_Subscriber_Station CHECK (
        (SubscriberType = 'Radio Station' AND StationID IS NOT NULL)
        OR (SubscriberType <> 'Radio Station' AND StationID IS NULL))
);
```

Notes on the design:

- **`Slug`** is auto-generated from `Name`, used in URLs, kept unique. Same
  pattern as the old `Client`/`Agency` tables.
- **`StationID`** is non-null *only* for `Radio Station` type. This is the
  one structural difference between types — a Radio Station subscriber binds
  to a real `Station` row (the pipeline's station table). This replaces the
  old `StationAccount` table cleanly.
- **`OtherDescription`** is required when `SubscriberType='Other'` and
  forbidden otherwise (CHECK enforces this).
- **`SubscriberStatus`** keeps the same vocabulary as the old `ClientStatus`/
  `AccountStatus` for consistency.

### `User` table changes

```sql
ALTER TABLE [User] ADD SubscriberID INT NULL FK -> Subscriber.SubscriberID;
-- backfill from existing ClientID/AgencyID/StationAccountID
-- drop old columns + drop AgencyClient + replace CK_User_Ownership

CONSTRAINT CK_User_Ownership CHECK (
    (UserType = 'internal' AND SubscriberID IS NULL)
    OR (UserType <> 'internal' AND SubscriberID IS NOT NULL)
);
```

- `UserType` values collapse from 7 to 3: `internal`, `subscriber_admin`,
  `subscriber_user`. The seven Subscriber *types* are an attribute of the
  Subscriber, not the User. A user is "the admin or the user of their
  Subscriber" regardless of what type that Subscriber is.
- The old `client_admin` / `agency_admin` / `station_admin` collapse to
  `subscriber_admin`. The "admin tier within their own tenant" semantic stays.
- Internal users still have no Subscriber binding.

### `Campaign` and `Commercial` changes

```sql
ALTER TABLE Campaign DROP COLUMN ClientID, AgencyID;
ALTER TABLE Campaign ADD SubscriberID INT NOT NULL FK -> Subscriber.SubscriberID;

ALTER TABLE Commercial ADD SubscriberID    INT NOT NULL FK -> Subscriber.SubscriberID;
ALTER TABLE Commercial ADD DisplayTapeID   NVARCHAR(100) NOT NULL;  -- user's clean TapeID
ALTER TABLE Commercial ADD FingerprintID   NVARCHAR(64)  NULL;
-- CommercialName now stores "<SubscriberID>_<DisplayTapeID>" (the prefixed form
-- that is byte-for-byte equal to the staged filename stem). See
-- FINGERPRINT_IDENTITY_DESIGN.md §5 for the rationale.
-- Drop any old UNIQUE on CommercialName; add the new business-rule constraint:
ALTER TABLE Commercial ADD CONSTRAINT UQ_Commercial_Subscriber_TapeID
    UNIQUE (SubscriberID, DisplayTapeID);
CREATE INDEX IX_Commercial_FingerprintID ON Commercial(FingerprintID);
```

Notes:

- `CommercialName` IS the on-disk filename stem (prefixed). The pipeline's
  resolver looks up by `CommercialName`, byte-for-byte. We never carry an
  `IsFileOwner` column: the existence of an active Commercial row whose
  `CommercialName` equals the on-disk filename stem IS the file-ownership
  invariant. See FINGERPRINT_IDENTITY_DESIGN §5.
- `DisplayTapeID` is the user-facing name (their own naming convention, e.g.
  `NCHK_030_1735_E`). Two different Subscribers can both use the same
  `DisplayTapeID`; the `UQ_Commercial_Subscriber_TapeID` constraint only
  forbids a Subscriber from reusing their own.

Each Campaign now belongs to exactly one Subscriber (the one who registered
it). Each Commercial belongs to exactly one Subscriber (the one who
registered it). Multiple Subscribers can register the same audio — they each
get their own Commercial row, joined by a shared `FingerprintID`. See
`FINGERPRINT_IDENTITY_DESIGN.md` for the FingerprintID semantics.

`AgencyClient` is dropped entirely. There is no agency-manages-client
relationship in the data.

## 4. Authorization — the rule, in one sentence

> An external user (anyone with a Subscriber binding) sees exactly the
> Detections that map to a Commercial whose `FingerprintID` is also held by
> a Commercial owned by the user's Subscriber. Internal users see everything.

That's it. No agency-vs-client distinction. No campaign-membership inference.
No managed-clients set to compute. Just "do I share a fingerprint with this
Detection's Commercial?"

The FingerprintID dedup model is what makes this work cleanly: when a
detection fires for one Commercial, all Subscribers who registered the same
underlying audio see it (because they share the FingerprintID), without the
pipeline having to produce N parallel detections.

See `FINGERPRINT_IDENTITY_DESIGN.md` for the SQL shape of the scoping query.

## 5. The Terms & Conditions confirmation — important

At commercial registration, the registering user must affirm a T&C statement
to the effect of:

> "I confirm that I am the lawful owner of this commercial, or have explicit
> authority from the lawful owner to upload it for detection purposes, and I
> grant RadioMonitor authority to track, detect, and extract clips of this
> commercial when it airs on radio stations."

This is a **legal gate**, not a UX nicety. It must be:

- **Versioned** — a `TermsVersion` constant in the codebase, bumped when
  wording changes. The version is captured at agreement time.
- **Logged** to `AuditLog` with: `action='commercial_registered_terms_agreed'`,
  the `UserID`, the `SubscriberID`, the `CommercialID` (after creation), the
  `TermsVersion`, and the user's IP address.
- **Required** to submit the form. No agree-by-default checkbox.
- **Audit-recoverable** — given any disputed commercial, we must be able to
  produce "user X, of Subscriber Y, on date Z, agreed to T&C version V."

The exact wording lives in a constants module (e.g. `app/legal.py`) so
versioning is explicit and reviewable.

## 6. Naming convention — UI vs code

| Concept | UI label | Code identifier |
|---|---|---|
| The entity type "Subscriber whose type is Advertiser" | "Advertiser" | `Subscriber` row with `SubscriberType='Advertiser'` |
| The data model concept | (not exposed) | `Subscriber`, `SubscriberID` |
| Existing `Client.ClientID` references in the codebase | n/a | renamed to `SubscriberID` |

The user has indicated they often say "Advertiser" colloquially. UI templates
should reflect that. The data model is "Subscriber" because that's what the
thing actually is in the product.

## 7. Migration path — clean-slate, NOT data-migrate

The user chose a full reset rather than data migration. Practically: the 6
test Clients and the invited Oracle Sun agency user that were created during
the prior session's UI testing are throwaway — they have no semantic meaning
under the new Subscriber model and would require guessing-as-migration (was
"Pick & Pay" an Advertiser or a Brand Owner?). They are dropped.

Migration `007` therefore:

- Creates the new `Subscriber` table and the new `Commercial`/`Campaign`
  columns.
- Drops `AgencyClient`, then drops `Client`, `Agency`, `StationAccount`,
  and the old FK columns on `User` and `Campaign`.
- Truncates `User` (except the internal admin? — see below), `Campaign`,
  `Commercial`, `CampaignCommercial`, `CampaignStation`, `AuditLog` (it
  references the old tables' IDs which are gone), `UserInvitation`,
  `ClientSubscription`, `ApiKey`.
- Reference data preserved: `Station` (6 rows) and `SubscriptionPlanConfig`
  (4 rows) are NOT touched.

Internal admin handling: the existing internal admin row in `User` has
`UserType='internal'` with all three old FKs NULL, which is already
compatible with the new `CK_User_Ownership` rule
("Internal AND SubscriberID IS NULL"). So we keep the admin row, just add
its `SubscriberID` column (default NULL) and drop the three old FK columns.
No re-seeding needed unless the admin password has been forgotten.

The seed admin script (`scripts/seed_admin.py`) is updated to create future
internal admins with `SubscriberID = NULL`.

The admin then registers Subscribers through the new UI in R3.

No back-compatibility shim. No dual-write period. No data migration.

## 8. What this does and doesn't change

**Changes:**
- Tenant management UI (one set of CRUD screens, not three)
- User invitation flow (one `SubscriberID` to pick, not three)
- Registration form (Subscriber-based scoping, T&C added)
- Scoping engine (FingerprintID join, see fingerprint doc)
- Models: collapse `Client`/`Agency`/`StationAccount` → `Subscriber`
- User type vocabulary: 7 values → 3
- Audit log shape (one `target_subscriber_id`, not three)
- All tests (Scenario 3 collapses to a simpler shape)

**Does NOT change:**
- The pipeline (web-app internal refactor)
- The staging/data file contract (still `staging\<station>\<category>\
  <SubscriberID>_<TapeID>.<ext>` with write-then-rename)
- The `Commercial.Status` lifecycle (pending/active/withdrawn)
- The `pending→active` activation job
- The detection-viewing UI shape (list + detail + clip)
- ffmpeg conversion, audit log, invitation flow mechanics

## 9. Open questions / future considerations

These are not blocking the refactor but worth noting:

- **Per-user-within-Subscriber permissions** (admin restricts a user to
  certain stations or campaigns). Deferred. The current `subscriber_admin`/
  `subscriber_user` split is a coarse start.
- **Self-service plan changes** by a Subscriber admin. Decided as a feature,
  UI not built.
- **Inviting users into "Other"-type Subscribers**: no special UI handling
  required (Other is just a type, same flow). But worth verifying after R3.
- **Multi-employer users** (a consultant working for two agencies): the user
  has accepted "register as Agent" as the pragmatic answer; one
  `SubscriberID` per user stands.
