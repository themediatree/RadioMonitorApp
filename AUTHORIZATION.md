# Authorization Model — v0.3

**Status:** decided. Supersedes v0.2's agency/client/station tripartite model.
The new authorization story is built on the flat Subscriber model
(`ENTITY_MODEL.md`) and the FingerprintID identity (`FINGERPRINT_IDENTITY_DESIGN.md`).

## 1. The one rule

> An external user sees exactly the Detections that map to a Commercial
> whose `FingerprintID` is also held by a Commercial owned by the user's
> Subscriber, where that Subscriber-owned Commercial is `Status='active'`.
> Internal users see everything.

That is the whole authorization story for commercial detections. No nesting
of agency-vs-client. No campaign-membership inference. No managed-clients
set. Pure: *"do I share a fingerprint with the thing detected?"*

## 2. User → Subscriber binding

Every external user belongs to exactly one Subscriber via `User.SubscriberID`.
The Subscriber is the authorization principal. The User is the identified
human within it.

Internal users (RadioMonitor staff) have `SubscriberID = NULL` and bypass
all scoping. `CK_User_Ownership` enforces:

```
(UserType = 'internal' AND SubscriberID IS NULL)
OR (UserType <> 'internal' AND SubscriberID IS NOT NULL)
```

## 3. User types

Three values:

- `internal`        — RadioMonitor staff; sees everything; runs admin UI.
- `subscriber_admin` — admin within their Subscriber; can invite users
                       into the Subscriber, edit Subscriber-level settings.
- `subscriber_user`  — regular user within their Subscriber; can register
                       commercials, view detections, but not manage users
                       or Subscriber settings.

The seven *Subscriber* types (Agent / Advertiser / Brand Owner / Radio
Station / Government / Political Party / Other) are attributes of the
Subscriber, NOT user types. A `subscriber_admin` of an "Agent" Subscriber and
a `subscriber_admin` of an "Advertiser" Subscriber have identical permissions
within their respective tenants.

## 4. The scoping function shapes

For each detection table, the scoping function in `app/services/scoping.py`
returns a SQLAlchemy filter expression for the user's allowed rows.

### Commercial detections (the only one built so far)

```python
def commercial_filter(user):
    if user.user_type == UserType.INTERNAL:
        return true()
    if user.SubscriberID is None:
        return false()  # non-internal user with no Subscriber: defensive

    c_det  = aliased(Commercial)   # the detection's commercial
    c_mine = aliased(Commercial)   # one of my Subscriber's commercials
    return exists().where(
        and_(
            c_det.CommercialID == Detection.CommercialID,
            c_det.FingerprintID.isnot(None),
            c_mine.FingerprintID == c_det.FingerprintID,
            c_mine.SubscriberID == user.SubscriberID,
            c_mine.Status == 'active',
        )
    )
```

### Song / Word detections (future)

Same shape as commercial, scoped through whatever subscription/keyword model
those use (decided in a later phase; song detections currently scope by a
matched `ClientSubscription.TargetValue` — that table will need a
`SubscriberID` field in the refactor, but the principle stays:
"my Subscriber registered a keyword that matches this detection").

### Frequency Spectrum Analysis (future)

Yet to be designed. Likely "my Subscriber subscribed to analysis for this
station."

## 5. The Terms & Conditions gate

At commercial registration, a checkbox must be checked confirming lawful
ownership / authority. The action is logged to `AuditLog` with:

- `action = 'commercial_registered_terms_agreed'`
- `actor_user_id` — the user who agreed
- `target_subscriber_id` — the Subscriber registering the commercial
- `details` — JSON containing `commercial_id`, `terms_version`,
  `terms_text_hash` (so the wording is recoverable)
- `ip_address` — captured via `_ip(request)` (existing helper)
- `created_at` — timestamp

Wording lives in `app/legal.py` as a versioned constant. Changing the wording
requires bumping the version. Every audit log entry references the version
that was in force at the time.

## 6. Defensive defaults — fail closed

Throughout the scoping engine, **unknown / unsupported user states return
`false()` (see nothing)**, never `true()`. Examples:

- Non-internal user with NULL `SubscriberID` → `false()`.
- User with an unrecognised `UserType` → `false()`.
- Subscriber in `archived`/`cancelled` status → `false()` (out of scope at
  the filter level; we won't even count the rows).

This is intentionally paranoid. An auth bug that *hides* data is recoverable
(user complains, we fix); an auth bug that *leaks* data is not (data has
already left).

## 7. Compared to v0.2 — what's gone

The v0.2 model had:

- Three viewer types (Agency, Client, Station) with different scoping
  branches.
- `AgencyClient` link table for "agency manages client" relationship.
- A station scoping branch that filtered detections by `Detection.StationID`
  (stations saw airings on their own station, regardless of who registered
  them).
- `Detection.CampaignID` as a hint, with station-aware resolution via
  `CampaignCommercial` + `CampaignStation`.
- Scenario 3 isolation (Oracle Sun vs Blue Sky each seeing only their
  campaign's station airings).

The v0.3 model removes all of this:

- One viewer type for external users (Subscriber-bound, regardless of
  Subscriber type).
- No `AgencyClient`. Subscribers stand alone.
- Stations are just another Subscriber type; they see what they registered
  (typically commercials they're providing POB-as-a-service for to their own
  advertisers), not airings on their physical station.
- No campaign-based resolution needed for visibility. The fingerprint join
  is the answer.
- Scenario 3 collapses: under v0.3, if Oracle and Blue Sky each register
  the same Clover audio independently, they each see their own
  registrations' detections (which, via FingerprintID, are the same
  detection set — both see all airings of that audio). If the user wanted
  station-segregated visibility, that's a registration choice (Oracle
  registers only for 5FM/kfm; Blue Sky only for HOT/947), not an
  authorization constraint.

This is simpler AND more correct for the real product.

## 8. Migration considerations

Anything in the codebase that referenced `ClientID` / `AgencyID` /
`StationAccountID` for authorization must move to `SubscriberID`. Anything
that joined through `AgencyClient` or inferred relationships through
campaigns must be removed. The scoping engine's current four branches
(internal / client / agency / station) collapse to two (internal / subscriber).

Tests under the old model:
- `test_scoping.py::TestCommercialScoping` — rewrite for FingerprintID
  joins; Scenario 3 collapses.
- `test_scoping.py::TestSongWordScoping` — defer until song/word UI exists.
- `test_scoping.py::TestScopingDialectSafety` — keep; add the new alias-
  based query to the compiled-dialect check.

Tests under the v0.2 model that test agency-vs-client isolation specifically
(rather than "different Subscribers don't see each other's data") will be
deleted; the property they test no longer exists as a separate concern.

## 9. Open items (not blocking)

- Per-user permission limits within a Subscriber (admin restricts a user to
  certain stations) — modelled but not built.
- Tenant self-service plan changes — decided as a feature, UI not built.
- Withdrawal of a Subscriber (whole-tenant deactivation) — Subscriber-level
  status field already exists (`SubscriberStatus`); cascading effect on
  detection visibility (any user of an archived Subscriber sees nothing)
  is enforced by §6's fail-closed defaults.
