# Deferred Work — RadioMonitor / NOCTIV Web App

Accurate as of the karaoke-transcript milestone (commercial/liveread/generic/
word-phrase/full-transcript playback all confirmed working end to end).

---

## Done — no longer deferred (cleaned up from stale entries)

- Song detection registration + viewing UI
- Word/phrase detection registration + viewing UI
- Email format validation (subscriber_service, invitation_service)
- Mobile number validation (SA format)
- Real SMTP (smtplib, STARTTLS/SSL)
- Single active session per user
- Commercial "Registered by" field
- Commercial withdrawal UI + Option-α re-staging
- Registration receipt
- Billing B1 (token infrastructure) + B2 (cost estimates wired into forms)
- Landing page (NOCTIV brand, images, logo)
- Post-login dashboard redesign (three-panel shell, NOCTIV palette)
- Karaoke transcript playback -- commercial, liveread, generic, word/phrase,
  full station transcriptions (all confirmed working)
- White dropdown background fix (color-scheme: dark on all select elements)
- Multi-select searchable station picker (shared macro, used everywhere)

---

## Outstanding -- grouped by what's next

### 1. Per-user permissions within a Subscriber
Admin restricts a subscriber_user to specific stations/campaigns within
their own Subscriber. Schema + UI not built. Design exists in
AUTHORIZATION.md section 9 -- needs revisiting given how much has changed
since.

### 2. Forgot password / password reset
No self-service reset exists. Flow: "Forgot password" link on login -> email
-> time-limited reset token (same pattern as UserInvitation) -> set new
password. Needs: PasswordResetToken table (or reuse invitation pattern),
reset email template, GET/POST /reset-password routes.

### 3. Production hardening -- remaining items
- DB password -- confirm changed from default (may already be done, verify)
- CSRF tokens on forms -- not yet implemented; SameSite=Lax cookie is the
  current mitigation
- Backup schedule / DR plan -- not yet set up
- Move logs off the same disk as the DB (if not already separate)

### 4. Stripe -- self-service credit purchasing (Phase B4)
Full design already settled. Build after first 5 paying subscribers are
confirmed -- currently using manual EFT + admin credit via /admin/tokens,
with a static instructions page at /account/credits/purchase.

Design (unchanged, still current):
- Stripe chosen over PayFast -- handles both ZAR (SA) and USD (SADC
  expansion) from one integration
- Flow: subscriber clicks "Purchase credits" -> selects bundle -> Stripe
  Checkout -> webhook confirms payment -> credits added via
  token_service.credit()
- Stripe also supports invoice/EFT for B2B clients who don't want to pay by
  card -- same webhook, same credit flow
- Prerequisite: NOCTIV must be a registered SA business entity with a
  business bank account to activate Stripe SA

### 5. Frequency Spectrum Analysis (FSA)
Separate specialised product (ICASA/SENTECH/government/security market),
built on Raspberry Pi 5 + RTL-SDR V4 hardware, last phase of the roadmap.
Not open to the general public the way other features are -- needs its own
design discussion on whether/how it surfaces in this web app at all. No
pipeline brief received yet.

### Confirmed working
Tested 2026-06-29 on an actual dashboard page below 768px -- hamburger icon
appears, menu opens/closes correctly. No fix was ever needed; the original
report was testing the landing page (different file, no hamburger by
design) rather than an authenticated dashboard page.

### 7. Button/layout consistency across dashboards -- final design pass
Different pages built across many sessions have inconsistent button
groupings, positions, and styling (some primary+ghost, some with icons,
some without, different placement relative to page header). Deliberately
deferred to one clean pass at the end rather than fixing piecemeal --
catalogue every page's action area, define one consistent pattern, apply
everywhere in a single session.

---

## Smaller / lower-priority items

- Tenant delete in UI -- deliberately omitted; risky operation, do via SQL
  with care if ever needed.
- Detection-type feature flag -> plan-tier mapping -- the four plan flags
  exist (AllowSpectrumAnalysis, AllowSongDetection, etc.) on
  SubscriptionPlanConfig, but the precise mapping of which plan gets what
  isn't finalised. Needs a management pricing decision, not engineering.
- Self-service plan-change UI for Subscriber admins -- decided as a
  feature, not built. Low priority.
- Subscriber-level withdrawal (whole-tenant archive) -- SubscriberStatus
  flag exists, no UI trigger yet. Currently done via SQL.
- Alembic adoption for schema migrations -- currently bare .sql files in
  sequence; models match the real DB; fine as-is, revisit if migration
  count grows unwieldy.
- Real logo + imagery refresh -- once any remaining copyright-cleared
  assets are ready (logo line/background already resolved).
- Pipeline: station-aware db_get_campaign_id -- non-blocking, web app
  resolves scope itself via FingerprintID; Detection.CampaignID is
  informational only.
- Library-manager content-hash diagnostics -- pipeline-side, optional,
  not blocking.

---

## Build sequence agreed (current)

1. Per-user permissions
2. Forgot password
3. Production hardening remaining items
4. Stripe (after first 5 paying subscribers)
5. FSA (pending pipeline brief + product decision on visibility)
6. Mobile hamburger retest
7. Button/layout consistency -- final design pass
