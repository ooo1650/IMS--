# Code guide

How the code is organised, and how to make the common kinds of change safely.

## Repository layout
```
IMS/
├── CLAUDE.md                 context for AI sessions (read first)
├── README.md                 run/deploy overview
├── ims-design-doc (3).md     original architecture spec
├── payment.md                Nepal IRD / VAT / CBMS requirements
├── docker-compose.yml        db, migrate, backend, worker, web (nginx), certbot, backup
├── .github/workflows/ci.yml  CI: backend on Postgres + ruff, frontend, console, image build
├── deploy/                   nginx/ (templates, snippets incl. JSON errors), postgres/pg_hba.conf, certbot-loop.sh,
│                             backup.sh, restore-drill.sh, pitr-prepare.sh, crontab (no-Docker installs only)
├── docs/                     these docs + IRD questions, templates, user manual
├── backend/
│   ├── ims/                  settings.py, urls.py (all routes), api.py (error handler, to_int, mixins), errors.py, testutils.py
│   ├── tenancy/ accounts/ catalog/ inventory/ sales/ purchasing/ reporting/ admin_extras/ compliance/
│   ├── scripts/              gen_pricing_vectors.py, test_with_postgres.py, export_schema.py
│   ├── ruff.toml             backend lint (`ruff check .`)
│   ├── fonts/                Noto Sans Devanagari (PDF rendering)
│   └── requirements*.txt
└── frontend/
    ├── src/{App,main,auth}.tsx, index.css
    ├── src/components/       Layout, ui (Button/Modal/Field/QtyInput/MoneyInput/useLoad/usePaged…), SyncQueue, charts, ProductPicker, ErrorBoundary, Icon
    ├── src/lib/              api (+test), pricing (+test), money (+test), offline, sync (+test), printer, types
    ├── src/pages/            one screen per file
    └── public/               sw.js (service worker), manifest
```

## Backend, file by file

### `ims/`
- `settings.py`: env-driven config: secrets and guard rails only (`IMS_TRUSTED_PROXIES`, `IMS_PLATFORM_*`, `IMS_TEST_LOGIN`, FonePay `FONEPAY_MOCK_ALLOWED` / `FONEPAY_ALLOWED_HOSTS`). Shop rules (cash rounding, discount limit, return threshold) are per business in `tenancy.BusinessRules`; email and sign-in limits in `admin_extras.SystemSettings`; tax in `ComplianceSettings`.
- `urls.py`: **every API route** (router + explicit paths) under `/api/v1/`.
- `api.py`:
  - `exception_handler`: service `ValidationError` → 400 (adds `short_lines` for stock errors); DRF wording rewritten to plain sentences (`plain_errors`); CSRF / signed-out messages in plain words; a late `OverflowError`/`DataError` becomes a 400; logs a failed manager approval (`failed_login_key`) after the rollback.
  - `to_int(value, field, default, min_value, max_value)` and `lines_of(request)`: the only way views read raw numbers and line lists (caps `MAX_PAISA`, `MAX_QTY`, `MAX_INT`).
  - `BranchScopedMixin` / `OrgScopedMixin` / `OrgFieldsMixin`: tenant scoping.
- `errors.py`: JSON instead of Django's HTML for `/api/` (CSRF failure, 400, 404, 500).
- `test_input_numbers.py`: fires bad numbers at every write endpoint; add new numeric inputs there.
- `testutils.py`: `World` test fixture (2 branches, a register each, users per role, 3 products with stock).

### `accounts/`
`roles.py` (roles → permission strings), `models.py` (`User.has_branch_perm`, `accessible_branch_ids`), `permissions.py` (`HasBranchPerm`, `HasOrgPerm`, `PerActionPermissionMixin`, `can()`, `verify_approver()` for manager overrides, which shares the sign-in lockout), `otp.py` (emailed codes; `can_send` returns a `Wait` with `retry_after`), `views.py` (login/logout/me/active-branch, users CRUD; `too_many()` builds every 429 with `code` + `retry_after`; `login_locked_out()` returns the seconds left).

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
`models.py` (`AuditLog`, `log_action()`), `audit.py` (signals on the models in `AUDITED`, recording before/after), `imports.py` (CSV or .xlsx import with dry run), `platform.py` (operator console API), `management/commands/` (`seed_demo`, `run_scheduler`, `create_platform_admin`, `ensure_db_roles`).

### Management commands
`seed_demo`, `expire_open_sales`, `reconcile_fonepay`, `reconcile_stock [--fix]`, `cbms_sync [--force]`, `refresh_auditor_views`, `generate_listing_evidence --out DIR [--create-test-bills]`.

## Frontend, file by file
- `lib/api.ts`: `api<T>(path, {method, body, form, branch})`. Throws `ApiError` (`.message` is a readable sentence with field labels and row numbers, `.code` the server's reason, `.retryAfter` seconds for 429s) or `NetworkError` (offline). `formatWait()` for countdowns.
- `lib/money.ts`: `npr()` to show; `toPaisa()` / `toBasisPoints()` / `parseWhole()` to read what people type ("Rs 500", "500/-", "10%", Nepali digits); `moneyProblem()` / `percentProblem()` / `wholeProblem()` say why an entry can't be used.
- `lib/pricing.ts`: port of `sales/pricing.py`. **Keep in sync.**
- `lib/offline.ts`: IndexedDB stores (`kv`, `catalog`, `queue`), offline login, `queueOfflineSale` (atomic with the local counter).
- `lib/sync.ts`: online state (`useOnline`), queue sync loop (a 401 pauses it, any other refusal marks that sale and carries on), offline shift-close posting. `components/SyncQueue.tsx` is the sidebar panel for queued/failed sales.
- `lib/printer.ts`: `printDocument(register, job)` / `printResult`. Full invoices and notes → HTML print dialog (Devanagari). Abbreviated invoices and offline slips → ESC/POS when the till has a thermal printer; the drawer pulse rides on the same job (sent once).
- `lib/devices/`: browser-only hardware. `settings.ts` (per-till settings in IndexedDB, `useDeviceSettings`), `transport.ts` (WebUSB / WebSerial / HTTP byte sinks), `escpos.ts` (+test: command builder, ASCII transliteration, pole display bytes), `scanner.ts` (+test: keyboard-wedge detection; any screen with buttons that takes scans must use `useScannerInput`, else the scanner's Enter presses the focused button; `diagnoseScan` for the scanner test, beep), `display.ts` (customer display over BroadcastChannel + pole display).
- `lib/barcode.ts` (+test, round-tripped through ZXing's readers): EAN-13/UPC-A check digits, in-store `2…` codes, EAN-13 and Code 128 SVG for labels. Label bar width comes from `labelBarWidth` (whole 203 dpi dots on rolls), never from the height left on the label.
- `components/DeviceSettings.tsx` (POS → Devices), `ScannerTest.tsx` (Devices → test a keyboard-mode scanner), `CameraScanner.tsx` (native BarcodeDetector, lazy ZXing fallback; also accepts a wedge scan while open, so turn off any other `useScannerInput` behind it), `LabelPrint.tsx` (Products → select → Print labels), `ProductDetail.tsx`. `pages/CustomerDisplay.tsx` is served at `/customer-display` without login.
- `components/ui.tsx`: `useLoad(fn, deps)`, `usePaged(path)` (cursor pages), `Button` (`why=` says why it's disabled), `Modal` (won't close on a stray click once edited), `Field` (`error=`), `QtyInput`, `MoneyInput`, `ViewOnly`, Badge/Status/Tabs.
- `pages/routes.ts`: every screen as a lazy chunk (`PAGES`), plus `prefetchPage(path)` / `prefetchWhenIdle(paths)`. The menu prefetches on hover/focus/touch, the app prefetches the screen in the address bar at startup (in parallel with the session check) and the POS in idle time for people who sell.
- `components/stats.tsx`: `Tile` (clickable with `onClick`/`action`), `OverviewStat`, `AttentionList` (rows link with `to`), `ReportTables`, shared by the dashboard, branch report and Inventory. `components/TurnoverMeter.tsx`: shared by the dashboard and Tax & IRD.
- `pages/Pos.tsx`: cart (kept in IndexedDB as `cartdraft:<branch>` and restored after a reload), invoice-type logic (mirrors the server), payment modals, offline sale, returns, held carts, shifts, register tools.
- `pages/Inventory.tsx`: filter in the URL (`?filter=low|out|negative`); *Order these* hands the items to `pages/Purchasing.tsx` through router state, which opens a pre-filled PO.
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
- Loading: a page never imports another page (that drags the whole other screen into the download); shared pieces go in `components/`. Occasional, heavy parts (FonePay QR, device settings, camera, label printing) are `lazy()` / `import()` where they're opened. While code or data loads, show `PageSkeleton` / `SkeletonRows` (static grey blocks, no shimmer), never a bare "Loading…". React and the router are a separate `vendor` chunk (`vite.config.ts`) so they stay cached across deploys.
- Inputs: quantities with `QtyInput`, rupees with `MoneyInput` + `moneyProblem()` in `<Field error=…>`; never `<input type="number">`. A disabled button gets `why=`. Every submit handler starts with `setError(null)`.
- A number on screen that someone has to act on links to the list behind it (`Tile onClick`, `AttentionList to`).
- Pages a role can open but not change show `<ViewOnly>` instead of silently missing buttons.

## Testing
- `backend/*/tests/`: unit and integration tests using `ims.testutils.World`.
- Postgres-only tests (triggers, concurrency) skip on SQLite. Run them with `scripts/test_with_postgres.py`.
- `frontend/src/lib/pricing.test.ts` runs the shared vectors; `api`, `money` and `sync` have their own tests.
- Lint: `ruff check .` (backend), `npm run lint` (oxlint, frontend). CI runs all of it on every push.
- Before saying "done": backend tests on both databases, `npx tsc -b`, `npm test`, `npm run build`, and click through the changed screen in a browser.
