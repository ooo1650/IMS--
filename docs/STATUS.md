# Project status — 2026-11-09

✅ done and tested · 🟡 built but needs something external or a decision · ❌ not built · 👤 a human/business task · ⏸ paused

> **⏸ IRD compliance work is paused (decided 2026-11-06).** The business is VAT-registered but is not applying for IRD software listing any time soon. What's built stays and keeps working: VAT invoices, numbering, append-only records, the VAT books and exports. CBMS posting is **off** (Tax & IRD → Settings). New IRD work is on hold: everything marked ⏸ below. Don't start it unless the owner reopens it.

## Done ✅
**Core (design doc)**
- Multi-branch tenancy, role-per-branch RBAC, session + CSRF auth, instant deactivation.
- Catalog: SKUs, multi-barcode with pack sizes, branch prices, archive/restore, CSV import with dry run, product photos (resized to WebP on upload, content-addressed so they cache forever; shown on POS tiles, lists and detail).
- POS speed: catalog shown from IndexedDB at once and refreshed in the background, re-downloaded only when its version changes; barcode/SKU lookup is a hash map; search filters a precomputed index at low priority; photo thumbnails are prefetched in idle time into a service-worker cache (offline too) and never block a sale.
- Inventory: movement ledger, weighted-average cost, sorted row locks (no deadlocks or oversell, proven on Postgres), adjustments, transfers with partial receipt, stocktakes (scoped, variance reasons, uncounted-items confirmation).
- Security & deployment: Docker Compose stack (db, one-shot migrate, backend, worker scheduler, nginx web with Let's Encrypt (certbot) and rate limits, backup + restore drill); least-privilege DB role `ims_app`; login lockout; proxy-safe client IP; CSP and security headers; FonePay disabled by default in production; CSV/XLSX formula-injection guard; health endpoint. Open items in `docs/SECURITY.md` (2FA, backup encryption, …).
- Hardware (browser-only, no install; Chrome/Edge over https): ESC/POS receipt printers over WebUSB or WebSerial, 58/80 mm, invoice-number barcode on receipts, cash drawer via the printer, scan-anywhere for USB/Bluetooth scanners, camera scanning (BarcodeDetector, ZXing fallback), barcode/price labels (A4 sheets, label rolls), in-store EAN-13 generation, customer display (second screen + serial 2×20 pole display). Per-till settings live in IndexedDB. Weighing scales are deferred (needs fractional quantities).
- POS: barcode/SKU search, line and sale discounts with manager approval, cash/FonePay/card/wallet/bank, hold/recall, returns, shifts with variance, printing (browser HTML or ESC/POS), cash drawer.
- FonePay flow against a mock gateway: initiate → poll/callback → idempotent commit, amount mismatch → orphan, paid-but-no-stock → conflict, expiry job, reconciliation command.
- FonePay merchant **per branch** (Admin → Branches & registers → FonePay): merchant code, username, encrypted password and secret, on/off per branch. QR, status checks, callback signatures and reconciliation use the branch's merchant. FonePay mode and API address are stored in the database too (`FonepaySettings`, same screen); no FonePay env vars remain.
- Offline PWA: cached catalog/config/login, queued cash sales, idempotent sync, provisional slips (branch series), 502/503 treated as offline.
- Dashboard (org) and branch report with per-branch-timezone "today".
- Sales analytics on both report screens (`reporting/analytics.py`, `components/SalesAnalytics.tsx`): 7/30/90-day period vs the previous one, KPI tiles with change and sparklines, daily revenue/profit/sales trend with crosshair, revenue by branch and category (with margin), payment mix, weekday × hour heatmap, top products with gross profit; every chart has a table view.
- Audit log and admin screens (users/roles, branches/registers).

**IRD compliance (payment.md)**: see `docs/compliance-checklist.md` for the item-by-item list.
- Full/abbreviated tax invoices, buyer PAN rules, per-branch per-FY gapless numbering, BS + AD dates.
- Print-once with numbered copies and delivery copies; cancel with reason; credit/debit notes.
- Append-only enforced by the model and by **PostgreSQL triggers**; audit trail with before/after values.
- Tax categories with effective-dated rates, zero vs exempt, excise, HST/ESF, blocked input credit, digital-payment VAT discount (off).
- CBMS outbox, sync, retry, pause on bad credentials, dashboard, turnover meter.
- Sales and purchase books, return registers, VAT return working, cancelled bills, stock vs purchase-book reconciliation, CBMS report, audit trail. Excel/XML/PDF/CSV export and accountant zip. Auditor DB views.
- Purchase VAT capture, purchase returns, supplier PAN.
- Backup script (6-year retention) and restore drill, both run successfully against Postgres.
- Listing-evidence generator, user manual (in-app Help), IRD question log, hosting and acceptance templates.

**Auth:** email or username sign-in, emailed sign-in codes (required for admins and branch managers), forgot/reset password, My account page, sign out everywhere.

**Tests:** 232 backend tests and 98 frontend tests, all green on 2026-11-09 (SQLite and embedded Postgres); CI in `.github/workflows/ci.yml` (backend on Postgres + ruff, frontend lint/test/build, console build + shared-file drift check, Docker image build). Manual browser testing of every screen (two passes, October).

**Review fixes (2026-11-09)**: customer and idempotency-key scoping at checkout, manager-approval lockout, return-vs-cancel race, shift lock at checkout/close, FonePay expiry job robustness, debit-note VAT rate required, offline-sync stalls (one rejected sale no longer blocks the queue; offline shift closes aren't dropped) with an "unsynced sales" panel on the till, 400s instead of 500s on bad numbers, threaded gunicorn + FonePay poll throttle. Tests in `sales/tests/test_review_fixes.py` and `frontend/src/lib/sync.test.ts`.

**Backups (2026-11-09)**: WAL archiving was broken in Docker (root-owned archive volume, `pg_wal` growing without limit); fixed. Weekly base backups + gzipped WAL give working point-in-time recovery (`deploy/pitr-prepare.sh`), old WAL is pruned, and dumps/base backups can be encrypted and copied off-site. Excel (.xlsx) import added next to CSV.

**Multi-business + operator console (2026-11-07)**
- Several separately registered businesses per install (one owner): each its own PAN, catalog, suppliers, customers, invoice series, CBMS login, reports, shop rules and FonePay; staff can hold roles in more than one; the app has a business switcher.
- Operator console (`console/`, its own project: :8443, `IMS_CONSOLE_DOMAIN`, or another server): create businesses with first branch + admin (emailed setup code), branches/registers, people with roles across businesses, rules, tax/CBMS, FonePay, system settings (email with test send, sign-in rules), jobs (CBMS sync, stock reconcile, …), audit.
- Settings moved from `.env` to the database (email, sign-in rules, shop rules); old values copied by migration. `manage.py create_platform_admin` is the only command needed on a new install.

## Waiting on something external 🟡
| Item | Waiting for | Where |
|---|---|---|
| Real FonePay integration | Bank's official API docs and sandbox credentials. `HttpFonepayGateway` is an unverified skeleton; `settlement_report` isn't implemented. | `sales/fonepay.py` |
| ⏸ Live CBMS | One run against IRD's **test server** to confirm the payload format (field names were taken from IRD's API doc via a mirror; the official PDF host was unreachable). | `compliance/cbms.py`, Settings |
| ⏸ Offline numbering in branch mode | IRD OK for "provisional slip, invoice issued on sync" (ird-questions D). Otherwise use per-register series. | Settings |
| ⏸ Assumptions A–I | IRD / tax practitioner answers | `docs/ird-questions.md` |
| VAT 5% scope | Which goods it applies to; assign products to VAT5 | Tax categories |
| Printer model | Pick hardware and test on a real till: WebUSB claim (Linux `usblp` / Windows WinUSB), drawer pulse, 58 vs 80 mm, pole display command set | POS → Devices |

## Not built ❌ (candidates for next sessions)
1. ⏸ **Annex 5 / Annex 13** reports: formats pending IRD.
2. **Foreign-currency sales** at the NRB rate (the POS is NPR-only).
3. ~~Excel (.xlsx) import~~: done (2026-11-09).
4. ⏸ **Excise rule** "industries can't sell to non-VAT retailers" (unverified): flag or block in checkout if confirmed.
5. **CD and environments**: CI exists (2026-11-09; not yet seen running on GitHub). Deploy automation and a permanent staging server don't.
6. **Load test** for checkout P95 < 500 ms (design doc §22).
7. ~~Rate limiting~~: done in nginx (2026-11-07, Caddy replaced).
8. **Postgres row-level security** (optional, design doc §6).
9. **Offline returns** with manager override (deliberately disabled, per the design doc).
10. **UI polish**: phone layout not checked. `Pos.tsx` (~1,400 lines) should be split into hooks/components with browser tests before more POS features land.
12. **Weighing scales / fractional quantities**: quantities are integers everywhere (stock, sales, pricing vectors); loose goods need a units decision first.
11. **Git**: commit and tag releases (`v0.1.0`).

## Business tasks 👤 (payment.md Steps 0, 1, 6, 7, 8): ⏸ paused
Ask IRD the Step 0 questions → decide the target segment and Nepal hosting → freeze a version and apply for listing with `generate_listing_evidence` → onboard each customer (`docs/onboarding-checklist.md`) → read the budget every Jestha 15 and IRD notices monthly.

## Known limitations / risks
- SQLite (dev) has no DB triggers or row locks; always run `scripts/test_with_postgres.py` before release.
- Docker deployment: full local staging run on 2026-11-09 (build, migrate, `ims_app` role, seed, checkout through nginx, console, backups, restore drill, point-in-time restore). Not yet run on the production VM with a public domain (Let's Encrypt untested).
- If a checkout response is lost and the till falls back to offline with the same key, the server returns the original sale. In register-series mode the offline number then shows as a gap to reconcile.
- `nepali-datetime` future-year calendar data must be kept updated.
- Demo database contains test transactions; use `flush` + `seed_demo` to reset (dev only, never in production).

## Suggested next session
1. Commit, push (so CI runs for the first time) and tag `v0.1.0`.
2. On every running Docker install: `docker compose up -d` with this version, confirm `pg_stat_archiver` archives, set `BACKUP_PASSPHRASE` and an off-site mount, take a base backup.
3. Phone-width pass, then split `Pos.tsx` and add browser tests (Playwright) for checkout and offline sync.
4. Decide fractional quantities for the grocery (weighing scales).
