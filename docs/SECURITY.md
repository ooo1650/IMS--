# Security

What protects the system, where it lives, and what is still open. Checked 2026-10-26.

## Controls in place
| Area | Control | Where |
|---|---|---|
| Authentication | Session cookies (HttpOnly, Secure, SameSite=Lax, 12 h) + CSRF; no tokens in JS | `ims/settings.py` |
| Brute force | Lockout after failed sign-ins per username or per IP within a window (defaults 5 / 20 in 15 min; set in the operator console), counted from the audit log so it holds across workers and restarts. The 429 says how long is left. Manager passwords typed at the till for approvals count toward the same lockout. | `accounts/views.py` `login_locked_out`, `accounts/permissions.py` `verify_approver` |
| Customer-display tablet | No login. A one-time 8-character code (31^8, 10 min) gives a 256-bit token, stored hashed, that can only read one till's customer screen (items, total, payment QR; no buyer, staff or cost data). Replaced or disconnected = revoked at once. The QR must be an inline PNG, never a URL. | `sales/display.py`, `sales/tests/test_display.py` |
| Input validation | Request numbers go through `ims.api.to_int` with ranges; serializers and models carry min/max validators; a late database overflow is a 400, never a 500. `ims/test_input_numbers.py` checks every write endpoint. Checkout refuses customers and idempotency keys of other branches/businesses. | `ims/api.py`, `sales/services.py` |
| Error replies | Every `/api/` error is JSON with a plain message (also Django's CSRF/404/500 pages and nginx's 429/413), so no stack traces or server pages reach users | `ims/errors.py`, `deploy/nginx/snippets/ims-json-errors.conf` |
| Passwords | PBKDF2 (Django default); min 10 chars, not common, not numeric-only, not similar to the username | `AUTH_PASSWORD_VALIDATORS` |
| Offline sign-in | PBKDF2-SHA256, 150k iterations, salted, max 24 h old | `frontend/src/lib/offline.ts` |
| Authorization | Deny-by-default role → permission map, checked per branch on every endpoint; branch isolation via `BranchScopedMixin` | `accounts/roles.py`, `accounts/permissions.py`, `ims/api.py` |
| Client IP | Taken `IMS_TRUSTED_PROXIES` entries from the right of X-Forwarded-For, so callers can't fake it in the audit log or dodge the IP lockout | `ims/net.py` |
| Audit | Every write to master data and documents diffed; sign-ins, lockouts, prints, voids logged; append-only | `admin_extras/audit.py` |
| Tamper resistance | Issued invoices, notes, receipts, print log, audit log are append-only (model guard + Postgres triggers) | `compliance/appendonly.py`, migration `compliance/0003` |
| Database privilege | App connects as `ims_app`: SELECT/INSERT/UPDATE/DELETE only. It cannot TRUNCATE, drop or alter tables, disable triggers or create tables. Verified on PostgreSQL. | `ensure_db_roles`, `docker-compose.yml` |
| Secrets | Only in `.env` (git-ignored, `chmod 600`); compose refuses to start without them; CBMS password encrypted (Fernet) | `.env.example` |
| Payments | FonePay off by default in production; mock "mark paid" endpoint only exists when explicitly enabled; callbacks HMAC-SHA512-verified with the branch's own secret (an empty secret never verifies) and re-checked with the gateway; mode, API address and merchant credentials are in the database (secrets encrypted); demo mode needs `IMS_ALLOW_FONEPAY_MOCK`, and the API address must be https on `IMS_FONEPAY_HOSTS` | `sales/fonepay.py`, `ims/settings.py` |
| Exports | Text cells can't start a spreadsheet formula (CSV/XLSX injection) | `compliance/exports.py` |
| Transport | HTTPS via nginx with Let's Encrypt (certbot service, auto-renewed; self-signed for local names), TLS 1.2/1.3 only, HSTS incl. subdomains, port 80 redirects to https | `deploy/nginx/`, `deploy/certbot-loop.sh` |
| Browser | CSP (no inline scripts, no eval, self only), X-Frame-Options DENY, nosniff, Referrer-Policy, COOP, Permissions-Policy limits camera/USB/serial to the app | `deploy/nginx/templates/ims-headers.inc.template` |
| Admin site | `/django-admin` reachable only from `IMS_ADMIN_ALLOW` networks | `deploy/nginx/40-ims.sh` |
| Containers | Non-root app user, read-only root filesystems, all capabilities dropped, `no-new-privileges`, database on an internal network with no published port, 10 MB request limit | `backend/Dockerfile`, `docker-compose.yml` |
| Dependencies | `npm audit`: 0 vulnerabilities; `pip-audit`: none known (2026-10-26) | re-run before each release |
| Backups | Nightly dumps with checksums, ~7-year retention, weekly automated restore drill; weekly base backups + gzipped WAL archive for point-in-time recovery; optional gpg encryption (`BACKUP_PASSPHRASE`) and off-site copy (`BACKUP_OFFSITE_DIR`) | `deploy/backup.sh`, `deploy/restore-drill.sh`, `deploy/pitr-prepare.sh` |

| Businesses | Every business-owned query filters on the active business; another business's product/supplier/category/branch IDs are refused; CBMS posts each business with its own credentials, and bad credentials pause only that business | `tenancy/tests/test_multibusiness.py` |
| Operator console | Platform admins only (`IsPlatformAdmin`), emailed code always required, IP allowlist `IMS_CONSOLE_ALLOW`, `/api/v1/platform/` returns 404 on the IMS host, every action audited | `admin_extras/platform.py`, `deploy/nginx/` |

## Open items
1. **Two-factor sign-in**: done as an emailed 6-digit code (`accounts/otp.py`), required for `IMS_2FA_REQUIRED_ROLES` (default SuperAdmin, OrgAdmin, BranchManager), opt-in for others. Not built: authenticator-app (TOTP) codes, which would work when email is down.
2. **Backups leave the VM**: done when `BACKUP_OFFSITE_DIR` points at a mounted second disk/share (docs/DEPLOYMENT.md "Backups"); still needs that mount set up on the server.
3. **Backup encryption at rest**: done with `BACKUP_PASSPHRASE` (gpg AES-256 for dumps and base backups; the weekly drill proves it decrypts). Off by default: set it. The WAL archive volume itself is not encrypted (it sits next to the live database).
4. **Image pinning by digest** and rebuilding monthly for OS security patches; add a scanner (Trivy/Grype) in CI once CI exists.
5. **Session revocation**: done. A password change or reset, "Sign out other devices" (My account) and the admin's "Sign out of every device" bump `User.session_epoch`, which is part of the session hash.
6. **Proxy-level rate limits**: done in nginx (per client IP: sign-in/reset 30/min burst 20, POS 20/s burst 40, other API 50/s burst 100). Several tills behind one office NAT share a limit; raise the numbers if a busy branch sees 429s.
7. **Penetration test** before go-live, and a review of the FonePay integration once the bank's API docs arrive.
8. **Login CSRF**: fixed. Sign-in, code, forgot and reset endpoints are `csrf_protect`ed.
9. **Hosted split (Render) client IPs**: with `IMS_TRUSTED_PROXIES=2`, a caller hitting the `onrender.com` address directly can choose the IP the app sees (per-IP lockout, audit IPs, `IMS_PLATFORM_ALLOW`). The per-account lockout still holds. Acceptable for the temporary demo hosting; never enable `IMS_TEST_LOGIN` there. The Docker deployment is not affected (only nginx can reach the backend).
10. **Device trust**: offline sign-in and the offline sales queue live in the till's browser; tills need OS accounts, disk encryption and screen locks.

Fixed 2026-11-09 (review): checkout accepted any customer ID (another business's customer name came back in the response); idempotency keys were looked up across branches (a known key returned another branch's sale); manager-approval passwords typed at the register bypassed the sign-in lockout. Each has a regression test in `sales/tests/test_review_fixes.py`.

## Reporting a problem
Tell the system owner privately; don't open a public issue with exploit details.
