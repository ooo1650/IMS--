# Code guide

How the code is organised, and how to make the common kinds of change safely.

## Repository layout
```
IMS/
├── CLAUDE.md                 context for AI sessions (read first)
├── README.md                 run/deploy overview
├── ims-design-doc (3).md     original architecture spec
├── payment.md                Nepal IRD / VAT / CBMS requirements
├── docker-compose.yml        Postgres + gunicorn + nginx + certbot
├── deploy/                   nginx/ (config templates, TLS setup), certbot-loop.sh, crontab, backup.sh, restore-drill.sh
├── docs/                     these docs + IRD questions, templates, user manual
├── backend/
│   ├── ims/                  settings.py, urls.py (all routes), api.py (error handler, BranchScopedMixin), testutils.py
│   ├── tenancy/ accounts/ catalog/ inventory/ sales/ purchasing/ reporting/ admin_extras/ compliance/
│   ├── scripts/              gen_pricing_vectors.py, test_with_postgres.py
│   ├── fonts/                Noto Sans Devanagari (PDF rendering)
│   └── requirements*.txt
└── frontend/
    ├── src/{App,main,auth}.tsx, index.css
    ├── src/components/       Layout, ui (Button/Modal/Field/useLoad/usePaged…), charts, ProductPicker, ErrorBoundary, Icon (inline SVG icons, part of an in-progress restyle)
    ├── src/lib/              api, pricing (+test), offline, sync, printer, money, types
    ├── src/pages/            one screen per file
    └── public/               sw.js (service worker), manifest
```

## Backend, file by file

### `ims/`
- `settings.py`: env-driven config. `IMS = {...}` holds business knobs (cash rounding, discount limit, return threshold). FonePay guard rails only (`FONEPAY_MOCK_ALLOWED`, `FONEPAY_ALLOWED_HOSTS`); its real configuration is in the database.
- `urls.py`: **every API route** (router + explicit paths) under `/api/v1/`.
- `api.py`: `exception_handler` (ValidationError → 400, adds `short_lines` for stock errors) and `BranchScopedMixin` (filters querysets to `request.branch`, stamps branch on create).
- `testutils.py`: `World` test fixture (2 branches, a register each, users per role, 3 products with stock).

### `accounts/`
`roles.py` (roles → permission strings), `models.py` (`User.has_branch_perm`, `accessible_branch_ids`), `permissions.py` (`HasBranchPerm`, `HasOrgPerm`, `PerActionPermissionMixin`, `can()`, `verify_approver()` for manager overrides), `views.py` (login/logout/me/active-branch, users CRUD).

### `catalog/`
`models.py`: Product (has `tax_category`, `hs_code`, `excise_rate`, `input_credit_blocked_pct`) and `tax_profile(product, date)` → `(vat_rate, kind, levy_type, levy_rate, code)`, which honours effective-dated rates. `views.py`: product CRUD with archive/restore/branch price, and `PosCatalogView` (the offline catalog, no costs).

### `inventory/services.py`
- `lock_items(branch, product_ids)`: creates missing rows, then locks them sorted. **Use it for every stock write.**
- `receive_stock` (recomputes the weighted average), `adjust_stock`, transfers (`dispatch_transfer`, `receive_transfer`, `cancel_transfer`), stocktakes (`start_stocktake`, `finish_stocktake`).

### `sales/`
- `pricing.py`: pure tax engine (`compute_totals`). See the module docstring for the formulas.
- `numbering.py`: `allocate(doc_type, branch, register, fy)`, `claim_offline`, `receipt_gaps`, `fiscal_year_for`.
- `services.py`: `price_cart`, `decide_invoice_type`, `checkout_cash` (also card/wallet/bank), `initiate_fonepay` / `commit_fonepay` / `refresh_fonepay_status` / `expire_open_sales`, `sync_offline_sale`, `void_sale`, `resolve_conflict`, `create_return`, `create_debit_note`, `record_print`, shift functions. `_issue()` is the single place an invoice gets its number.
- `fonepay.py`: gateway interface (`MockFonepayGateway`, an unverified `HttpFonepayGateway` skeleton, HMAC signing).
- `views.py`: POS endpoints, sales list/detail, print endpoints, credit/debit note viewsets.

### `purchasing/services.py`
`receive_goods` (purchase VAT via `purchase_vat()`, rounded once per rate), `create_purchase_return`, `mark_ordered`, `cancel_po`.

### `compliance/`
| File | Purpose |
|---|---|
| `models.py` | TaxCategory and TaxCategoryRate, ComplianceSettings (`get_settings()`), CbmsPost, MockCbmsRecord |
| `appendonly.py` | `AppendOnlyModel` base: blocks changes after `is_issued()` except `MUTABLE_AFTER_ISSUE`, blocks delete |
| `bs.py` | BS conversion, fiscal year labels/bounds, BS month ranges |
| `words.py` | amount in words (lakh/crore) |
| `crypto.py` | Fernet encryption for the CBMS password |
| `cbms.py` | payload builders, enqueue functions, `send` (HTTP or `mock://`), `post_one`, `sync_pending`, `status_summary` |
| `documents.py` | `invoice_context` / `credit_note_context` / `debit_note_context` → `render_html` (template) / `render_pdf` (ReportLab) |
| `reports.py` | `Period`, the statutory reports, `turnover`, `fiscal_years`; registry `REPORTS` |
| `exports.py` | `to_xlsx/to_xml/to_pdf/to_csv`, `zip_reports` |
| `views.py` | settings, tax categories, CBMS dashboard, reports and exports, documents |
| `migrations/0002` | seeds tax categories, maps products |
| `migrations/0003` | Postgres triggers + auditor views (plus a BS backfill) |
| `management/commands/` | `cbms_sync`, `refresh_auditor_views`, `generate_listing_evidence` |

### `admin_extras/`
`models.py` (`AuditLog`, `log_action()`), `audit.py` (signals on the models in `AUDITED`, recording before/after), `imports.py` (CSV import with dry run), `management/commands/seed_demo.py`.

### Management commands
`seed_demo`, `expire_open_sales`, `reconcile_fonepay`, `reconcile_stock [--fix]`, `cbms_sync [--force]`, `refresh_auditor_views`, `generate_listing_evidence --out DIR [--create-test-bills]`.

## Frontend, file by file
- `lib/api.ts`: `api<T>(path, {method, body, form, branch})`. Throws `ApiError` (HTTP error, `.data` has field errors) or `NetworkError` (offline).
- `lib/pricing.ts`: port of `sales/pricing.py`. **Keep in sync.**
- `lib/offline.ts`: IndexedDB stores (`kv`, `catalog`, `queue`), offline login, `queueOfflineSale` (atomic with the local counter).
- `lib/sync.ts`: online state (`useOnline`), queue sync loop, offline shift-close posting.
- `lib/printer.ts`: `printDocument(register, job)` / `printResult`. Full invoices and notes → HTML print dialog (Devanagari). Abbreviated invoices and offline slips → ESC/POS when the till has a thermal printer; the drawer pulse rides on the same job (sent once).
- `lib/devices/`: browser-only hardware. `settings.ts` (per-till settings in IndexedDB, `useDeviceSettings`), `transport.ts` (WebUSB / WebSerial / HTTP byte sinks), `escpos.ts` (+test: command builder, ASCII transliteration, pole display bytes), `scanner.ts` (+test: keyboard-wedge detection, beep), `display.ts` (customer display over BroadcastChannel + pole display).
- `lib/barcode.ts` (+test, round-tripped through ZXing's readers): EAN-13/UPC-A check digits, in-store `2…` codes, EAN-13 and Code 128 SVG for labels.
- `components/DeviceSettings.tsx` (POS → Devices), `CameraScanner.tsx` (native BarcodeDetector, lazy ZXing fallback), `LabelPrint.tsx` (Products → select → Print labels), `ProductDetail.tsx`. `pages/CustomerDisplay.tsx` is served at `/customer-display` without login.
- `components/ui.tsx`: `useLoad(fn, deps)`, `usePaged(path)` (cursor pages), Button/Modal/Field/Badge/Status/Tabs.
- `pages/Pos.tsx`: cart, invoice-type logic (mirrors the server), payment modals, offline sale, returns, held carts, shifts, register tools.
- `pages/Compliance.tsx`: Tax & IRD (reports + export, CBMS, turnover, settings, tax categories).

## How to…

### Add a business setting that admins can change
Add a field to `ComplianceSettings` → `makemigrations` → it shows up automatically in `SettingsSerializer` (it uses `exclude`) → add an input in `pages/Compliance.tsx` `Settings()`. If the POS needs it, expose it in `PosConfigView` and `types.ts > PosConfig`.

### Change a VAT rate (budget day)
Don't edit code. In Tax & IRD → Tax categories → **Add rate** with the effective date. Existing invoices keep their stored rates.

### Change the tax maths
Edit `sales/pricing.py` **and** `frontend/src/lib/pricing.ts`. Run the old vectors against the new code first (they must still match unless the change is intentional), then `python scripts/gen_pricing_vectors.py`, `manage.py test sales.tests.test_pricing`, `npm test`.

### Add a new statutory report
Write `def my_report(period, branches)` in `compliance/reports.py` returning `{"title","subtitle","columns":[(key,label,kind)],"rows","totals","notes"}` and register it in `REPORTS`. It automatically gets JSON, xlsx/xml/pdf/csv export, and a slot in the UI dropdown once you add it to `REPORTS` in `pages/Compliance.tsx`.

### Add a new tax document type or change the invoice layout
The context builder is in `compliance/documents.py`, the HTML is `compliance/templates/compliance/document.html`, and the PDF is `render_pdf`. Printing goes through `services.record_print` so print-once holds.

### Add an endpoint
Put the logic in a service function, then add a thin view. Use `BranchScopedMixin` plus `default_perms` / `action_perms` for branch data, and `HasOrgPerm` for org-wide data. Register it in `ims/urls.py`, add a test (happy path + permission denied + other-branch denied), and add the frontend type in `lib/types.ts`.

### Add a model that holds legal records
Subclass `AppendOnlyModel`, implement `is_issued()` / `MUTABLE_AFTER_ISSUE`, add a Postgres trigger in a new migration (copy the pattern in `compliance/migrations/0003`), and add the model label to `AUDITED` in `admin_extras/audit.py`.

## Conventions
- Business logic lives in `services.py`; views stay thin. Services take model instances and raise `ValidationError`.
- Every write that matters calls `log_action(...)` (business event). Audited models also get automatic before/after rows.
- Comments explain *why* and cite the spec section (`§10.2`, `payment.md Step 2`).
- Frontend: one page per file, no UI framework, tokens in `index.css` (light/dark), `npr()` for display, `toPaisa()` for input.

## Testing
- `backend/*/tests/`: unit and integration tests using `ims.testutils.World`.
- Postgres-only tests (triggers, concurrency) skip on SQLite. Run them with `scripts/test_with_postgres.py`.
- `frontend/src/lib/pricing.test.ts` runs the shared vectors.
- Before saying "done": backend tests on both databases, `npx tsc -b`, `npm test`, `npm run build`, and click through the changed screen in a browser.
