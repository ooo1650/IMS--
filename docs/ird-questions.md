# Questions to settle with IRD and the tax practitioner

payment.md Step 0 says to ask IRD these questions before finalising the tax engine and CBMS module. The software is built so that every answer is a **settings change** (Tax & IRD → Settings / Tax categories), not a code change. Record each answer here with the date and who gave it.

## From payment.md Step 0

| # | Question | Current behaviour in IMS | Answer (date, source) |
|---|---|---|---|
| 1 | **5% rate in CBMS.** The API has one `taxable_sales_vat` / `vat` pair. How is a 5% line reported? Is there a newer API version? | Taxable value and VAT for every rate are summed into the single pair. The per-rate split is kept on the invoice lines and in the sales book. | |
| 2 | **Digital-payment 10% VAT discount.** Is the procedure published? Which sellers does it cover? How should it appear on the bill, in the return and in CBMS? | **OFF.** When switched on, it's a configurable share of VAT (default 10%), shown as a separate "Digital payment VAT discount" line and reducing the amount payable. VAT is reported to CBMS unreduced. | |
| 3 | **Vendor listing.** What is the process, and what are the fees, documents and test cases? Is source code required? Does every version need re-listing? | `manage.py generate_listing_evidence` produces the evidence pack. | |
| 4 | **Rs 10 crore CBMS line.** When will the Department notice be issued? | The turnover meter warns at 80% and 100% of Rs 10 crore and Rs 20 crore. Both thresholds are configurable. | |
| 5 | **IRD's own billing system.** What is its scope and launch date? Will it have an API? | — | |
| 6 | **Annex 5 / Annex 13** formats for FY 2083/84 | Not built. The VAT return working is there, and the annexes can be added once the formats are confirmed. | |

## Assumptions made while building (please confirm)

| # | Assumption | Where to change it |
|---|---|---|
| A | CBMS `total_sales` = the invoice total **including** VAT (what the customer paid). | `compliance/cbms.py::bill_payload` |
| B | A cancelled invoice that CBMS already received is reported to `/api/billreturn` as a full return with `credit_note_number = CANCEL-<invoice>` (CBMS has no cancel endpoint). | `compliance/cbms.py::cancellation_payload` |
| C | Invoice numbering: **one series per branch per fiscal year** (payment.md Step 2). Per-register series remain available as a setting. | Settings → Invoice series |
| D | In per-branch mode, an **offline** till cannot issue a tax-invoice number. It prints a *provisional slip*, and the tax invoice is issued when the till syncs: issue date = the sync date, transaction date = the actual sale time (Rule 17 has both fields). Is this acceptable? If not, switch to per-register series (offline numbering then works, but a branch has several series). | Settings → Invoice series |
| E | The Rs 10,000 abbreviated-invoice limit is compared with the invoice total **including** VAT. Above it, the buyer's name and address are required and a full invoice is issued. A buyer PAN is optional unless "Require buyer PAN over limit" is on. | Settings |
| F | Zero-rated sales are reported to CBMS as `export_sales`. Exempt sales go in `tax_exempted_sales`. HST/ESF services go in their own fields. | Tax categories |
| G | VAT 5% (reduced) is effective from **Shrawan 1, 2083 (17 July 2026)**. The scope (which goods) still has to be set product by product. | Tax categories → VAT5 |
| H | The fiscal year label is printed as `2083/84`; CBMS receives `2083.084`; document numbers use `2083-84`. | — |
| I | BS dates come from the `nepali-datetime` calendar tables (25 Oct 2026 = Kartik 8, 2083, checked against published patros). payment.md's "Kartik 9" is one day off. | Update the package yearly |

## Unverified items from payment.md

- **Industries may not sell to non-VAT-registered retailers** (excise rule, single source). Not built yet. If it's confirmed, add a block or flag on sales where `buyer_pan` is empty for excisable goods.
- **Source-code submission** for listing (single source).
- **Rs 10 crore CBMS line**: announced, but there's no Department notice yet.
