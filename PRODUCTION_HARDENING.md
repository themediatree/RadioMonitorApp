# NOCTIV RadioMonitor -- Production Hardening Checklist

Updated to reflect actual confirmed state, not the original draft.

---

## Confirmed done

1. **Strong JWT secret** -- confirmed set (64-char generated secret), startup
   warning removed.
2. **HTTPS** -- live via Cloudflare Tunnel at https://noctiv.co.za. No port
   forwarding, no router exposure.
3. **SESSION_COOKIE_SECURE=true** -- confirmed set, working correctly over
   HTTPS.
4. **BASE_URL=https://noctiv.co.za** -- confirmed set, invite/reset links
   correctly use the real domain.
5. **DEBUG=false, APP_ENV=production** -- confirmed set, /docs and /redoc
   disabled.
6. **Security headers** -- Grade A on securityheaders.com (HSTS, CSP,
   X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
   all present). Grade capped at A (not A+) due to 'unsafe-inline' in CSP --
   resolving this requires moving inline JS to external files, deferred to
   the design pass.
7. **Login rate limiting** -- 5 failed attempts per IP per 10 minutes
   triggers a 15-minute lockout.
8. **Single active session per user** -- SessionVersion mechanism, confirmed
   working.
9. **.env file** -- contains real secrets, not committed to any repo (no git
   in use for this project -- zips are the deployment artifact).

---

## Verify now

### DB password rotation
**Confirmed rotated.** Tested 2026-06-29: `sqlcmd` login with the old
default password returns "Login failed" -- password has been changed.
Nothing further to do.

---

## Built this session -- enable when ready

### Backup schedule
`scripts/backup_database.ps1` -- full SQL Server backup with 30-day
retention, plus a mirror of `audio_archive` (these files cannot be
regenerated if lost). Reads the DB password from `.env` rather than
hardcoding it.

**Note:** SQL Server Express does not support backup compression (an
Enterprise/Standard-only feature) -- `WITH COMPRESSION` was removed after
testing confirmed Express rejects it. Backups will be roughly the size of
the live database; factor this into disk space planning, especially since
`D:\Backups` currently shares a disk with the database itself (see caveat
below).

Registered as `RadioMonitor-Backup`, daily at 02:00, disabled by default:
```powershell
Enable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-Backup"
```

**Important caveat:** `D:\Backups` is currently the same physical disk as
the database it's backing up. This protects against accidental deletion or
corruption, but NOT against disk failure. Strongly recommend moving
`D:\Backups` to a separate physical disk or network share before relying on
this for disaster recovery. Update `$BackupDir` in the script once that's
in place.

### Log rotation
Registered as `RadioMonitor-LogRotation`, weekly Sunday 03:00, disabled by
default. Rotates any `.log` file over 50MB, deletes rotated logs older than
30 days.
```powershell
Enable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-LogRotation"
```

---

## Deliberately deferred

### CSRF tokens
Current mitigation: `SameSite=Lax` on the session cookie, which blocks the
classic cross-site POST attack vector in all modern browsers. `Lax` (not
`Strict`) is intentional -- it allows invitation/password-reset links clicked
from an email client to still carry the session correctly.

Full synchronizer-token CSRF (a hidden `_csrf` field on every form, validated
server-side) would add real defense-in-depth but touches every form in the
app -- deliberately bundled into the final design pass rather than done
twice (once now, once again when forms are restyled).

### CSP 'unsafe-inline'
Grade A (not A+) on securityheaders.com because of inline `<script>` tags
throughout the app. Fixing this means moving all inline JS to external
`.js` files -- a real but cosmetic-adjacent change, bundled into the design
pass.

---

## Status summary

| Item | Status |
|------|--------|
| Strong JWT secret | Done |
| HTTPS (Cloudflare Tunnel) | Done |
| Secure cookie flag | Done |
| BASE_URL correct | Done |
| DEBUG=false / APP_ENV=production | Done |
| Security headers | Done (Grade A) |
| Login rate limiting | Done |
| Single session enforcement | Done |
| DB password rotated | Done (confirmed 2026-06-29) |
| Backup schedule | Done -- confirmed working |
| Log rotation | Built -- not yet manually triggered/verified |
| Activation task | Done -- confirmed working (LastTaskResult: 0) |
| Backups on separate disk | Not yet -- same volume as DB currently |
| CSRF tokens (full) | Deferred to design pass |
| CSP unsafe-inline removal | Deferred to design pass |
