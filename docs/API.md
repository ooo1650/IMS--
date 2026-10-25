# API reference

Base path: **`/api/v1/`**. All routes are defined in `backend/ims/urls.py`. Generated from the URL resolver on 2026-10-25.
A browsable list is at `GET /api/v1/` (DRF API root) when logged in.

## Conventions

| Topic | Rule |
|---|---|
| Auth | Django **session cookie**. `GET auth/csrf` first, then send the `X-CSRFToken: <csrftoken cookie>` header on every non-GET request. |
| Branch | Send **`X-Branch-Id: <id>`** on every request; the session's active branch is the fallback. Branch-scoped endpoints return 403 if you have no role at that branch. |
| Permissions | Written below as `branch:perm` (role at the active branch, or an org role) or `org:perm` (org role only). Role → perms map: `backend/accounts/roles.py`. |
| Money | Integer **paisa** (`*_paisa`). Rs 120.50 = `12050`. Rates are decimal strings (`"13.00"`). Percent discounts are **basis points** (`1000` = 10%). |
| Dates | ISO-8601 UTC datetimes; BS dates as `"2083.07.08"`; fiscal year `"2083-84"`. |
| Lists | Cursor pagination `{results, next, previous}` (50 per page; `?page_size=` up to 500). Small reference lists (branches, categories, suppliers, users, tax categories) return a plain array. |
| Errors | `400 {"detail": "..."}` or field errors `{"field": ["msg"]}`; stock errors add `short_lines: [{sku, available, qty}]`. `403` permission/branch, `404` not found or other branch. |
| Idempotency | Checkout endpoints take `idempotency_key` (UUID). A retry returns the existing sale (`200` instead of `201`). |
| Exports | Reports take **`?export=xlsx|pdf|xml|csv`** (not `format`, which DRF reserves). |
| Methods | DELETE is not allowed anywhere except `pos/held/{id}/` (legal records are append-only). |

## Auth & tenancy
| Method & path | Perm | Notes |
|---|---|---|
| `GET auth/csrf` | public | Sets the `csrftoken` cookie. |
| `POST auth/login` | public | `{username, password}` → `Me`. Auto-selects the branch if the user has only one. |
| `POST auth/logout` | auth | |
| `GET auth/me` | auth | `{id, username, name, org_role, org_permissions[], branches:[{id,code,name,timezone,role,permissions[]}], active_branch_id}` |
| `POST auth/active-branch` | auth | `{branch_id}` → `Me` (stored in the session). |
| `GET auth/roles` | auth | Role → permission map from `roles.py`: `[{role, label, scope: org\|branch\|any, permissions[]}]`. Added during the in-progress UI work. |
| `GET/POST users/`, `GET/PUT/PATCH users/{id}/` | org:users.manage | `{username, first_name, last_name, email, is_active, password?, roles:[{branch: id\|null, role}]}`. `is_active=false` revokes access immediately. |
| `GET branches/` (accessible), `GET branches/directory/` (all active, minimal) | auth | |
| `POST/PUT/PATCH branches/…` | org:branches.manage | `{org, name, code, timezone, address, phone, is_active}` |
| `GET/POST/PUT/PATCH registers/…` | read auth / write org:branches.manage | `{branch, code, name, printer_driver, printer_config:{url}, is_active}`; filter `?branch=` |

## Catalog
| Method & path | Perm | Notes |
|---|---|---|
| `GET products/` | catalog.view | `?q=` (name/SKU/barcode), `?category=`, `?archived=1|all`. Includes `branch_price_paisa`, `on_hand` for the active branch. `cost_price_paisa` is hidden from cashiers. |
| `POST products/`, `PUT/PATCH products/{id}/` | catalog.manage | `{sku, name, category, supplier, unit, cost_price_paisa, sale_price_paisa, tax_inclusive, tax_category, hs_code, excise_rate, input_credit_blocked_pct, barcodes:[{code,type,pack_qty}]}`. `tax_rate` is derived from the category. |
| `POST products/{id}/archive/`, `…/restore/` | catalog.manage | Soft delete. |
| `POST products/{id}/branch-price/` | catalog.manage | `{sale_price_paisa \| null}` for the active branch. |
| `GET/POST/PUT/PATCH categories/…` | read catalog.view / write catalog.manage | |
| `GET/POST/PUT/PATCH suppliers/…` | read catalog.view / write purchasing.manage or org catalog.manage | `{name, pan, phone, email, address}` |
| `GET pos/catalog/` | branch:pos.sell | Offline snapshot: products with branch price, resolved tax (rate, kind, excise, levy), barcodes, stock estimate. **No costs.** |

## POS
| Method & path | Perm | Body / notes |
|---|---|---|
| `GET pos/config` | branch:pos.sell | Branch, seller (name, PAN), fiscal_year, invoice_series, abbreviated/delivery limits, digital VAT discount config, cash rounding, discount limit, return threshold, FonePay backend, registers (printer, next number, open shift). |
| `POST pos/shifts/open/` | branch:shift.manage | `{register_id, opening_cash_paisa}` |
| `POST pos/shifts/{id}/close/` | shift.manage (others' shifts: reports.view) | `{counted_cash_paisa, offline?}` → expected, counted, variance |
| `GET pos/shifts/`, `…/{id}/summary/` | shift.manage (own) / reports.view | |
| `POST pos/checkout` | branch:pos.sell | **Cart** + `{register_id, idempotency_key, method: cash\|card\|wallet\|bank, tendered_paisa (cash), payment_ref (non-cash)}` → `201 Sale` |
| `POST pos/checkout/initiate` | branch:pos.sell | Cart + `{register_id, idempotency_key}` → Sale (status open) + `qr_payload`, `prn` |
| `GET pos/sale/{id}/payment-status` | branch:pos.sell | Polls FonePay; `{status, payment_status, receipt_number, stock_conflict, sale}` |
| `POST pos/fonepay/callback` | public, HMAC-signed | `{prn, status, amount, trx_id, signature}`; re-checks with the gateway |
| `POST pos/fonepay/mock-pay/{prn}` | branch:pos.sell, mock only | `{paid_paisa?, fail?}`, dev helper |
| `POST pos/checkout/sync` | branch:pos.sell | Offline sale: Cart (lines include `unit_price_paisa, tax_rate, tax_inclusive`) + `{register_id, idempotency_key, tendered_paisa, fiscal_year, created_at, receipt_seq (register series) \| provisional_ref (branch series), client_total_paisa, offline_discount_approved}` → Sale + `receipt_gaps` |
| `GET/POST pos/held/`, `DELETE pos/held/{id}/` | branch:pos.sell | `{register, label, cart}` |
| `GET pos/registers/{id}/receipt-gaps` | branch:reports.view | `?fiscal_year=` |
| `GET/POST/PATCH customers/…` | branch:pos.sell | `?q=` |

**Cart** (shared by checkout, initiate, sync):
```json
{
  "lines": [{"product_id": 1, "qty": 2, "discount_type": "none|amount|percent", "discount_value": 0, "discount_reason": ""}],
  "sale_discount_type": "none", "sale_discount_value": 0, "sale_discount_reason": "",
  "buyer_name": "", "buyer_address": "", "buyer_pan": "",
  "customer_id": null,
  "approver_username": "", "approver_password": ""
}
```
Rules: discounts need a reason. Discounts above `cashier_max_discount_bp` need `discount.approve` (your own, or the approver fields). The invoice is abbreviated if the total ≤ the limit and there's no PAN; otherwise full, which needs `buyer_name` and `buyer_address`, and a PAN must be 9 digits.

## Sales, invoices, notes
| Method & path | Perm | Notes |
|---|---|---|
| `GET sales/` | pos.sell (own) / reports.view (all) | Filters: `q` (invoice no./buyer/PAN), `status`, `unprinted=1`, `cancelled=1`, `conflicts=1`, `review=1`, `offline=1` |
| `GET sales/{id}/` | branch:pos.sell | Full Sale incl. lines, payments, credit_notes, debit_notes, cbms_status, print_count |
| `GET sales/lookup/?receipt=` | branch:pos.sell | Find by invoice number (returns) |
| `GET sales/{id}/receipt/` | branch:pos.sell | Document context for display (**does not** count as a print) |
| `POST sales/{id}/print/` | branch:pos.sell | `{kind: original\|delivery}` → `{kind, copy_no, document, html}`. The first print is the original; later ones are "COPY OF ORIGINAL — Copy #n". |
| `POST sales/{id}/void/` | branch:pos.void | `{reason}`, cancels. Refused if credit/debit notes exist. |
| `POST sales/{id}/returns/` | branch:returns.create | `{register_id, reason, refund_method, lines:[{sale_line_id, qty}], approver_username?, approver_password?}` → CreditNote |
| `POST sales/{id}/debit-note/` | branch:pos.void | `{taxable_paisa, reason, tax_rate?}` → DebitNote |
| `POST sales/{id}/resolve-conflict/` | branch:conflicts.resolve | `{resolution: accepted\|refunded, reason?}` |
| `GET credit-notes/`, `…/{id}/`; `POST credit-notes/{id}/print/` | branch:returns.create | |
| `GET debit-notes/`, `…/{id}/`; `POST debit-notes/{id}/print/` | branch:reports.view | |

**Sale** (main fields): `id, receipt_number, fiscal_year, series, receipt_seq, status, invoice_type, buyer_name/address/pan, payment_method, subtotal_paisa, discount_total_paisa, tax_total_paisa, excise_total_paisa, hst_total_paisa, esf_total_paisa, digital_vat_discount_paisa, round_adjust_paisa, total_paisa, issued_at, issued_bs, transaction_at, transaction_bs, voided_at, void_reason, offline, provisional_ref, stock_conflict, conflict_resolution, needs_review, print_count, lines[], payments[], credit_notes[], debit_notes[], cbms_status`.

## Inventory
| Method & path | Perm | Notes |
|---|---|---|
| `GET inventory/` | branch:inventory.view | `?q=`, `?low=1`, `?negative=1` |
| `PATCH inventory/{id}/` | branch:inventory.adjust | `{reorder_point, reorder_qty, allow_negative}` |
| `POST inventory/adjust/` | branch:inventory.adjust | `{product, qty (signed), reason}` |
| `GET movements/` | branch:inventory.view | `?product=`, `?type=` |
| `GET transfers/`, `…/{id}/` | branch:inventory.view | Both the sending and receiving branch see them |
| `POST transfers/` | branch:transfer.manage | `{to_branch, note, lines:[{product, qty_sent}]}` |
| `POST transfers/{id}/dispatch/` · `…/cancel/` | transfer.manage (sending branch) | |
| `POST transfers/{id}/receive/` | transfer.manage (receiving branch) | `{lines:[{id, qty_received, variance_reason}]}` |
| `GET/POST stocktakes/` | branch:stocktake.manage | create `{category?}` |
| `GET/POST stocktakes/{id}/lines/` | branch:stocktake.manage | `?q=`; POST `{lines:[{id, counted_qty, reason}]}` |
| `POST stocktakes/{id}/finish/` | branch:stocktake.manage | Posts variances. A large variance without a reason → 400. |

## Purchasing
| Method & path | Perm | Notes |
|---|---|---|
| `GET purchase-orders/` | purchasing.manage or reports.view | `?status=draft,ordered,partial` |
| `POST purchase-orders/`, `PUT/PATCH …/{id}/` (draft only) | branch:purchasing.manage | `{supplier, expected_at, note, lines:[{product, qty_ordered, unit_cost_paisa}]}` |
| `POST purchase-orders/{id}/order/` · `…/cancel/` | purchasing.manage | |
| `POST purchase-orders/{id}/receive/` | purchasing.manage | `{supplier_invoice_ref, supplier_invoice_date (YYYY-MM-DD), is_import, lines:[{po_line, qty, unit_cost_paisa?}]}` → PO with `receipts[]` (VAT, claimable VAT, BS dates) |
| `GET/POST purchase-returns/` | branch:purchasing.manage | `{supplier, goods_receipt?, supplier_note_ref, reason, lines:[{product, qty, unit_cost_paisa?}]}` |

## Reports
| Method & path | Perm | Notes |
|---|---|---|
| `GET reports/dashboard` | org:dashboard.view | Org KPIs per branch, trend, top products, slow movers, low stock, conflicts, FonePay mismatches |
| `GET reports/branch` | branch:reports.view | Same for the active branch |

## Tax & IRD (compliance)
Report scope: org users (org `reports.view` or `dashboard.view`) get **all branches** by default, or `?branch=<id>`. Branch users get their active branch.

| Method & path | Perm | Notes |
|---|---|---|
| `GET compliance/fiscal-years` | auth | `{current, current_month, years[], months[{no,name}], today_bs}` |
| `GET compliance/reports/{name}` | reports scope (`audit-trail` needs audit.view) | `name` ∈ `sales-book, purchase-book, sales-returns, purchase-returns, vat-return, cancelled-bills, stock-reconciliation, cbms-sync, audit-trail`. Query: `fy=2083-84`, `month=1..12` (1 = Shrawan; omit for the whole year), `branch=`, `export=`. JSON: `{title, subtitle, columns:[{key,label,kind}], rows[], totals{}, notes[], summary?}` (`vat-return` adds `summary: {output_vat, input_vat, net, nil_return, due_date_ad, due_date_bs}`) |
| `GET compliance/reports/accountant-export` | reports scope | Zip of xlsx + xml books for the period |
| `GET compliance/turnover` | reports scope | FY turnover vs CBMS (Rs 10/20 crore) and VAT thresholds + `alerts[]` |
| `GET compliance/documents/{invoice\|credit_note\|debit_note}/{id}` | reports scope | Watermarked "PREVIEW" HTML; `?export=pdf` for PDF. Not a print. |
| `GET/PATCH compliance/settings` | org:branches.manage | Org fields (`org_name, org_name_np, org_pan, org_address, …`) + ComplianceSettings. `cbms_password` is write-only; the response has `cbms_password_set`. |
| `GET compliance/tax-categories/` | auth | With `rates[]`, `current_vat_rate`, `current_levy_rate` |
| `POST compliance/tax-categories/`, `PATCH …/{id}/` | org:catalog.manage | `{code, name, kind, levy_type, is_active}` |
| `POST compliance/tax-categories/{id}/rates/` | org:catalog.manage | `{effective_from, vat_rate, levy_rate, note}` |
| `GET compliance/cbms/` | reports scope | `?status=pending\|synced\|failed` |
| `GET compliance/cbms/status/` | reports scope | `{enabled, paused_reason, pending, synced, failed, oldest_pending_at}` |
| `POST compliance/cbms/sync-now/` · `…/retry-failed/` | org:branches.manage | |

## Admin & import
| Method & path | Perm | Notes |
|---|---|---|
| `GET audit-log/` | org:audit.view (all) or branch:audit.view | `?action=` prefix, `?user=`, `?branch=`. Rows include `changes` (before/after). |
| `GET import/template/{products\|opening-stock}` | auth | CSV template |
| `POST import/products` | org:catalog.manage | multipart `file`, `dry_run=true\|false` |
| `POST import/opening-stock` | branch:import.run | multipart `file`, `dry_run` |

## Example: cash sale with curl
```bash
B=http://localhost:5173/api/v1; J=cookies.txt
curl -s -c $J -b $J $B/auth/csrf >/dev/null; T=$(grep csrftoken $J | awk '{print $7}')
curl -s -c $J -b $J -H "X-CSRFToken: $T" -H 'Content-Type: application/json' \
  -d '{"username":"cashier","password":"demo-pass-123"}' $B/auth/login
T=$(grep csrftoken $J | awk '{print $7}')   # login rotates the token
curl -s -b $J -H "X-CSRFToken: $T" -H 'X-Branch-Id: 1' -H 'Content-Type: application/json' \
  -d '{"register_id":1,"idempotency_key":"'$(uuidgen)'","tendered_paisa":200000,"lines":[{"product_id":1,"qty":1}]}' \
  $B/pos/checkout
```
