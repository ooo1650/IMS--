# What to Do: Building a Nepal-Compliant IMS/POS (FY 2083/84)

As of 25 Oct 2026 (Kartik 9, 2083). This is a research summary, not legal or tax advice. Have a tax practitioner review it before you apply for IRD listing.

---

## Step 0: Talk to IRD first (this week)

Several design decisions depend on answers IRD has not published. Call or visit IRD's e-billing / CBMS section and ask the questions below. Write the answers down.

1. **5% rate in CBMS.** How do I report a 5% (or other non-13%) line to CBMS? The API has only one `taxable_sales_vat` / `vat` pair. Is there a newer API version?
2. **Digital-payment 10% VAT discount.** Is the procedure published? Which sellers does it cover? How is the discount shown on the bill, and how is it reported in the return and in CBMS?
3. **Vendor listing.** What is the current process to get software listed? What are the fees? Do you need my source code? What are the test cases? Does every new version need re-listing?
4. **Rs 10 crore CBMS line.** When will the Department issue a notice for it?
5. **IRD's own billing system.** What is its scope and launch date? Will it have an API?
6. **Annex 5 / Annex 13.** What formats apply for FY 2083/84?

Do not finalise the tax engine or CBMS module until you have answers to questions 1 and 2.

---

## Step 1: Decide your product position

IRD may release a free billing tool for small B2C sellers. A product that only prints VAT bills could lose its market to that tool.

- [ ] Make **inventory, stock reconciliation, purchases and accounting links** your core value, not bill printing.
- [ ] Choose your first target: retail/marts, restaurants, or distributors/manufacturers. Each has a different CBMS threshold and different features.
- [ ] Decide on hosting. Cloud hosting requires a **server in Nepal**.

---

## Step 2: Build the "must have for listing" core

IRD will not list your software without these.

### Data integrity
- [ ] Bills and notes are **append-only**. There is no UPDATE or DELETE path in the app, the API or the database. Enforce this with DB permissions or triggers.
- [ ] Use a real SQL-queryable DBMS (PostgreSQL, MySQL, etc.).
- [ ] An **audit log** records every write: user, timestamp, and before/after values. Users can view and print the audit-trail report.
- [ ] Each bill prints **once**. Reprints are stamped "Copy of Original" with a print count.
- [ ] Cancelling a bill **requires a reason**. Cancelled bills stay visible and printable.
- [ ] Corrections are made only through credit/debit notes linked to the original invoice number and date.

### Dates and numbering
- [ ] Store **both BS (Nepali) and AD dates** on every record.
- [ ] The fiscal year runs from Shrawan 1 to the end of Asar.
- [ ] Number invoices in a per-branch, per-fiscal-year series, in chronological order.

### Invoice templates
- [ ] **Full tax invoice (Rule 17)** includes:
  - "कर बीजक" (Tax Invoice) on the face
  - invoice number
  - transaction date and issue date, as separate fields
  - seller and buyer name, address and PAN
  - payment method
  - for each line: S.No., HS code, description, quantity, rate and amount
  - discount, taxable value, VAT %, VAT amount, total, amount in words and signature
- [ ] **Abbreviated invoice (Rule 18)**:
  - block it for bills over Rs 10,000
  - switch to a full invoice automatically when the buyer gives a PAN
- [ ] **Credit/debit note** includes:
  - a sequential number
  - the original invoice number and date
  - the reason for the note
  - the amount and the tax amount
- [ ] For foreign-currency sales, show the NPR equivalent at the NRB rate on the transaction date.
- [ ] Provide a delivery-copy print option. Goods over Rs 10,000 must travel with the invoice.

### Export, backup, hosting
- [ ] Export to Excel, XML and PDF.
- [ ] Back up and restore. Keep **6 years** of data and logs.
- [ ] Provide a materialized view of the CBMS fields for IRD auditors.
- [ ] Keep each tenant's data separate.
- [ ] Prepare a three-way hosting agreement template (you, hosting operator, customer) to file with IRD.
- [ ] Write a user manual in Nepali or English and add it as in-app help.

---

## Step 3: Build the tax engine (configurable, never hard-coded)

VAT is no longer single-rate. Rates and exemptions can change every budget and through IRD notices.

- [ ] Give each item a **tax category** with an effective-from date:
  - 13% standard
  - 5% reduced
  - 0% zero-rated
  - exempt
  - new rates added through config
- [ ] Keep **zero-rated and exempt separate**. Only zero-rated sales keep input credit.
- [ ] Allow mixed-rate carts on one bill.
- [ ] Support tax-inclusive and tax-exclusive pricing. The tax fraction is rate ÷ (100 + rate), so 13/113 at 13%.
- [ ] Add fields for excise, Health Service Tax and Education Service Fee.
- [ ] Flag purchase items with blocked input credit: petrol, alcohol and beverages, entertainment, and 60% of vehicle cost.
- [ ] Record the payment method on every bill: cash, QR, card, wallet or bank.
- [ ] Add a **digital-payment VAT discount** rule. Keep it **OFF by default** until IRD publishes the procedure.

---

## Step 4: Build the CBMS module

- [ ] Write an async poster with a retry queue for these endpoints:
  - `POST https://cbapi.ird.gov.np/api/bill`
  - `POST https://cbapi.ird.gov.np/api/billreturn`
- [ ] Set `isrealtime = false` for bills posted late after an outage.
- [ ] Handle the response codes:
  - 200 = success
  - 101 = bill already exists; treat it as synced
  - 105 = original bill not found (returns endpoint)
  - 100, 102, 103, 104 = show the error and retry
- [ ] Format dates as BS `YYYY.MM.DD` and the fiscal year as `2083.084`.
- [ ] Provide a per-customer credential store and an update screen. A portal password change breaks sync until the new password is entered.
- [ ] Show a sync-status dashboard (pending / synced / failed) and send failed-sync alerts.
- [ ] Add a turnover meter that warns customers when they approach **Rs 10 crore** and **Rs 20 crore**.
- [ ] Test against the CBMS test server. The spec's test login is `Test_CBMS`, with seller PAN `999999999`.

---

## Step 5: Build the reports

- [ ] Sales book (Schedule-9) and purchase book (Schedule-8)
- [ ] Sales return and purchase return registers, monthly
- [ ] Monthly VAT return working, due within 25 days of the BS month end. Nil returns are also required.
- [ ] Annex 5 and Annex 13, after you confirm the formats with IRD
- [ ] Audit trail, cancelled bills and CBMS sync reports
- [ ] **Stock vs purchase-book reconciliation.** Excess stock is fined at 50% of its market value.
- [ ] An accountant export, so the VAT return, sales register and books all match
- [ ] An alert when turnover reaches the VAT registration threshold: Rs 50 lakh for goods, Rs 30 lakh for services or mixed

---

## Step 6: Get listed with IRD

1. Freeze a release version. The listing covers that **exact version** only.
2. Prepare test evidence for each of these:
   - test bills
   - a credit note
   - a cancelled bill
   - a reprint
   - an audit trail
   - a CBMS test sync
3. Apply to IRD with the details from Step 0 (fees, documents, any source-code requirement).
4. After you are listed, set up a **re-listing process** for future versions. Keep listed and unlisted builds clearly separated.

---

## Step 7: Onboard each customer

For every business that uses your software:

- [ ] Give the customer an **acceptance letter**, and tell IRD that they are using your software.
- [ ] Help them run test bills on their own setup.
- [ ] Help them apply on the Taxpayer Portal under **Electronic Billing User Permission**. They will need:
  - PAN and business registration certificates
  - 20 sample bills in Excel
  - print samples: a first copy, a reprint, a credit note and a cancelled bill
  - the sales report and the audit-trail report
  - the CBMS test report, if they must connect to CBMS
  - device, server and backup details
- [ ] Branches must use the same system. List them all in the application.

---

## Step 8: Stay current

- [ ] Every **Jestha 15**, read the new budget and Finance Act. VAT rates, Schedule 1 exemptions and thresholds can change.
- [ ] Check IRD notices monthly at ird.gov.np. Thresholds change by notice.
- [ ] Keep rates, thresholds and templates in config, so that a law change means a settings update, not a new release.

---

## Unverified items: check before you build on them

- An excise rule saying **industries may not sell to non-VAT-registered retailers**. Only one source reports it. If it is true, a manufacturer or distributor POS should block or flag those sales.
- Whether listing requires **source-code submission**. Only one booklet reports it.
- The **Rs 10 crore** CBMS line. It was announced in the budget and local offices are applying it, but there is no Department-level notice yet.

---

## Key sources

- IRD CBMS API document: https://ird.gov.np/public/pdf/976029276.pdf
- IRD CBMS user guide: https://www.ird.gov.np/public/pdf/1589285873.pdf
- Electronic Invoice Procedure 2074: https://ird.gov.np/content/5490/tax-laws-16910453028/
- Finance Act 2083: https://ird.gov.np/content/13566/economic-act--2083--date-of-publication-in/
- CBMS notice 2083/01/04 (Rs 20 crore): https://ird.gov.np/content/13488/cbms-notice-01-04/
- LookupTax Nepal VAT guide: https://lookuptax.com/docs/country/nepal-vat-guidelines-indirect-tax
- Nexus Advisory on IRD billing software: https://nexusadvisory.com.np/ird-approved-billing-software-nepal/
- Full research doc: https://claude.ai/artifact/No7eajbCxcsttReQaBLFoQ