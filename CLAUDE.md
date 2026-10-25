# IMS — context for the next session

Multi-branch **Inventory Management + POS** for one organisation (~10 branches in Nepal), made **Nepal IRD-compliant**.
Source specs: `ims-design-doc (3).md` (architecture) and `payment.md` (IRD/VAT/CBMS rules). Both are in the repo root.

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
cd backend && .venv/bin/python manage.py migrate && .venv/bin/python manage.py seed_demo   # demo data (refuses if data exists; `flush` first)
.venv/bin/python manage.py runserver 127.0.0.1:8000
cd frontend && npm run dev            # http://localhost:5173  (Vite binds IPv6 localhost, not 127.0.0.1)
# demo logins (password demo-pass-123): admin, manager, cashier, cashier_pkr, clerk, viewer, manager_brt

cd backend && .venv/bin/python manage.py test                 # 114 tests, SQLite (4 Postgres-only skipped)
.venv/bin/python scripts/test_with_postgres.py                # same suite on embedded Postgres (pgserver) — triggers + concurrency
cd frontend && npm test && npx tsc -b && npm run build        # 68 pricing-parity tests
```
Last verified: all green on 2026-10-25.

## Invariants (don't break these)
1. **Money is integer paisa** (`BigIntegerField`), never float. Rates are `Decimal`. Pricing math uses exact `Fraction`, rounded half-up only when stored.
2. **Tax = discount before tax, sum per rate, round once** (`sales/pricing.py`). `frontend/src/lib/pricing.ts` is a line-for-line port. **Change both**, then regenerate the shared vectors with `backend/scripts/gen_pricing_vectors.py`. Check old vectors still match first.
3. **Lock order**: `InventoryItem` rows are locked sorted by `product_id` (`inventory.services.lock_items`), then the `DocumentSequence` row. Use `select_for_update(of=("self",))` whenever you `select_related` a nullable FK (Postgres rejects FOR UPDATE on outer joins; SQLite hides this).
4. **Gapless numbering** only via `sales.numbering.allocate` (a locked row, never a DB sequence). Default series is **per branch + fiscal year** (`KTM-2083-84-000001`). Prefixes: `CN-`, `DN-`, `PR-`.
5. **Issued tax documents are append-only**: `compliance.appendonly.AppendOnlyModel` (model guard) + Postgres triggers (`compliance/migrations/0003`). After issue, only cancellation/review columns may change. Never `.update()` an issued sale; seed/backfill code must set values *before* issue (e.g. `checkout_cash(now=...)`).
6. **Every stock change writes a `StockMovement`**. `quantity_on_hand` is a cache (`manage.py reconcile_stock` verifies it).
7. **Tenant isolation**: branch-scoped viewsets inherit `ims.api.BranchScopedMixin`. The active branch comes from the `X-Branch-Id` header or the session (`tenancy.middleware`). Org-level report users see all branches (`compliance.views._report_branches`).
8. **BS dates** come from `compliance.bs` (nepali-datetime). Fiscal year label `2083-84`, CBMS format `2083.084`, dates `YYYY.MM.DD`. 2026-10-25 = 2083.07.08.
9. **CBMS** is an outbox (`CbmsPost`) written in the same transaction as the document; `manage.py cbms_sync` posts it. `mock://` base URL = in-process double.
10. Service functions raise `django.core.exceptions.ValidationError`; `ims.api.exception_handler` turns it into a 400.

## Gotchas learned the hard way
- DRF reserves `?format=` → report exports use **`?export=xlsx|pdf|xml|csv`**.
- Noto Sans Devanagari has **no Latin glyphs**: PDFs use Helvetica plus `documents.mixed()` to wrap Devanagari runs.
- React StrictMode double-runs effects: anything that POSTs from an effect must be idempotent (FonePay modal keeps one idempotency key per attempt).
- `manage.py flush` also wipes the migration-seeded tax categories; `seed_demo` re-runs that seed.
- **A UI restyle is in progress outside the build sessions** (since 2026-10-25 16:33): `components/Icon.tsx` (inline SVG icons), `Login.tsx` and `ui.tsx` were changed by the user or another tool. Read files fresh before editing them, and don't revert that work.

## Where things are
- Checkout, returns, notes, printing: `backend/sales/services.py`. Numbering: `sales/numbering.py`. Pricing: `sales/pricing.py`.
- IRD/CBMS/reports/documents: `backend/compliance/` (`cbms.py`, `reports.py`, `documents.py`, `exports.py`, `bs.py`).
- Stock logic: `backend/inventory/services.py`. Purchasing + purchase VAT: `backend/purchasing/services.py`.
- POS UI and offline logic: `frontend/src/pages/Pos.tsx`, `src/lib/{offline,sync,printer,pricing}.ts`.
