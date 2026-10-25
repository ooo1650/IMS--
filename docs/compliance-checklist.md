# payment.md → implementation status

✅ built and tested · 🟡 built, needs your input or external confirmation · 👤 a human/business task (the software only helps)

## Step 0–1 — IRD questions, product position
| Item | Status |
|---|---|
| Ask IRD the six questions | 👤 Tracked in `docs/ird-questions.md`, along with the assumptions made while building |
| Product position: inventory, reconciliation, purchases, accounting links | ✅ These are already the core of IMS; billing is one module |
| Choose a target segment | 👤 IMS fits retail/marts out of the box |
| Cloud hosting with a server in Nepal | 👤 `docker-compose.yml` runs on any VM; host it in Nepal |

## Step 2 — Must-haves for listing
| Item | Status | Where |
|---|---|---|
| Bills and notes append-only, with no UPDATE/DELETE in app, API or DB | ✅ | Model guard `compliance/appendonly.py`; **PostgreSQL triggers** in `compliance/migrations/0003…` (tested by raw SQL on Postgres) |
| SQL-queryable DBMS | ✅ | PostgreSQL |
| Audit log of every write with user, time and before/after; viewable and printable | ✅ | `admin_extras/audit.py` (signals + middleware), Tax & IRD → Reports → Audit trail (PDF/Excel) |
| Print once; reprints stamped "Copy of Original" with a count | ✅ | `services.record_print`, `PrintLog`, POST `/sales/{id}/print/` |
| Cancelling needs a reason; cancelled bills stay visible and printable | ✅ | `void_sale`, CANCELLED banner, cancelled-bills report |
| Corrections only via credit/debit notes linked to the invoice | ✅ | `create_return`, `create_debit_note` |
| BS and AD dates on every record | ✅ | `issued_bs`/`transaction_bs` on sales, `issued_bs` on notes, `received_bs`/`supplier_invoice_bs` on purchases, `at_bs` on audit rows |
| Fiscal year Shrawan 1 – Asar end | ✅ | `compliance/bs.py` |
| Per-branch, per-fiscal-year chronological series | ✅🟡 | `sales/numbering.py`; offline handling needs confirmation (ird-questions D) |
| Full tax invoice (Rule 17), all listed fields | ✅ | `compliance/templates/compliance/document.html`, `documents.render_pdf` |
| Abbreviated invoice (Rule 18): blocked over Rs 10,000; full when the buyer gives a PAN | ✅ | `services.decide_invoice_type` |
| Credit/debit note: number, original invoice no. and date, reason, amount, tax | ✅ | |
| Foreign-currency sales at the NRB rate | ❌ | The POS sells in NPR only. Add this if you export or accept foreign currency |
| Delivery-copy print | ✅ | `kind=delivery`, offered over the limit |
| Export to Excel, XML, PDF | ✅ | `compliance/exports.py`, every report has export buttons |
| Backup and restore, 6-year retention | ✅🟡 | `deploy/backup.sh`, `deploy/restore-drill.sh`; schedule them and test a restore |
| Materialized view of CBMS fields for auditors | ✅ | `cbms_bill_view`, `cbms_return_view` + `refresh_auditor_views` |
| Tenant separation | ✅ | Branch scoping (one org per deployment; a separate DB per customer) |
| Three-way hosting agreement template | ✅👤 | `docs/hosting-agreement-template.md` (have a lawyer review it) |
| User manual / in-app help | ✅ | `docs/user-manual.md`, Help page in the app |

## Step 3 — Tax engine
| Item | Status |
|---|---|
| Tax categories with effective-from dates (13 / 5 / 0 / exempt / new via config) | ✅ `TaxCategory` + `TaxCategoryRate` |
| Zero-rated vs exempt kept separate | ✅ Separate kinds, line `tax_kind`, separate buckets |
| Mixed-rate carts | ✅ |
| Inclusive and exclusive prices, fraction r/(100+r) | ✅ |
| Excise, HST, ESF fields | ✅ Product excise rate; HST/ESF as category levies; all in CBMS fields |
| Blocked input credit flag (petrol, alcohol, entertainment, 60% vehicles) | ✅ `Product.input_credit_blocked_pct`; purchase book shows claimable VAT |
| Payment method on every bill (cash, QR, card, wallet, bank) | ✅ |
| Digital-payment VAT discount, OFF by default | ✅🟡 Configurable, awaiting IRD procedure |

## Step 4 — CBMS
| Item | Status |
|---|---|
| Async poster with retry queue (bill, billreturn) | ✅ Outbox `CbmsPost` + `manage.py cbms_sync` (cron, every minute) |
| `isrealtime=false` for late posts | ✅ Configurable window (10 min) |
| Response codes 200/101/105/100–104 | ✅ Tested |
| BS `YYYY.MM.DD`, fiscal year `2083.084` | ✅ |
| Per-customer credential store and update screen; password change pauses sync | ✅ Encrypted at rest; code 100 pauses until updated |
| Sync dashboard and failed-sync alerts | ✅ Tax & IRD → CBMS |
| Turnover meter (Rs 10 / 20 crore) | ✅ |
| Test against the CBMS test server (Test_CBMS / 999999999) | 🟡 Built-in `mock://` double tested. Set the IRD test URL and run once to confirm the live format |

## Step 5 — Reports
| Item | Status |
|---|---|
| Sales book (Schedule-9), purchase book (Schedule-8) | ✅ |
| Sales and purchase return registers (monthly) | ✅ |
| Monthly VAT return working with due date (+25 days) and nil flag | ✅ |
| Annex 5 / 13 | ❌ Formats pending IRD (Step 0 #6) |
| Audit trail, cancelled bills, CBMS sync reports | ✅ |
| Stock vs purchase-book reconciliation (50% fine on excess) | ✅ |
| Accountant export | ✅ Zip of Excel and XML books |
| VAT registration threshold alert (Rs 50 lakh goods / 30 lakh services) | ✅ Shown when the org is not VAT-registered |

## Step 6–8
| Item | Status |
|---|---|
| Freeze a release; listed vs unlisted builds | 👤 Tag releases in git; `docs/onboarding-checklist.md` |
| Test evidence (bills, credit note, cancelled bill, reprint, audit trail, CBMS sync) | ✅ `manage.py generate_listing_evidence --out DIR` |
| Customer acceptance letter | ✅👤 `docs/acceptance-letter-template.md` |
| Electronic Billing User Permission application | 👤 Checklist in `docs/onboarding-checklist.md` |
| Rates, thresholds and templates in config | ✅ Tax & IRD → Settings and Tax categories |
| Watch the budget (Jestha 15) and IRD notices monthly | 👤 |
