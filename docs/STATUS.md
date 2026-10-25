# Project status — 2026-10-25

✅ done and tested · 🟡 built but needs something external or a decision · ❌ not built · 👤 a human/business task

## Done ✅
**Core (design doc)**
- Multi-branch tenancy, role-per-branch RBAC, session + CSRF auth, instant deactivation.
- Catalog: SKUs, multi-barcode with pack sizes, branch prices, archive/restore, CSV import with dry run.
- Inventory: movement ledger, weighted-average cost, sorted row locks (no deadlocks or oversell, proven on Postgres), adjustments, transfers with partial receipt, stocktakes (scoped, variance reasons, uncounted-items confirmation).
- POS: barcode/SKU search, line and sale discounts with manager approval, cash/FonePay/card/wallet/bank, hold/recall, returns, shifts with variance, printing (browser HTML or ESC/POS), cash drawer.
- FonePay flow against a mock gateway: initiate → poll/callback → idempotent commit, amount mismatch → orphan, paid-but-no-stock → conflict, expiry job, reconciliation command.
- Offline PWA: cached catalog/config/login, queued cash sales, idempotent sync, provisional slips (branch series), 502/503 treated as offline.
- Dashboard (org) and branch report with per-branch-timezone "today".
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

**Tests:** 114 backend tests (all pass on SQLite and embedded Postgres), 68 frontend pricing-parity tests, manual browser testing of every screen (two passes).

## Waiting on something external 🟡
| Item | Waiting for | Where |
|---|---|---|
| Real FonePay integration | Bank's official API docs and sandbox credentials. `HttpFonepayGateway` is an unverified skeleton; `settlement_report` isn't implemented. | `sales/fonepay.py` |
| Live CBMS | One run against IRD's **test server** to confirm the payload format (field names were taken from IRD's API doc via a mirror; the official PDF host was unreachable). | `compliance/cbms.py`, Settings |
| Offline numbering in branch mode | IRD OK for "provisional slip, invoice issued on sync" (ird-questions D). Otherwise use per-register series. | Settings |
| Assumptions A–I | IRD / tax practitioner answers | `docs/ird-questions.md` |
| VAT 5% scope | Which goods it applies to; assign products to VAT5 | Tax categories |
| Printer model | Pick hardware; test the ESC/POS bridge and drawer | Register settings |

## Not built ❌ (candidates for next sessions)
1. **Annex 5 / Annex 13** reports: formats pending IRD.
2. **Foreign-currency sales** at the NRB rate (the POS is NPR-only).
3. **Excel (.xlsx) import**: CSV only today (openpyxl is already installed).
4. **Excise rule** "industries can't sell to non-VAT retailers" (unverified): flag or block in checkout if confirmed.
5. **CI/CD pipeline** (GitHub Actions: tests on Postgres, build, deploy) and staging/prod environments.
6. **Load test** for checkout P95 < 500 ms (design doc §22).
7. **Rate limiting in Caddy** (only the nginx config has it).
8. **Postgres row-level security** (optional, design doc §6).
9. **Offline returns** with manager override (deliberately disabled, per the design doc).
10. **UI polish**: phone layout not checked; a restyle is in progress outside the build sessions (Icon.tsx, Login, ui, Dashboard, Pos, Admin edited 16:33–16:45 on 2026-10-25, plus a new `auth/roles` endpoint).
11. **Git**: the project is not a git repository yet. Initialise it and tag releases (IRD listing covers an exact version).

## Business tasks 👤 (payment.md Steps 0, 1, 6, 7, 8)
Ask IRD the Step 0 questions → decide the target segment and Nepal hosting → freeze a version and apply for listing with `generate_listing_evidence` → onboard each customer (`docs/onboarding-checklist.md`) → read the budget every Jestha 15 and IRD notices monthly.

## Known limitations / risks
- SQLite (dev) has no DB triggers or row locks; always run `scripts/test_with_postgres.py` before release.
- If a checkout response is lost and the till falls back to offline with the same key, the server returns the original sale. In register-series mode the offline number then shows as a gap to reconcile.
- `nepali-datetime` future-year calendar data must be kept updated.
- Demo database contains test transactions; use `flush` + `seed_demo` to reset (dev only, never in production).

## Suggested next session
1. `git init`, commit, tag `v0.1.0`.
2. Run a CBMS test sync against IRD's test server once you have the URL and credentials.
3. Build the Excel import and the CI pipeline (tests on Postgres).
4. Coordinate with the in-progress UI restyle, then do a phone-width pass on the POS.
