# IMS — Multi-branch Inventory Management + POS

Built from `ims-design-doc (3).md` and made Nepal IRD-compliant per `payment.md`.
One Django project (DRF, separate apps per domain) + a React/TypeScript PWA.
Money is integer paisa everywhere; every document carries BS and AD dates.

```
backend/   Django 5.2 + DRF       accounts · tenancy · catalog · inventory · sales · purchasing · reporting · admin_extras · compliance
frontend/  React 19 + TS + Vite   POS (online + offline PWA) · dashboard · inventory · purchasing · admin
deploy/    nginx/, certbot-loop.sh, web.Dockerfile, crontab, backup.sh, restore-drill.sh
docs/      IRD questions, compliance checklist, onboarding, templates, user manual
```

## Documentation
| Doc | For |
|---|---|
| [`docs/STATUS.md`](docs/STATUS.md) | What's built, what's left, decisions pending |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the system fits together; key flows |
| [`docs/CODE-GUIDE.md`](docs/CODE-GUIDE.md) | File-by-file tour; how to make common changes |
| [`docs/DATABASE.md`](docs/DATABASE.md) | Tables, constraints, triggers, ERD |
| [`docs/API.md`](docs/API.md) | Every endpoint with permissions and payloads |
| [`docs/compliance-checklist.md`](docs/compliance-checklist.md), [`docs/ird-questions.md`](docs/ird-questions.md) | IRD status and open questions |
| [`docs/user-manual.md`](docs/user-manual.md) | End-user guide (also in-app Help) |
| [`CLAUDE.md`](CLAUDE.md) | Context for AI coding sessions |

## Run it locally

```bash
# backend (SQLite for dev; set DATABASE_URL=postgres://… for Postgres)
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo          # 3 branches, users, catalog, stock, a week of sales
.venv/bin/python manage.py runserver 127.0.0.1:8000

# frontend (proxies /api to :8000 so cookies are same-origin)
cd frontend
npm install
npm run dev                                    # http://localhost:5173
```

Demo users (password `demo-pass-123`):

| user | role |
|---|---|
| `admin` | OrgAdmin (all branches, dashboard, users) |
| `manager` | BranchManager @ KTM, Cashier @ PKR (role-per-branch) |
| `cashier` | Cashier @ KTM |
| `cashier_pkr` | Cashier @ PKR |
| `clerk` | InventoryClerk @ KTM |
| `viewer` | Viewer (org, read-only reports) |
| `manager_brt` | BranchManager @ BRT |

FonePay runs against a **mock gateway** by default: the QR modal shows
"Simulate payment / failure" buttons so the whole initiate → poll → commit
flow works without bank credentials.

## Tests

```bash
cd backend && .venv/bin/python manage.py test                 # 114 tests on SQLite
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/test_with_postgres.py                # same suite on a throwaway PostgreSQL:
                                                              # DB triggers + concurrency tests run too
cd frontend && npm test                                       # pricing parity with the Python engine
```

`backend/sales/tests/pricing_vectors.json` is checked by **both** the Python
and TypeScript pricing engines, so offline totals on the register always match
what the server computes.

## Nepal IRD compliance (payment.md)

Full status per item: **`docs/compliance-checklist.md`**. Open questions and the
assumptions made while building: **`docs/ird-questions.md`** — read before go-live.

- **Invoices:** full tax invoice (कर बीजक, Rule 17) or abbreviated (Rule 18) chosen
  automatically; > Rs 10,000 needs buyer details; a buyer PAN forces a full invoice.
  Per-branch, per-fiscal-year gapless series (`KTM-2083-84-000001`). Print once;
  reprints say "COPY OF ORIGINAL — Copy #n"; delivery copies. Cancel needs a reason
  and keeps the bill printable. Corrections only via credit (CN-…) / debit (DN-…) notes.
- **Append-only:** model guards + **PostgreSQL triggers** (raw SQL can't edit or
  delete issued documents or the audit log). Audit trail with before/after values.
- **Tax engine:** effective-dated tax categories (13 / 5 / zero-rated / exempt / HST…),
  excise, blocked input credit, digital-payment VAT discount (off by default).
- **CBMS:** outbox + `manage.py cbms_sync` (cron, every minute), retries, `isrealtime`,
  response codes, encrypted credentials, pause on bad password, sync dashboard,
  turnover meter. `mock://` double for dev; set the IRD URL/credentials to go live.
- **Reports** (Tax & IRD page): sales book (Sch-9), purchase book (Sch-8), return
  registers, VAT return working (due date, nil flag), cancelled bills, stock vs
  purchase-book reconciliation, CBMS sync, audit trail — Excel/XML/PDF/CSV export and
  an accountant zip. Auditor views `cbms_bill_view` / `cbms_return_view`.
- **Listing evidence:** `manage.py generate_listing_evidence --out DIR [--create-test-bills]`.
- **Backups:** `deploy/backup.sh` (6-year retention) + weekly `deploy/restore-drill.sh`.

## How the design doc maps to code

| Design doc | Where |
|---|---|
| §6 tenancy (branch filter, transfers visible to both ends) | `tenancy/middleware.py`, `ims/api.py::BranchScopedMixin`, `inventory/models.py::TransferQuerySet` |
| §7 RBAC, role per branch, org roles with `branch=NULL` | `accounts/roles.py`, `accounts/models.py`, `accounts/permissions.py` |
| §7/§21 session + CSRF auth, instant revocation | `accounts/views.py`, `ims/settings.py` |
| §9.2 tax: discount before tax, sum per rate, round once | `sales/pricing.py` ⇄ `frontend/src/lib/pricing.ts` |
| §9.4/§9.10 gapless per-register receipts (locked row, no sequences) | `sales/numbering.py` |
| §9.5 credit notes with their own series, partial returns | `sales/services.py::create_return` |
| §9.6 weighted-average cost, negative-stock rule | `inventory/services.py::weighted_average`, `receive_stock` |
| §9.7 `allow_negative`, `stock_conflict` | `sales/services.py::_decrement_stock` |
| §9.8 stocktakes, §9.9 transfers with partial receipt | `inventory/services.py` |
| §10.1 online cash checkout (sorted locks, one transaction) | `sales/services.py::checkout_cash` |
| §10.2 FonePay initiate / callback / polling / idempotent commit | `sales/services.py`, `sales/fonepay.py` |
| §10.3 offline cash, queued sync, local receipt series | `frontend/src/lib/offline.ts`, `sync.ts`, `sales/services.py::sync_offline_sale` |
| §10.5 expiry job | `manage.py expire_open_sales` (cron every minute) |
| §13 printer abstraction (browser / network ESC/POS / USB bridge), drawer | `frontend/src/lib/printer.ts` |
| §14 reconciliation | `manage.py reconcile_fonepay` (daily) |
| §16 CSV import (dry run, idempotent) | `admin_extras/imports.py` |
| §17 dashboard, §18 per-branch timezone "today" | `reporting/queries.py` |
| §20 audit log | `admin_extras/models.py::log_action` |

## Decisions and gaps — read before production

These are still **open questions** from §25 of the doc, and the code is built so each can change without a rewrite:

1. **FonePay API is unverified.** `sales/fonepay.py::HttpFonepayGateway` follows the
   community-library shape (HMAC-SHA512, poll by PRN). Field names, URLs and the
   callback signature format (`prn,status,amount,trx_id`) are **placeholders**:
   confirm them against the bank's official docs and sandbox before you switch FonePay
   to Live (Admin → Branches & registers → FonePay). `settlement_report` isn't implemented yet.
2. **IRD receipt numbering.** Now per branch + fiscal year (payment.md). Offline tills print
   a provisional slip and the invoice is issued on sync — confirm with IRD (ird-questions D),
   or switch to per-register series in Settings.
3. **Fiscal year** is computed from the real BS calendar (`nepali-datetime`); update the
   package yearly as the Panchanga committee fixes future month lengths.
4. **Discount before tax**, **weighted-average costing** and the **`allow_negative`
   default** are implemented as the doc specifies. Confirm them against Nepal VAT rules.
5. **Cash rounding** is off by default (`IMS_CASH_ROUNDING_PAISA=1`). Set it to `100`
   to round cash totals to the nearest rupee; the difference goes to `round_adjust_paisa`.
6. Offline login caches a PBKDF2 hash in IndexedDB and is refused after 24h. It only
   works in a secure context (HTTPS or localhost).
7. If a checkout's response is lost after the server has committed, the POS
   falls back to an offline sale with the **same idempotency key**. The sync
   then returns the server's sale, and the offline number shows up as a gap to
   reconcile against the paper receipt.
8. Imports accept **CSV** only (no Excel parser yet). Export the sheet to CSV first.
9. The doc's tenant filter is applied **explicitly** in viewsets (`BranchScopedMixin`)
   rather than through a thread-local "magic" manager. Postgres RLS is not enabled.

## Deploy

Docker: `cp .env.example .env`, fill it in, `docker compose up -d --build`. Full guide in `docs/DEPLOYMENT.md`; security controls in `docs/SECURITY.md`.

- Measure checkout P95 (< 500 ms) with a load test before launch.
