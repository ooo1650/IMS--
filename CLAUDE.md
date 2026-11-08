# IMS — context for the next session

Multi-branch **Inventory Management + POS** for **several separately registered businesses with one owner** (e.g. a grocery chain and a shoe shop: each its own PAN, catalog, invoices, reports and branches; ~10 branches in Nepal), made **Nepal IRD-compliant**. An **operator console** (`console/`, its own project; served on :8443 or `IMS_CONSOLE_DOMAIN`, or hosted elsewhere — `console/README.md`) creates businesses, branches and admin accounts and replaces most `.env` settings and CLI jobs.
Source specs: `ims-design-doc (3).md` (architecture) and `payment.md` (IRD/VAT/CBMS rules). Both are in the repo root.

**IRD compliance work is paused (2026-11-06).** The business is VAT-registered but not applying for IRD listing soon. Keep the existing VAT/invoice/append-only behaviour working, keep CBMS off, and don't start new IRD features (Annex 5/13, live CBMS, listing evidence) unless the owner asks.

**Read these before changing anything:**

| Doc | What it covers |
|---|---|
| `docs/STATUS.md` | What's built, what's left, open decisions |
| `docs/ARCHITECTURE.md` | Apps, request flow, the key flows (checkout, offline, CBMS) |
| `docs/CODE-GUIDE.md` | File-by-file tour, how to make common changes, conventions |
| `docs/DATABASE.md` | Every table, constraints, append-only triggers, ERD |
| `docs/API.md` | Every endpoint, permissions, request/response shapes |
| `docs/ird-questions.md` | Assumptions waiting on IRD/accountant answers |
| `docs/compliance-checklist.md` | payment.md item → status |

## Stack
- **backend/**: Django 5.2 + DRF, session auth + CSRF (not JWT), PostgreSQL in prod (SQLite fallback for dev). Python venv at `backend/.venv`.
- **frontend/**: React 19 + TypeScript + Vite PWA (react-router 7, idb, qrcode). Dev server proxies `/api` → `127.0.0.1:8000`.
- Apps: `tenancy, accounts, catalog, inventory, sales, purchasing, reporting, admin_extras, compliance`.

## Run / test
```bash
cd backend && .venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo   # 2 demo businesses + `operator` (refuses if data exists; `flush` first)
.venv/bin/python manage.py runserver 127.0.0.1:8000
cd frontend && npm run dev            # http://localhost:5173  (Vite binds IPv6 localhost, not 127.0.0.1)
cd console && npm install && npm run dev   # operator console on http://localhost:5174 (sign in as `operator`)
cd backend && .venv/bin/python manage.py create_platform_admin --username op --email you@x.com   # first operator on a real install
# demo logins (password demo-pass-123): admin (owner, both businesses), manager, cashier, cashier_pkr, clerk, viewer, manager_brt, manager_shoes; operator (console)

cd backend && .venv/bin/python manage.py test                 # 201 tests, SQLite (4 Postgres-only skipped)
.venv/bin/python scripts/test_with_postgres.py                # same suite on embedded Postgres (pgserver) — triggers + concurrency
cd frontend && npm test && npx tsc -b && npm run build   # 94 tests
cd console && npx tsc -b && npm run build              # console (src/shared/ = copies from frontend/src: `npm run sync-shared`)
```
Last verified: all green on 2026-10-26 (SQLite and embedded Postgres).
Hosted split (Vercel frontends + Render backend + Supabase DB): `docs/DEPLOYMENT.md` last section; `vercel.json` in `frontend/` and `console/`, backend guard `ims/platform_guard.py`.
Docker: `cp .env.example .env && docker compose up -d --build` (see `docs/DEPLOYMENT.md`; not yet run on a real Docker host).

## Invariants (don't break these)
1. **Money is integer paisa** (`BigIntegerField`), never float. Rates are `Decimal`. Pricing math uses exact `Fraction`, rounded half-up only when stored.
2. **Tax = discount before tax, sum per rate, round once** (`sales/pricing.py`). `frontend/src/lib/pricing.ts` is a line-for-line port. **Change both**, then regenerate the shared vectors with `backend/scripts/gen_pricing_vectors.py`. Check old vectors still match first.
3. **Lock order**: `InventoryItem` rows are locked sorted by `product_id` (`inventory.services.lock_items`), then the `DocumentSequence` row. Use `select_for_update(of=("self",))` whenever you `select_related` a nullable FK (Postgres rejects FOR UPDATE on outer joins; SQLite hides this).
4. **Gapless numbering** only via `sales.numbering.allocate` (a locked row, never a DB sequence). Default series is **per branch + fiscal year** (`KTM-2083-84-000001`). Prefixes: `CN-`, `DN-`, `PR-`.
5. **Issued tax documents are append-only**: `compliance.appendonly.AppendOnlyModel` (model guard) + Postgres triggers (`compliance/migrations/0003`). After issue, only cancellation/review columns may change. Never `.update()` an issued sale; seed/backfill code must set values *before* issue (e.g. `checkout_cash(now=...)`).
6. **Every stock change writes a `StockMovement`**. `quantity_on_hand` is a cache (`manage.py reconcile_stock` verifies it).
7. **Tenant isolation, two levels.** *Business* (`Organization`): catalog, suppliers, customers, settings, roles and reports belong to one business; filter on `request.org` (`ims.api.OrgScopedMixin`, `OrgFieldsMixin` for related fields) and pass the business explicitly (`get_settings(org)`, `rules_for(org)`, `fonepay.server_settings(org)`; there is no "first org" fallback). *Branch*: `ims.api.BranchScopedMixin`. `tenancy.middleware` sets `request.branch` (`X-Branch-Id`/session) and `request.org` (the branch's business, else `X-Org-Id`/session). A business-wide role (`UserBranchRole` with `branch=NULL`) applies only inside its own business. `inventory.services.lock_items` and `price_cart` refuse another business's products. `tenancy/tests/test_multibusiness.py` is the isolation contract.
8. **BS dates** come from `compliance.bs` (nepali-datetime). Fiscal year label `2083-84`, CBMS format `2083.084`, dates `YYYY.MM.DD`. 2026-10-25 = 2083.07.08.
9. **CBMS** is an outbox (`CbmsPost`) written in the same transaction as the document; `manage.py cbms_sync` posts it. `mock://` base URL = in-process double.
10a. **Operator console** (`/api/v1/platform/…`, `admin_extras/platform.py`): `User.is_platform_admin` only, always needs an emailed code, holds no business roles. nginx returns 404 for `/api/v1/platform/` on the IMS host and serves it only on the console server block (`IMS_CONSOLE_ALLOW`).
10b. **Settings live in the database**: SMTP/sign-in rules in `admin_extras.SystemSettings` (email sent by `admin_extras.mail.DatabaseSmtpBackend`), shop rules in `tenancy.BusinessRules`, tax in `ComplianceSettings`, FonePay in `FonepaySettings`/`FonepayMerchant`. Only secrets and security guard rails stay in `.env`.
10. **The running app uses the least-privilege DB role `ims_app`** (row access only). Migrations run as the owner, followed by `manage.py ensure_db_roles`. New features must not need DDL or TRUNCATE at runtime.
11. **Client IP = `ims.net.client_ip(request)`** (trusted-proxy aware). Never read `X-Forwarded-For` directly.
12. Service functions raise `django.core.exceptions.ValidationError`; `ims.api.exception_handler` turns it into a 400.

## Gotchas learned the hard way
- DRF reserves `?format=` → report exports use **`?export=xlsx|pdf|xml|csv`**.
- Noto Sans Devanagari has **no Latin glyphs**: PDFs use Helvetica plus `documents.mixed()` to wrap Devanagari runs.
- React StrictMode double-runs effects: anything that POSTs from an effect must be idempotent (FonePay modal keeps one idempotency key per attempt).
- `manage.py flush` also wipes the migration-seeded tax categories; `seed_demo` re-runs that seed.
- **UI style (2026-11-07, unslop pass — rules from the unslop skill by Damir Sokolovsky, CC BY 4.0):** direction = exact, sturdy, unhurried; the shop counter and the accountant's ledger. Graphite greys, deep green `#14573f` as the only accent (primary action, selection, the main chart). IBM Plex Sans (self-hosted via @fontsource, latin 400/500/600) and **IBM Plex Mono for every figure** (`.fig`, `td.num`, totals) — that's the signature. Weights 400/500 only, spacing on a 4px grid, radius 4/8/12. Report tables put "(Rs)" in the header and show zero as a muted dash. The POS bill is drawn as a thermal receipt (`.receipt`, torn edge via mask). Stat cards are numbers only (no sparklines); exceptions go in `AttentionList`. No top bar: branch picker and online status live in the sidebar. Badges only for exceptions. No gradients, glows, uppercase labels, emoji or looping motion. The **operator console** is deliberately different so it is never mistaken for the shop app: ink-blue accent `#2f4b8f` and a dark graphite frame (sidebar, sign-in page), set in `console/src/console.css`.

## Where things are
- Checkout, returns, notes, printing: `backend/sales/services.py`. Numbering: `sales/numbering.py`. Pricing: `sales/pricing.py`.
- IRD/CBMS/reports/documents: `backend/compliance/` (`cbms.py`, `reports.py`, `documents.py`, `exports.py`, `bs.py`).
- Product photos: `backend/catalog/images.py` (content-addressed WebP in `MEDIA_ROOT`, `media` volume); POS side `frontend/src/lib/images.ts` + `public/sw.js` (cache `ims-media-v1`). POS catalog is cache-first with a `version` check (`pos/catalog/?version=`).
- Stock logic: `backend/inventory/services.py`. Purchasing + purchase VAT: `backend/purchasing/services.py`.
- POS UI and offline logic: `frontend/src/pages/Pos.tsx`, `src/lib/{offline,sync,printer,pricing}.ts`.
- Hardware (browser-only WebUSB/WebSerial, per-till settings in IndexedDB): `src/lib/devices/`, `src/lib/barcode.ts`, `components/{DeviceSettings,CameraScanner,LabelPrint}.tsx`, `pages/CustomerDisplay.tsx` (`/customer-display`, no login).
