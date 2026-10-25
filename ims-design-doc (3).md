# Design Doc: Multi-Tenant Inventory Management System (IMS) with POS

**Status:** Draft
**Author:** (you)
**Last updated:** 2026-10-18
**Jurisdiction:** Nepal (no e-invoicing required)
**Currency:** NPR — money stored as integer paisa (1 NPR = 100 paisa)

---

## 1. Overview

A web-based Inventory Management System (IMS) for one organization with ~10 branches across Nepal. Each branch is a tenant with its own inventory and a POS (point-of-sale) cash-register screen. A centralized dashboard aggregates data across branches.

- **Backend:** one Django project + Django REST Framework, separate apps per domain.
- **Frontend:** React + TypeScript SPA (PWA for offline cash sales).
- **Data:** one PostgreSQL database (backups + point-in-time recovery). Add one read replica only if reporting queries slow the POS.
- **Web:** nginx or Caddy reverse proxy + gunicorn on one or two VMs.
- **Auth:** Django session cookies + CSRF (not JWT — one first-party SPA, one backend, instantly revocable).
- **Payments:** FonePay Nepal (QR-based) for digital; cash handled locally.
- **Receipts:** internal gapless numbering (no fiscal authority call needed).

Architecture is deliberately simple. We add infrastructure only when we measure a problem it solves.

---

## 2. Goals

- One org, ~10 branches; each branch sees only its own data.
- A POS screen per branch for in-store sales — cash (works offline) and FonePay QR (needs internet).
- A centralized dashboard for org-wide inventory, sales, and alerts.
- RBAC with role-per-branch.
- Fast, correct checkout. Checkout latency P95 < 500ms.
- Tested backups with a verified restore.

## 3. Non-Goals (product scope)

- Not a marketplace or e-commerce site (no public storefront).
- No multi-org (one org owns all branches).
- **No accounting ledger** — this system records sales and inventory value; it is not a general ledger. Export to an accounting package if needed.
- **No supplier payables** — purchase orders track what's ordered and received, not what's owed to suppliers.
- **No loyalty program** — no points, tiers, or rewards.
- **No e-commerce / online ordering** — in-store sales only.

---

## 4. Architecture

One Django project, separate apps, one Postgres. Boundaries enforced through imports and code review.

```
                         +---------------------------+
                         |   Centralized Dashboard     |
                         |   (org-wide aggregates)     |
                         +-------------+--------------+
                                       |
            +--------------------------+--------------------------+
            |            React + TypeScript SPA (PWA)            |
            |   POS UI   |   Branch Mgmt UI   |   Admin/Dashboard  |
            +-----+----------------------+---------------------+---+
                  |  HTTPS / session     |
            +-----v----------------------+-------------------------+-+
            |              nginx / Caddy (reverse proxy)            |
            +-----+----------------------------------------------+--+
                  |
            +-----v----------------------------------------------+--+
            |         one Django project + DRF (apps below)        |
            |  auth · tenancy · catalog · inventory · sales/pos    |
            |  purchasing · reporting                              |
            |  RBAC · tenant middleware                            |
            +-----+----------------------------------------------+--+
                  |
            +-----v----------------------------------------------+--+
            |           one PostgreSQL (primary)                  |
            |   backups + WAL archiving + point-in-time recovery  |
            +-----------------------------------------------------+
```

Design decision (not a non-goal): no microservices. One Django project with separate apps is enough for ~10 branches and a small team. Services, per-service DBs, event buses, and Kubernetes are not justified at this scale.

---

## 5. Django Apps (one project, separate apps)

| App            | Responsibility                                            |
|----------------|----------------------------------------------------------|
| `accounts`     | Users, roles, permissions, session auth, role-per-branch  |
| `tenancy`      | Organization + Branch models, tenant middleware, timezones |
| `catalog`      | Products, categories, suppliers, units, barcodes, prices  |
| `inventory`    | Stock-on-hand, movements, transfers, stocktakes, costing  |
| `sales`        | Sales, sale lines, discounts, returns, payments, receipts, cashier sessions, credit notes |
| `purchasing`   | Purchase orders, goods receipt, partial receipts          |
| `reporting`    | Dashboard queries (plain SQL, indexed views, nightly job) |
| `admin_extras` | User/branch management screens, product archiving         |

---

## 6. Multi-Tenancy

One org, ~10 branches.

- Every tenant-scoped table has a non-null `branch_id` column.
- `TenantMiddleware` injects the user's active branch into the request.
- All querysets filter by `branch_id` automatically (custom `Manager`).
- The centralized dashboard runs cross-branch querysets for org-admin.
- **Two-branch objects (transfers):** a transfer belongs to both `from_branch` and `to_branch`. Rule: a transfer is visible to managers of **both** branches. The tenant filter checks `branch_id IN (from_branch, to_branch)` for transfers. The dashboard (org-admin) bypasses the filter entirely.
- **Optional safety net:** add Postgres Row-Level Security (RLS) on `branch_id` later if you want a DB-level guard. Not needed on day one; the queryset filter plus tests is the primary defense.

---

## 7. RBAC — Role per Branch

A user has a role **at each branch** they work in. Org-level roles (OrgAdmin, SuperAdmin) have no branch.

```
UserBranchRole(user_id, branch_id NULL, role)
  -- branch_id is nullable: null means org-scoped (OrgAdmin, SuperAdmin)
  -- (user_id, branch_id) is unique
```

- A cashier at Branch A is not automatically a cashier at Branch B.
- A user's session carries their roles; the active branch selects which role applies.
- A user can be BranchManager at one branch and Cashier at another.
- OrgAdmin/SuperAdmin rows have `branch_id = NULL` and apply across all branches.

**Roles (default set, extensible)**

| Role           | Scope          | Key permissions                              |
|----------------|----------------|----------------------------------------------|
| SuperAdmin     | Org (no branch)| Everything                                   |
| OrgAdmin       | Org (no branch)| All branches, dashboard, users, config        |
| BranchManager  | One branch     | Inventory, purchasing, reports, returns approval |
| Cashier        | One branch     | POS sales, drawer, own shift                 |
| InventoryClerk | One branch     | Stock in/out, adjustments, transfers, stocktakes |
| Viewer         | Branch or Org  | Read-only reports                            |

**Enforcement (two layers)**

1. **Backend (authority):** DRF permission classes + decorators. Every endpoint checks the role for the active branch (or org if `branch_id IS NULL`); tenant scope checks `branch_id` membership.
2. **Frontend (UX):** route guards hide/disable UI the user can't use. Never the only check.

**Model:** role -> permissions (resource, action). A small custom implementation or `django-rbac`.

**Auth:** Django session cookies + CSRF protection. Not JWT. One first-party SPA on one backend — sessions are simpler and can be revoked instantly (no "fired cashier keeps access for 15 minutes" window).

---

## 8. Database

One PostgreSQL instance.

- **Money:** stored as integer minor units (paisa). `BIGINT` columns, never `FLOAT` or `NUMERIC` for money. `NPR 12.50` stored as `1250`.
- **Backups:** nightly base backup + continuous WAL archiving. Point-in-time recovery (PITR) configured.
- **Tested restore:** restore from backup into a throwaway instance on a schedule (weekly) and verify it boots and serves a sale. An untested backup is not a backup.
- **RPO/RTO:** RPO < 15 min (WAL), RTO < 1 hour. Test against these.
- Add one read replica **only** if reporting queries actually slow the POS. Route reporting to it then; checkout always reads/writes the primary.
- **Indexes:** `(branch_id, product_id)`, `(branch_id, created_at)` on sales and movements.
- No PgBouncer or Redis unless you measure a problem they solve.

---

## 9. Domain Model

This section is the most detailed — the domain is harder and more valuable than infrastructure.

### 9.1 Products, barcodes, pricing

```
Product(id, sku, name, category_id, supplier_id?, unit, cost_price_paisa,
        sale_price_paisa, tax_rate, tax_inclusive, is_active, archived_at?)
Barcode(product_id, code, type, pack_qty)
BranchPrice(branch_id, product_id, sale_price_paisa)  -- optional per-branch override
Category(id, name, parent_id?)
Supplier(id, name, phone?, email?, address?)
```

- A product has one canonical SKU. Multiple barcodes (pack sizes, supplier barcodes). `pack_qty` lets a barcode ring up a case as N units.
- `sale_price_paisa` is the default. `BranchPrice` overrides it per branch (optional).
- `cost_price_paisa` is the purchase default only. Actual cost is tracked via movements (see 9.6).
- Products are soft-deleted (`archived_at`), never hard-deleted — sales reference them.
- **Admin:** product archive/restore screens; bulk import (see Section 16).

### 9.2 Tax and rounding — precise rule

- Tax rate is **per product**, with an optional **per-branch override** (`BranchTaxRate` if needed — add only if branches are in different tax regions; Nepal uses VAT, one rate likely).
- **Rounding happens once, on the sale total — never per line.** The precise rule:
  1. For each sale line, compute the **unrounded** tax amount (fractional) using the line's tax rate.
  2. Group lines by tax rate. Sum the unrounded line tax for each rate.
  3. Round each rate's total tax to the nearest paisa (round half-up). This gives the per-rate tax total.
  4. Sum the per-rate tax totals = `tax_total_paisa` on the sale.
  5. `total_paisa = subtotal_paisa - discount_total_paisa + tax_total_paisa`, rounded to paisa. `round_adjust_paisa` captures any residual.
- **Tax-inclusive pricing** (e.g., 13% VAT included): line tax = `unit_price_paisa * qty * rate / (100 + rate)`. This is fractional per line — which is exactly why we sum unrounded and round once per rate, not per line.
- Money is integer paisa. No `FLOAT`, no `NUMERIC` for money fields. Tax computation uses Python `Decimal` internally, rounds to paisa only at the store step.
- `SaleLine` stores `tax_rate` and the rounded `tax_amount_paisa` (the per-line share of the rate's total, allocated proportionally so the lines sum to the rate's total). Store the allocation method.

### 9.3 Sales, discounts, payments, receipts

```
Sale(branch_id, cashier_id, customer_id?, status, subtotal_paisa,
     discount_total_paisa, tax_total_paisa, round_adjust_paisa, total_paisa,
     opened_at, paid_at?, voided_at?, voided_by?, offline BOOLEAN,
     register_id?, idempotency_key, payment_ref?, stock_conflict BOOLEAN DEFAULT false,
     expires_at?)
SaleLine(sale_id, product_id, qty, unit_price_paisa, discount_type,
         discount_value_paisa, line_total_paisa, tax_rate, tax_amount_paisa,
         unit_cost_paisa)  -- unit_cost snapshot for COGS
Payment(sale_id, method, amount_paisa, tendered_paisa, change_paisa, ref,
        fonepay_prn?, fonepay_trx_id?, status, expires_at?)
Receipt(sale_id, number, format, printed_at?)
```

- **Discounts:** line-level (amount or percent, per sale line) and sale-level (amount or percent, on subtotal). Discount has a `reason`; manager-approved discounts link `approved_by`. Values stored at sale time — the sale is frozen.
- **Discount applies before tax** per the line; sale-level discount applies to the pre-tax subtotal. Rule: **discount before tax.**
- **Sale status:** `open` (cart, FonePay initiated but unpaid), `paid`, `voided`, `payment_orphan` (paid externally but stock not available — see 10.2 step 6). Void (not delete) keeps the audit trail and the receipt number.
- **`round_adjust_paisa`** stores the rounding delta so `subtotal - discount + tax + round_adjust = total` exactly.
- **`offline`** flag marks sales taken while the register was disconnected.
- **`register_id`** links the sale to the physical register (needed for offline receipt numbers).
- **`idempotency_key`** prevents double-commit on retry or offline sync.
- **`stock_conflict`** flag: set when a sale records despite insufficient stock (see 9.7, 10.2, 10.3). Both online FonePay and offline cash use this flag consistently — see Section 10.
- **`expires_at`** on open sales and initiated payments: abandoned sales are cleaned up (see 10.5).

### 9.4 Receipt numbers (gapless, per-register series)

Receipt numbers must be gapless and monotonic per register.

```
ReceiptSequence(branch_id, register_id, fiscal_year, next_number)
  PK (branch_id, register_id, fiscal_year)
```

- **Per-register gapless series** (`branch-register-fiscalyear-N`), not a shared per-branch series and not exclusive blocks. Each register has its own monotonic sequence; numbers within a register are chronological and gapless. Numbers across registers at the same branch differ in their register prefix, so they're not comparable — that's expected.
- **Only** this locked-row approach works. A Postgres **sequence is not gapless** — sequences don't roll back, so a failed checkout burns a number. Do not use sequences.
- At checkout, `SELECT ... FOR UPDATE` on the `ReceiptSequence` row for the register, allocate the next number, commit it in the same transaction as the sale.
- Voids keep the number; a voided sale is never deleted.
- **Confirm with your accountant that the IRD (Inland Revenue Department, Nepal) accepts per-register series** instead of a single per-branch series. If the IRD requires a single branch series, offline numbering becomes harder (coordinate a single server-allocated series and accept that offline can't issue a confirmed number — it issues a provisional and the server reassigns on sync, which risks gaps if a sync fails). This is an open question to resolve before building offline.
- **Credit notes** use the same scheme: per-register gapless series `CreditNoteSequence(branch_id, register_id, fiscal_year, next_number)`. Offline credit notes are deferred (see 10.3 disabled actions) unless the register has a pre-allocated offline series — confirm with the accountant.

### 9.5 Credit notes (returns)

Returns need their **own** document number, not the original receipt's.

```
CreditNote(branch_id, register_id, original_sale_id, cashier_id?, approved_by,
           total_paisa, number, at, reason)
CreditNoteLine(credit_note_id, original_sale_line_id, qty, unit_price_paisa,
               line_total_paisa)
```

- A return references the original sale. Items are restocked (movement `return_in`). A refund payment is recorded.
- **Credit note number** is gapless per register, separate sequence (`CreditNoteSequence`).
- Returns above a threshold require manager approval.
- A return can be partial (some lines, some qty).
- **Offline returns:** disabled by default (the cashier can't verify the original sale without the server). A manager override can allow an offline return with a provisional number if the accountant confirms per-register offline series are acceptable.

### 9.6 Inventory and costing (weighted-average)

```
InventoryItem(branch_id, product_id, quantity_on_hand, reorder_point, reorder_qty,
              weighted_avg_cost_paisa, allow_negative BOOLEAN DEFAULT false)
StockMovement(branch_id, product_id, type, qty, unit_cost_paisa, ref, ref_type,
              at, by, reason?)
  type in: purchase_in, sale_out, return_in, adjust_in, adjust_out,
           transfer_out, transfer_in, stocktake_adjust
```

- Every stock change is a movement. `quantity_on_hand` is a cached column reconciled by movements.
- **Costing: weighted-average**, not FIFO. On each `purchase_in` or `transfer_in`, recompute:
  ```
  if quantity_on_hand <= 0:
      weighted_avg_cost_paisa = received_cost
  else:
      weighted_avg_cost_paisa = ((quantity_on_hand * weighted_avg_cost_paisa)
                                 + (received_qty * received_cost)) \
                                / (quantity_on_hand + received_qty)
  ```
  The **negative-stock rule:** when on-hand is zero or negative, the new cost equals the received cost. This prevents the formula producing nonsense when the denominator is small or negative.
- Store the new `weighted_avg_cost_paisa` on the `InventoryItem`. One source of cost truth: the movement's `unit_cost_paisa` and the item's `weighted_avg_cost_paisa`.
- `Product.cost_price` is the purchase default only; there is no separate `InventoryItem.unit_cost` — use `weighted_avg_cost_paisa`.
- Sale lines snapshot `unit_cost_paisa` = the weighted-average at sale time (COGS).
- Returns: the returned item goes back at the current weighted-average cost (simpler than FIFO layer tracking).
- Transfers: cost travels with the stock (`StockMovement.unit_cost_paisa` on the `transfer_out` and `transfer_in`).
- **Lock order for receipts too:** lock `InventoryItem` rows in sorted order (by `product_id`) for purchase receipts and transfer receipts, not only checkout. Two concurrent receipts touching the same products must not deadlock.

**Why weighted-average:** for a 10-branch retailer it's far simpler. FIFO needs cost layers (a separate table tracking each purchase batch) and makes returns and transfers harder. Default to weighted-average unless a jurisdiction mandates FIFO — Nepal does not.

### 9.7 Negative stock policy

System counts are often wrong. A hard block stops a cashier from selling something sitting on the shelf.

- `InventoryItem.allow_negative` flag (default `false`, set per product or per branch).
- If `true`: checkout allows the sale even when `quantity_on_hand <= 0`. Stock goes negative. The movement records the negative. A report flags negative-stock items for recount.
- If `false`: **online** checkout rejects the line. **Offline** checkout records the sale anyway (the customer has the goods) and sets `stock_conflict = true` for manager review — you can't block an offline sale after it's happened.
- **Decision:** default to `false`; set `true` for fast-moving items where the system count is known to lag.

### 9.8 Stocktakes

```
Stocktake(branch_id, status, started_at, finished_at?, by, scope?)
  status in: draft, counting, finished
StocktakeLine(stocktake_id, product_id, counted_qty, system_qty, variance, reason?)
```

- A stocktake is a counted snapshot. Partial stocktakes (a category, a shelf) supported via `scope`.
- Finishing writes adjustment movements for the variances (movement type `stocktake_adjust`).
- Variances above a threshold require a `reason`.

### 9.9 Stock transfers with partial receipt

```
StockTransfer(from_branch_id, to_branch_id, status, started_at, by, received_at?,
              received_by?)
  status in: draft, in_transit, received, cancelled
StockTransferLine(transfer_id, product_id, qty_sent, qty_received, unit_cost_paisa,
                  variance_reason?)
```

Flow:
1. `draft` -> `in_transit`: deduct from `from_branch` (movement `transfer_out`). Stock is in transit — owned by neither branch's on-hand.
2. `in_transit` -> `received`: add to `to_branch` (movement `transfer_in`), set `received_at`.
3. `cancelled` (only from draft, with compensating movement if already dispatched).

**Partial receipt:** `qty_received` may be less than `qty_sent` (damage, loss in transit). The difference requires a `variance_reason`. The receiving branch books `qty_received`; the variance is written as an adjustment movement on the receiving branch (type `adjust_out`, reason = transfer loss).

**States merged:** `dispatched` and `in_transit` are the same state — once dispatched, it's in transit. No separate `dispatched` state.

**Visibility:** a transfer is visible to managers of both `from_branch` and `to_branch`. The tenant filter allows `branch_id IN (from_branch, to_branch)`. The dashboard (org-admin) sees all.

### 9.10 Offline receipt numbers (per-register gapless series)

See Section 9.4. Offline uses the **same per-register gapless series** as online — the register draws the next number from its own `ReceiptSequence` row, which it holds locally.

- The register maintains a **local copy** of its `ReceiptSequence.next_number` while offline, so it can allocate numbers without a server call.
- On sync, the server validates the register's used numbers against its series and advances the server-side `next_number` past the highest used. Because the series is exclusive to that register, there's no conflict with other registers.
- **No unused-number gaps from blocks:** unlike the block approach (which left gaps when a block wasn't exhausted), the per-register series has no end — the register just keeps incrementing. The only gaps within a register's series are voids (intentional, kept) and failed checkouts (prevented by the locked-row transaction).
- **Confirm with the accountant** that per-register series satisfy IRD requirements before building offline (see 9.4 open question).

---

## 10. Checkout Flow (the critical path)

This is the design after the four blockers are resolved: no e-invoicing, offline cash sales, FonePay QR for digital, printer abstracted.

**FonePay integration note:** the exact FonePay API details (signing method, callback vs polling, credential format) are **not confirmed**. Community libraries suggest HMAC-SHA512 request signing and status polling by PRN (payment reference number), with asynchronous settlement — not a webhook callback and not an RSA private key. **Get the official docs from your bank before finalizing section 14.** The design below handles both callback and polling.

### 10.1 Online cash checkout (server transaction)

1. SPA sends cart (branch_id, register_id, lines, payment=cash, cashier_id, idempotency_key) to `POST /api/v1/pos/checkout`.
2. Django opens a transaction.
3. Lock stock rows for all products in the cart, **sorted by product_id** (prevents deadlocks — all registers lock in the same order).
4. Validate stock available (or `allow_negative` is true). If unavailable and `allow_negative` is false, reject the line.
5. Compute line totals, discounts, tax (sum per rate, round once — see 9.2), rounding.
6. Decrement stock, write `StockMovement(sale_out)` per line, snapshot `unit_cost_paisa`.
7. Write `Sale` + `SaleLine`s + `Payment(cash)`.
8. Lock `ReceiptSequence` row for the register, allocate receipt number.
9. Commit. Return sale + receipt number.
10. Frontend sends receipt + drawer-open command to the printer bridge (async, outside the transaction).

One transaction, one commit. Stock and sale are atomic.

### 10.2 Online FonePay QR checkout (external payment, then commit)

FonePay is QR-based. The charge happens **outside** the database, so checkout is **not one transaction**. The commit step is triggered by a server-side status check — either a FonePay callback (if the API provides one) or a status poll by PRN (if polling-based). **Design for both; the polling path is the fallback regardless.**

1. SPA sends cart to `POST /api/v1/pos/checkout/initiate`. Django validates the cart (stock available, prices, totals — **does not lock stock yet, does not decrement**), creates a `Sale` with `status = open`, `Payment(status = initiated, fonepay_prn, expires_at = now + 10 min)`. Initiates the FonePay QR (request signed per the official API — HMAC-SHA512 per community libraries, **confirm with bank**). Returns the QR payload to the SPA.
2. Frontend displays the QR. Customer scans and pays.
3. **Commit trigger (either path):**
   - **Callback path (if API supports):** FonePay calls `POST /api/v1/pos/fonepay/callback` with the result. Django verifies the signature.
   - **Polling path (fallback, and primary if no callback):** a server-side job (or the SPA polling `GET /api/v1/pos/sale/{id}/payment-status`) checks the FonePay transaction status by PRN on an interval (e.g., every 5s for up to 10 min) until terminal (success/failed/expired).
4. On confirmed success, Django opens a transaction:
   - **Check the paid amount equals the sale total.** If not, mark `payment_orphan` (under/overpayment) for manager review — do not commit the sale as normal.
   - Lock stock rows (sorted by product_id).
   - Re-validate stock. **If stock is insufficient and `allow_negative` is false:** record the sale with `status = paid` and `stock_conflict = true` (see 10.6 — the customer is at the counter and has paid; record the sale and flag it, in both online and offline cases). Do not refund automatically.
   - Decrement stock (or let it go negative if flagged), write movements, snapshot `unit_cost_paisa`.
   - Update `Sale.status = paid`, `Payment.status = success`, `Payment.fonepay_trx_id`.
   - Allocate receipt number.
   - Commit.
5. Frontend polls `GET /api/v1/pos/sale/{id}/payment-status` or receives the callback. Sends receipt to printer.

**Idempotent handling of repeated notifications:** the `Payment.fonepay_prn` is unique; the commit step checks `Payment.status` first — if already `success`, the repeated callback/poll is a no-op. The `Sale.idempotency_key` also guards against double-commit.

**No stock locks held during the external wait.** Other registers can sell the same item. Stock is locked only at the commit step (step 4), briefly.

### 10.3 Offline cash checkout (queued sync)

1. The register is offline. The PWA service worker serves the POS shell from cache.
2. **Login offline:** the cashier must have logged in online at least once; the session cookie and user profile are cached locally (IndexedDB). Offline login validates against the cached credential hash; it does **not** extend server-side session validity. If the cached session is older than a threshold (e.g., 24h), offline login is refused — the cashier must reconnect.
3. **Session expiry mid-shift:** if the cached session expires while offline, the cashier can continue the current shift (the session validity is a server concern; offline mode is trust-the-device). On reconnect, the server re-validates the session; if the user was deactivated, queued sales are still accepted (they happened) but the user can't start new ones.
4. Cashier builds the cart. The local catalog (IndexedDB, last synced) provides product lookup and prices. Tax and rounding computed locally. **Cost prices are NOT in the cached catalog** — only `sale_price_paisa`, `tax_rate`, `tax_inclusive`, barcode, name, sku. Cost is a server concern; leaking it to the device is a risk with no offline benefit.
5. **Actions disabled offline:**
   - FonePay QR payment (needs internet).
   - Returns / credit notes (can't verify the original sale; deferred unless manager override + accountant-approved offline series).
   - Manager-approved discounts that require server-side approval (a manager can approve locally with a cached manager session, but it's flagged for review on sync).
   - Stock adjustments, stocktakes, transfers (require server-side stock truth).
   - New product creation, price changes.
   - Cashier shift close that posts to the server (the shift is closed locally; it syncs on reconnect).
6. On checkout, the PWA writes the sale to IndexedDB with a **receipt number from the local per-register series** (see 9.10) and an idempotency key. Stock is decremented in the local estimate only.
7. The receipt is printed locally (printer bridge on the LAN).
8. When the register reconnects, the service worker syncs queued sales to `POST /api/v1/pos/checkout/sync` one at a time (each with its idempotency key). The server:
   - Checks idempotency (skip if already synced).
   - Locks stock rows (sorted), validates, decrements, writes the sale with the offline receipt number, commits.
   - If stock is insufficient and `allow_negative` is false, **the sale is still recorded** with `stock_conflict = true` (the customer left with the goods — see 10.6). Not rejected.
9. Sync confirms the used receipt numbers and advances the server-side `ReceiptSequence` for the register.

**Offline FonePay:** not supported — FonePay needs internet. The POS disables the FonePay button when offline; only cash is available.

**Local storage loss:** a queued sale in IndexedDB that never syncs (the device is lost, or IndexedDB is cleared) is a **lost sale**. Mitigations:
- The register prints a receipt locally at sale time — the paper receipt is the source of truth offline.
- A daily offline backup of IndexedDB to the register's local disk (the PWA can write to the filesystem if the OS allows, or prompt the cashier to export).
- On sync, the server reports any gaps in the register's receipt series (a number used offline but no sale synced) — these are flagged for manual reconciliation against paper receipts.
- Accept this as a known risk; it's rare and the paper trail covers it.

### 10.4 Deadlock prevention

When locking stock rows `FOR UPDATE`, always lock in **sorted order by product_id**. Two simultaneous carts sharing products lock in the same order, so they wait rather than deadlock. Enforce this in the checkout code: sort the cart lines by `product_id` before the lock loop. **Apply the same sorted lock order to purchase receipts and transfer receipts** (Section 9.6).

### 10.5 Expiry and cleanup of abandoned open sales

- A `Sale` created by FonePay initiate (`status = open`) has `expires_at = now + 10 min`.
- A scheduled job (Django management command run by cron every minute) finds `open` sales past `expires_at`:
  - If the FonePay payment is still `initiated`: poll the FonePay status one final time. If success, commit the sale (10.2 step 4). If not success, mark `Sale.status = voided`, `Payment.status = expired`.
  - If the payment is `failed`: mark `Sale.status = voided`.
- This cleans up abandoned carts and prevents stock from being held (it was never locked during the wait, so no stock to release — just close the sale record).

### 10.6 "Paid but out of stock" — consistent handling

Both online FonePay and offline cash can hit "paid but stock insufficient." In **both** cases the customer is at the counter. The rule is the same:

- **Record the sale** with `status = paid` and `stock_conflict = true`.
- **Do not automatically refund.** A manager reviews `stock_conflict` sales and decides: accept (override stock, the item was there), or refund (process via FonePay refund for digital, or cash out for cash).
- The sale appears on the dashboard's "stock conflicts" tile for resolution.
- If `allow_negative` is true for the product, no conflict — stock goes negative and is flagged for recount (no `stock_conflict` flag, just a negative-stock report).

This fixes the inconsistency: previously offline cash recorded the sale, but online FonePay became a `payment_orphan` needing a refund. Now both record the sale and flag it. `payment_orphan` is reserved for a different case: the paid amount didn't match the sale total (under/overpayment), which is a payment error, not a stock error.

---

## 11. Customer

```
Customer(id, branch_id?, name, phone, email?, address?, tax_id?, created_at)
```

- A minimal entity. A walk-in sale has `customer_id = NULL` (walk-in).
- Named customers are optional, for tracking or returns. `branch_id` nullable — a customer can be org-wide or branch-specific.
- Not a loyalty program (non-goal). Just a reference for sales and credit notes.

---

## 12. POS Screen (per branch)

**Features (v1)**

- Barcode / SKU search to add items (local catalog when offline — no cost prices cached).
- Cart: qty, line and sale discounts, tax calc (sum per rate, round once), rounding on total.
- Totals: subtotal, tax, total, rounding.
- Payment: **cash** (works offline), **FonePay QR** (needs internet). Cash shows change due.
- Receipt: printable (printer bridge). Gapless number per register.
- Cash drawer: opens on cash sale (ESC/POS command to the printer's DK port).
- Cashier shift: open (starting cash), close (counted cash, variance).
- Hold / recall a sale; returns/refunds (credit note, manager approval above threshold).
- **Offline indicator:** the POS shows online/offline status. When offline: FonePay disabled, returns disabled, server-dependent actions disabled (see 10.3 step 5); cash sales queue for sync.
- **Low-number warning:** warns the cashier if the local receipt series is approaching the server's last confirmed number (only relevant if the accountant mandates a single branch series — see 9.4 open question).
- **Session status:** shows the cached session's age; warns if approaching the offline-login threshold.

---

## 13. Hardware: Printer and Cash Drawer (abstracted)

Printer model is not decided yet. The printer layer is an **interface** so the choice can come later.

```python
class PrinterDriver(Protocol):
    def print_receipt(self, receipt: ReceiptData) -> None: ...
    def open_drawer(self) -> None: ...
```

- **Network ESC/POS (recommended when decided):** printer on LAN with HTTP endpoint. SPA POSTs receipt + drawer-open command. Many thermal printers expose an HTTP endpoint.
- **USB thermal (local bridge):** needs a small local daemon on the register machine (browser can't write USB directly).
- **Browser print dialog (HTML fallback):** render receipt as HTML; kiosk mode for silent printing. Cheapest, needs a click unless kiosk mode.
- **Cash drawer** is connected to the receipt printer's DK port. Opening it is an ESC/POS command sent to the printer (same bridge). Some drawers are USB — those need a local bridge too.

The backend returns receipt data (JSON). The frontend formats it and sends it to whichever driver is configured. The driver choice is a per-register config setting, swappable.

---

## 14. Payments — FonePay Nepal (design pending official docs)

**The exact FonePay API is not confirmed.** Community libraries suggest:
- Dynamic QR creation with **HMAC-SHA512** request signing (not RSA).
- Status verification by **polling** the transaction status using a PRN (payment reference number).
- Asynchronous settlement — a status monitor confirms the payment, not a real-time callback.

**These are third-party libraries, not official documentation. One library notes its FonePay support is not yet certified against current merchant sandbox credentials.** Get the official docs from your bank before finalizing this section.

**Design for both callback and polling (polling is the fallback regardless):**

- **Initiate:** merchant generates a QR via the FonePay API (signed request). Returns a PRN and QR payload.
- **Verify:** either a FonePay callback (if the API supports one) or server-side polling by PRN until terminal status.
- **Commit:** on confirmed success, commit the sale (Section 10.2 step 4) — check the paid amount equals the sale total, then lock stock and commit.
- **Credentials:** merchant credentials (username, password, terminal ID, signing secret) stored in env / vault, never in repo. **Confirm the signing method (HMAC-SHA512 vs other) and credential format from the official docs.**
- **Sandbox:** FonePay provides a sandbox for development. Confirm the sandbox URL and credentials with the bank.
- **PCI scope:** minimal. The app never sees the customer's card or banking credentials — FonePay handles those. The app records the payment method, amount, and FonePay transaction ID.
- **Internet required:** FonePay needs internet at the register. Offline sales are cash-only.
- **Reconciliation:** a daily job reconciles FonePay settlement reports against recorded payments to catch mismatches (e.g., a payment that succeeded at FonePay but the commit failed). Flag mismatches for review. This is essential because settlement is asynchronous.
- **Polling fallback:** even if the API supports callbacks, implement polling as the fallback. Callbacks can be missed (network, downtime); polling by PRN guarantees the server eventually learns the payment status. The expiry job (10.5) does a final poll before voiding.

---

## 15. Network Outage Behavior

With ~10 stores in Nepal, a store losing internet is a regular event.

**Decision: offline-tolerant cash sales with queued sync.**

- The POS is a **PWA** (service worker + IndexedDB).
- **Login offline:** cached credential hash; refused if older than 24h threshold (see 10.3 step 2).
- **Session expiry mid-shift:** continue offline; re-validate on reconnect (see 10.3 step 3).
- **Disabled offline:** FonePay, returns/credit notes, manager-approved discounts (unless local manager session), stock adjustments, stocktakes, transfers, new products, price changes (see 10.3 step 5).
- **Local catalog:** product data cached on the device, refreshed periodically and on reconnect. **Cost prices are NOT cached** — only sale-relevant fields (see 10.3 step 4).
- **Stock:** best-effort local estimate (last known). True stock is reconciled on sync.
- **Queued sync:** on reconnect, sales sync one at a time with idempotency keys. Stock conflicts are flagged, not rejected (10.6).
- **Receipt numbers:** per-register gapless series, local copy while offline (see 9.10).
- **Local storage loss:** paper receipt is the source of truth offline; server flags receipt-number gaps on sync (see 10.3).

---

## 16. Data Import

Stores arrive with spreadsheets of products and opening stock.

- **Product import:** CSV/Excel upload. Map columns (name, sku, barcode, price, tax, category). Validate, show errors row by row, preview before commit.
- **Opening stock import:** per branch, product + quantity + cost. Writes opening stock movements (`purchase_in` with the given cost, sets weighted-average).
- **Idempotent:** re-importing the same file doesn't double the stock; key on SKU. Dry-run preview.
- Provide a template.

---

## 17. Centralized Dashboard

Org-level, aggregates across all branches. Plain SQL to start.

**Tiles**

- Total sales (today / week / month), by branch and trend. **"Today" uses the branch's timezone** (Section 18).
- Inventory value by branch (on-hand × weighted-average cost).
- Low-stock and out-of-stock alerts (cross-branch).
- Top products, slow movers.
- Branch comparison.
- Purchase orders pending.
- Stock conflicts (from offline sync and paid-but-out-of-stock) for manager review.
- FonePay reconciliation mismatches.

**Implementation**

- Plain SQL with the indexes from Section 8.
- If a query gets slow, add an **indexed view** or a **nightly job** that writes summary rows.
- Start without Redis. Add a cache only if a tile is slow and read often.

---

## 18. Time Zones

Each branch has a timezone.

```
Branch(id, org_id, name, code, timezone, address, is_active)
```

- `timezone` is an IANA zone (e.g., `Asia/Kathmandu`). Nepal is `Asia/Kathmandu` (UTC+5:45) for all branches, but store the field per branch in case of expansion.
- **"Today's sales" on the dashboard** uses the branch's timezone, not the server's. A sale at 23:00 Kathmandu is "today" in Kathmandu, not "tomorrow" in UTC.
- Reports group by the branch's local date.

---

## 19. Purchasing

```
PurchaseOrder(branch_id, supplier_id, status, ordered_at, expected_at?, received_at?)
  status in: draft, ordered, partial, received, cancelled
PurchaseOrderLine(po_id, product_id, qty_ordered, qty_received, unit_cost_paisa)
GoodsReceipt(po_id, received_at, by, lines[])
```

- **Partial receipts:** `qty_received` can be less than `qty_ordered`. Status moves to `partial` then `received` when complete.
- **Receiving updates cost:** each goods receipt writes a `purchase_in` movement with the received unit cost and recomputes the weighted-average cost (Section 9.6). **Lock `InventoryItem` rows in sorted order (by product_id) during receipt** to prevent deadlocks (10.4).
- **Cost on the line:** `PurchaseOrderLine.unit_cost_paisa` is the ordered cost; the goods receipt can record a different actual cost (supplier invoice) which is what updates the weighted-average.

---

## 20. Admin Screens

- **User management:** create/edit users, assign roles per branch, deactivate (soft delete, not hard delete — sales reference users).
- **Branch management:** create/edit branches, set timezone, activate/deactivate.
- **Product management:** create/edit, add barcodes, set prices, archive (soft delete). Per-branch price overrides.
- **Audit log viewer:** who did what (sale, void, return, stock adjust, transfer, login) with timestamp + branch.

---

## 21. Security

- HTTPS everywhere; HSTS at the proxy.
- **Session cookies + CSRF** (not JWT). httpOnly + SameSite. Revoked instantly on logout/deactivation.
- Password hashing (Django default PBKDF2/Argon).
- Rate limiting at the proxy (login, POS endpoints).
- Audit log: every material action (sale, void, return, stock adjust, transfer) logged with user, branch, timestamp.
- FonePay signing secret in vault, never in repo. Webhook/callback signature verification (method per official docs).
- Secrets in env / vault, never in repo.
- Input validation on all endpoints; ORM parameterized queries to prevent SQLi.
- CORS allowlist for the SPA origin.

---

## 22. Performance

- One Postgres, no replicas until measured.
- Checkout latency target: **P95 < 500ms.** Measure it. If it slips, fix the query/index, not the architecture.
- Indexes: `(branch_id, product_id)`, `(branch_id, created_at)`.
- `select_related`/`prefetch_related` to kill N+1.
- Cursor pagination on all list endpoints.
- No Redis/PgBouncer until a measured problem.

---

## 23. Deployment

- **Backend:** one Django project, Dockerized, gunicorn behind nginx/Caddy.
- **Frontend:** build to static assets; serve via the proxy. PWA manifest + service worker.
- **DB:** one PostgreSQL, managed (RDS) or self-hosted with WAL archiving.
- **Run:** one or two VMs, or a simple container platform. Not Kubernetes.
- **CI/CD:** tests -> build image -> deploy. Migrations run as a release step.
- **Backups:** nightly base + continuous WAL; tested restore (weekly drill).
- **Environments:** dev, staging, prod.

---

## 24. Testing

- Unit: tenancy isolation (no cross-branch leaks), RBAC (deny by default), role-per-branch, org-role with null branch, price/tax/rounding math (integer paisa, sum-per-rate-round-once), weighted-average cost recompute (including negative-stock case), negative-stock policy.
- Integration: checkout atomicity (rollback on failure, stock not decremented), gapless receipt numbers (failed checkout doesn't burn a number), FonePay commit (callback and polling paths), "paid but out of stock" records sale with `stock_conflict` (both online and offline), transfer partial receipt, return restock + credit note, offline sync with idempotency, expiry cleanup of abandoned sales, paid-amount-equals-total check.
- Concurrency: two simultaneous carts sharing products — no deadlock (sorted lock order), no oversell. Two concurrent purchase receipts touching same products — no deadlock.
- API tests: every endpoint's happy + permission-denied + tenant-denied paths.
- Load test checkout against the P95 < 500ms target before launch.
- Backup restore drill weekly.

---

## 25. Open Questions (remaining)

1. **FonePay official API:** get the docs from your bank. Confirm signing method (HMAC-SHA512?), callback vs polling, credential format, sandbox URL. This is the biggest unknown in section 14.
2. **IRD receipt numbering:** confirm with your accountant that per-register gapless series are accepted. If a single per-branch series is required, offline numbering needs rework (see 9.4).
3. **Costing confirmation:** weighted-average is the default. Confirm no jurisdiction requirement for FIFO.
4. **Discount-before-tax rule:** confirm this matches Nepal VAT rules.
5. **Negative stock default:** decide the default for `allow_negative` per product category.
6. **Printer model:** decide when ready — the interface is ready for any of the three options.
7. **FonePay merchant credentials:** obtain sandbox and production credentials.
8. **Per-branch tax override:** needed only if branches are in different tax regions. If all Nepal, one VAT rate — skip `BranchTaxRate`.

---

## 26. Milestones (with rough estimates for a 2-3 person team)

| # | Milestone | Est. | Notes |
|---|-----------|------|-------|
| 1 | Auth (sessions, RBAC, role-per-branch) + tenancy (branches, timezones) + catalog (products, barcodes) + inventory CRUD | 3-4 wks | Foundation; one branch first |
| 2 | POS checkout — cash, online only. Includes **discounts and tax** (sum per rate, round once), gapless receipts, deadlock-safe locks, weighted-average cost | 3-4 wks | The critical path. Tax and discounts are built here, not deferred to M7 |
| 3 | Printer integration (pick model, implement driver, cash drawer) + cashier sessions (shift open/close, variance) | 2 wks | Separated from checkout to reduce risk |
| 4 | FonePay QR payment integration (initiate, verify via callback **and** polling, reconciliation). **Get official docs first.** | 3 wks | External payment; needs sandbox creds and confirmed API |
| 5 | Offline cash sales (PWA, local catalog without cost prices, offline login, disabled-action rules, queued sync, stock_conflict, local storage loss mitigations) | 4-5 wks | Riskiest frontend work; buffer added. **Blocked on IRD numbering confirmation (Q2)** |
| 6 | Purchasing (POs, partial receipts, cost update on receipt with sorted locks) + stock transfers (in-transit, partial receipt) + stocktakes | 3 wks | |
| 7 | Returns/credit notes (with own numbering series, offline returns if approved) | 2 wks | Tax/discounts already in M2 |
| 8 | Centralized dashboard (cross-branch, per-branch timezone) + low-stock alerts + FonePay reconciliation | 2 wks | |
| 9 | Data import (products + opening stock) + admin screens (user/branch/product management, audit log) | 2 wks | |
| 10 | Hardening, load test (P95 < 500ms), backup restore drill, launch | 2 wks | |

**Total:** ~26-33 weeks for a 2-3 person team. Milestones 1-5 are the core POS; 6-10 are the rest.

**Order rationale:** blockers resolved first. Checkout (M2) is online-only first, with tax and discounts built in (not deferred). Offline (M5) is after online checkout is proven, with buffer for its risk. FonePay (M4) needs official docs before starting — get them early. Printer and sessions (M3) are separated from checkout. Returns (M7) depends on the numbering scheme being confirmed.
