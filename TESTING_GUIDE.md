# RadioMonitor — Testing Guide (what to test right now)

This reflects the app **as built today**. It tells you what works (test it),
what exists but is limited (test with caveats), and what isn't built yet
(don't expect it). Test **logic and flow only** — ignore visual polish, that's
a deliberate later phase.

---

## How to think about the app's current state

The app has a complete **foundation**: you can log in, manage who exists
(users), manage the organizations (clients/agencies/stations), and there's a
proven **engine** under the hood that decides who may see which airings. What
does NOT exist yet is the **screens that show airings** and the **screens to
set up campaigns**. So today you're testing the plumbing and the admin side,
not the end-product detection views.

---

## 1. WORKS NOW — test these properly

### Authentication
- **Log in** at `/login` with your internal admin. Correct credentials → land
  on `/dashboard`. Wrong password → friendly error, no crash.
- **Log out** → returns you to a logged-out state; visiting `/dashboard` after
  logout should bounce you to `/login`.
- **Protected pages**: while logged out, going straight to `/dashboard` or any
  `/admin/...` URL should redirect to `/login`, not show the page.

### Client management (internal only)
- `/admin/clients` lists clients. **New client**: name + plan (dropdown) +
  status. Expect: only real plans appear in the dropdown; only the four valid
  statuses appear. Submitting creates the client and shows it in the list with
  an auto-generated slug.
- **Edit** a client: change its name/plan/status; changes persist.
- Try to break it on purpose: it should be hard to. The dropdowns only offer
  valid values, so the FK/CHECK errors you hit doing this by hand in SQL
  shouldn't be reachable through the form.

### Agency management (internal only)
- `/admin/agencies` — list, **New agency** (name + optional contacts + active
  checkbox), **Edit**. Same shape as clients. Deactivating (unchecking Active)
  should persist as inactive.

### Station account management (internal only)
- `/admin/stations` — list, **New station account**, **Edit**.
- Expect: the "New" form's station dropdown only offers stations that don't
  already have an account. A station already accounted-for won't appear.
- If the dropdown is **empty**, that means the pipeline's `Station` table has no
  rows (or all are taken) — see §3 note on pipeline data.

### Invitation flow (internal + tenant admins)
- `/admin/invitations` → **New invitation**. Pick a role; for client/agency
  roles, pick the matching client/agency from the dropdown (leave the other as
  "none"). Submit.
- Expect: the invite link is printed to the **console** where `python main.py`
  runs (email is a stub — see §2). Copy it.
- Open the link in an **incognito window**, set a password → the new user is
  created and auto-logged-in to their dashboard.
- The new user can then log in normally at `/login`.

### Authorization boundaries (the security model)
- A **client_admin** or **agency_admin** can reach `/admin/invitations` but
  should NOT be able to reach `/admin/clients`, `/admin/agencies`, or
  `/admin/stations` (those are internal-only → expect a redirect/refusal).
- A tenant admin inviting a user can only invite into **their own** org.
- These are worth poking at: log in as a non-internal user and try to reach
  internal-only URLs directly.

---

## 2. EXISTS BUT LIMITED — test, but expect the caveat

### Email = console stub
Invitations don't send real email. The link appears in the server console. This
is intentional; real SMTP is deferred until just before real onboarding. So
"test email delivery" is not applicable yet — test that the link is generated
and that it works when you paste it.

### HTMX / Alpine.js = placeholders
The dynamic-interactivity libraries aren't wired yet. The invitation form, for
example, shows BOTH the client and agency dropdowns at once (with labels saying
which is for which role) rather than showing/hiding based on the role you pick.
That's expected — the server validates the right pairing regardless. Don't test
"the form hides the agency box when I pick a client role"; that behaviour isn't
wired.

### No delete for tenants
You can create and edit clients/agencies/stations, but there's no "delete"
button — deleting a tenant that may have data is risky, so it was left out on
purpose. Don't expect to remove a client through the UI yet.

### Self-service billing / plan changes by tenants
Decided as a feature (a tenant admin may change their own plan) but the UI for
a tenant to manage their own org isn't built — only internal-side management
exists. Don't expect a client_admin to have a "change my plan" screen yet.

---

## 3. NOT BUILT YET — don't expect these

These are coming; their absence is not a bug.

- **Detection-viewing screens.** There is NO page yet where a client/agency/
  station sees their airings. The engine that decides visibility exists and is
  tested, but the screens that display detections are the next major build.
  The dashboard's "Recent detections / Station status / Reports" cards are
  placeholders.
- **Campaign management.** No UI to create campaigns, assign an agency, or link
  commercials/stations to a campaign. Until this exists, there's no way through
  the app to set up the data the detection views would show.
- **Subscription management** (songs/words keyword rules). No UI to add the
  keyword/title subscriptions that scope song/word detections.
- **Per-user permission limits** within an org (restrict a user to certain
  stations/campaigns). Modelled as a future item, not built.
- **Reports, exports, spectrum/transcription views** — all later phases.
- **Real-time anything, notifications, SMS** — not in scope yet.

---

## 4. What "success" looks like for today's testing

You've validated the current build if:
1. You can log in/out, and protected pages are actually protected.
2. You can create + edit a client, an agency, and (if stations exist) a station
   account through the UI, with the forms preventing invalid plan/status values.
3. You can invite a user (internal creating any; a tenant admin only within
   their org), copy the console link, accept it, and that user lands logged-in.
4. Non-internal users cannot reach internal-only admin pages.
5. Nothing 500s; the console shows no tracebacks during normal use.

If all five hold, the foundation is solid and we move to the visible product:
campaign management, then detection-viewing screens.

---

## 5. Keep a notes file as you go

Jot anything confusing, missing-context, or awkward into a running list (a
`UX_NOTES.md` or just notepad). NOT to fix now — to inform the design phase
later. "This screen doesn't explain what a station account is", "no way back to
dashboard from here", "the list should show how many users a client has" — that
kind of thing. It becomes the design brief when we get to polish.
