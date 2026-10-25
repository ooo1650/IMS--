# Architecture

## 1. Big picture

```
 Browser (React + TS PWA)                        Server (one VM, Nepal)
 ┌──────────────────────────────┐   HTTPS        ┌─────────────────────────────────────────┐
 │ POS · Sales · Inventory ·    │  same origin   │ Caddy / nginx                            │
 │ Purchasing · Tax & IRD ·     │ ─────────────▶ │  /        → static SPA (frontend/dist)   │
 │ Dashboard · Admin · Help     │  session +     │  /api/*   → gunicorn → Django + DRF      │
 │                              │  CSRF cookie   │                                         │
 │ IndexedDB: catalog, offline  │                │ Django apps (one project)               │
 │ sale queue, cached login     │                │  tenancy accounts catalog inventory     │
 │ Service worker: app shell    │                │  sales purchasing reporting compliance  │
 └──────────────┬───────────────┘                │  admin_extras                           │
                │ ESC/POS over HTTP (LAN)        └──────────────┬──────────────────────────┘
        thermal printer / drawer                                │ SQL (row locks, triggers)
                                                 ┌──────────────▼───────────┐   cron: cbms_sync,
                                                 │ PostgreSQL 16            │   expire_open_sales,
                                                 │ WAL archive + nightly    │   backups, reconcile…
                                                 │ dumps (6-year retention) │
                                                 └──────────────────────────┘
        external: FonePay (QR payments) · IRD CBMS (cbapi.ird.gov.np)
```

There's **no microservices, Redis or queue broker**, by design (design doc §4). Background work runs as `manage.py` commands from cron.

## 2. Django apps

| App | Owns | Key modules |
|---|---|---|
| `tenancy` | Organization (seller, PAN), Branch (timezone), Register (printer config); `TenantMiddleware` | `models.py`, `middleware.py` |
| `accounts` | User, UserBranchRole (role per branch; `branch=NULL` = org role); RBAC | `roles.py` (role → permission map), `permissions.py` |
| `catalog` | Product, Barcode (pack qty), Category, Supplier (PAN), BranchPrice; `tax_profile()` | `models.py`, `views.py` (incl. offline POS catalog) |
| `inventory` | InventoryItem (cached qty, weighted-avg cost), StockMovement ledger, transfers, stocktakes | `services.py` (sorted locks, WAC) |
| `sales` | Sale, SaleLine, Payment, Receipt, PrintLog, CreditNote, DebitNote, DocumentSequence, shifts, held carts, customers | `services.py`, `pricing.py`, `numbering.py`, `fonepay.py` |
| `purchasing` | PurchaseOrder, GoodsReceipt (purchase VAT), PurchaseReturn | `services.py` |
| `reporting` | Dashboard and branch report queries (sales, stock, alerts) | `queries.py` |
| `compliance` | Tax categories (effective-dated), settings, CBMS outbox and client, BS calendar, tax documents (HTML/PDF), statutory reports, exports, append-only base and DB triggers | `bs.py`, `cbms.py`, `documents.py`, `reports.py`, `exports.py`, `appendonly.py` |
| `admin_extras` | AuditLog (before/after), CSV import, demo seed | `audit.py`, `imports.py` |

Dependency direction (roughly): `tenancy ← accounts ← catalog ← inventory ← sales/purchasing ← compliance reports`. `compliance.appendonly` and `compliance.bs` are low-level helpers used by everything.

## 3. Request flow

1. `SessionMiddleware` / `AuthenticationMiddleware` resolve the user from the session cookie.
2. `tenancy.middleware.TenantMiddleware` sets `request.branch` from the `X-Branch-Id` header, falling back to the session. It's ignored unless the user has a role there.
3. `admin_extras.audit.AuditContextMiddleware` puts user/branch/IP in a contextvar for the audit signals.
4. The DRF view checks permissions: `HasBranchPerm("pos.sell")` (branch role or org role) or `HasOrgPerm(...)`.
5. The view calls a **service function** (business logic lives in `services.py`, never in views), which runs inside `transaction.atomic()`.
6. Service code raises `ValidationError` → `ims.api.exception_handler` → HTTP 400 `{"detail": …}`.

### RBAC
`accounts/roles.py` maps roles to permission strings (`pos.sell`, `pos.void`, `inventory.adjust`, `reports.view`, `dashboard.view`, `catalog.manage`, …). A user's permissions at a branch = branch role perms ∪ org role perms. The frontend hides menus by permission (`components/Layout.tsx`), but the backend is the authority.

## 4. Key flows

### 4.1 Cash / card / wallet / bank checkout — `sales.services.checkout_cash`
```
idempotency check → open shift? → price_cart (tax_profile on today's date, branch price)
→ decide_invoice_type (abbreviated ≤ Rs 10,000 w/o PAN; else full, needs buyer name+address)
→ discount authority (manager credentials above 5%)
→ create Sale(paid) + SaleLines → _decrement_stock (lock items sorted, check, sale_out movements, COGS snapshot)
→ Payment → _issue(): allocate invoice number (locked DocumentSequence), issued_at/BS, transaction_at/BS,
  Receipt row, CbmsPost(bill) outbox  → commit
```
Then the frontend calls `POST /sales/{id}/print/`. `record_print` writes a PrintLog (original or copy n), and the server returns the HTML.

### 4.2 FonePay QR — `initiate_fonepay` → `commit_fonepay`
Initiate creates an *open* Sale and an *initiated* Payment and asks the gateway for a QR. **No stock locks during the wait.** The POS polls `payment-status`, which polls FonePay by PRN (a callback is also supported). On success: amount check (a mismatch marks it `payment_orphan`) → decrement stock (a shortage sets `stock_conflict`, it's not rejected) → `_issue`. `expire_open_sales` (cron) does a final poll, then voids. The real FonePay API is unconfirmed; `FONEPAY_BACKEND=mock` is the default.

### 4.3 Offline cash — `frontend/src/lib/{offline,sync}.ts` + `sync_offline_sale`
- The PWA caches the catalog (no cost prices), the POS config and a PBKDF2 login hash (valid 24 h).
- **Per-branch series (default):** the till prints a *provisional slip* (`P-<registerId>-000001`, not a tax invoice). On sync the server issues the invoice number; issue date = sync time, transaction date = sale time.
- **Per-register series:** the till numbers locally from its own series, and the server claims that number on sync.
- The queue syncs one sale at a time with idempotency keys. 401/403 leaves items pending; stock shortfalls are recorded as `stock_conflict`.

### 4.4 Returns and corrections
- **Credit note** (`create_return`): partial quantities, pro-rata refund of amount/VAT/excise/levy computed cumulatively (exact), restock at the current average cost, CN number, CBMS `billreturn` outbox.
- **Debit note** (`create_debit_note`): an extra charge on an issued invoice.
- **Cancel** (`void_sale`): reason required, restocks, keeps the number. Blocked once notes exist. CBMS gets a full-return record (an assumption, see ird-questions B).

### 4.5 Purchasing
PO → ordered → `receive_goods` (supplier invoice no./date, VAT per rate rounded once, blocked input credit, `purchase_in` movements, weighted average recomputed) → purchase book. `create_purchase_return` takes stock out and reverses input VAT.

### 4.6 CBMS — `compliance/cbms.py`
Outbox row created in the document's transaction → cron `cbms_sync` every minute → POST `/api/bill` or `/api/billreturn`.
- Codes 200/101 → synced.
- 105 → wait for the bill.
- 100 → pause everything and show an alert until the password is updated.
- Others → exponential backoff (gives up after 20 attempts as *failed*; "Retry failed" re-queues).
- `isrealtime` is false when posted outside the window (10 min).
- Credentials are Fernet-encrypted in `ComplianceSettings`.

### 4.7 Reporting
- `reporting/queries.py`: operational dashboard (today in each branch's timezone, trend, low stock, slow movers, conflicts).
- `compliance/reports.py`: statutory books for a BS fiscal year or month. They all share one row/column shape, so `exports.py` renders any of them to xlsx/xml/pdf/csv.

## 5. Frontend structure
- `App.tsx`: routes filtered by permission. `components/Layout.tsx`: nav (`NAV` list), branch switcher, online/queue pills.
- `auth.tsx`: session login with offline fallback, active branch, `can()` / `canOrg()`.
- `lib/api.ts`: fetch wrapper (CSRF header, `X-Branch-Id`; 502/503/504 count as offline → `NetworkError`).
- `lib/pricing.ts`: same tax engine as the server (parity tests). `lib/printer.ts`: browser HTML print or ESC/POS text.
- `pages/*`: one file per screen. `Pos.tsx` is the largest (cart, buyer, payments, offline, returns, shifts).

## 6. Deployment
`docker-compose.yml` runs `db` (Postgres 16 with WAL archiving), `backend` (gunicorn) and `web` (Caddy serving the SPA and proxying the API). Migrations run as a release step (`--profile release`). Cron jobs live in `deploy/crontab`: CBMS sync, expiry, FonePay reconciliation, stock reconciliation, auditor view refresh, backup, weekly restore drill. Secrets go in `backend/.env` (see `.env.example`).

## 7. Decisions worth knowing
- Session auth instead of JWT: one first-party SPA, and revocation is instant (`is_active=False` takes effect on the next request).
- Weighted-average costing instead of FIFO (design doc §9.6).
- Explicit tenant scoping in viewsets, not a thread-local "magic" manager. Postgres RLS is not used.
- Exact rational arithmetic for tax, so the browser and server agree to the paisa.
- Numbering per branch (IRD ask) with provisional offline slips. Switchable to per register in Settings.
