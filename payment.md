# Nepal IMS/POS: Build Spec (FY 2083/84)

This is a single build spec for a Nepal-compliant IMS/POS. It has no phases or timeline. Give it to your AI coding tool module by module, or all at once.

As of 25 Oct 2026 (Kartik 9, 2083). This is a research summary, not legal or tax advice.

---

## Hard rules the code must never break

Paste this section into every AI prompt. IRD reviews the software before listing it, and a single violation can block the listing. It can also expose your customers to a Rs 5 lakh fine.

1. Saved bills, credit notes and debit notes are append-only. The app, API and database have no UPDATE or DELETE path for them. Enforce this with DB permissions or triggers, not only in app code.
2. Every write is audit-logged: user, timestamp, and before/after values.
3. A bill prints once. Every reprint is stamped "Copy of Original" with a print count.
4. Cancelling a bill requires a reason. The cancelled bill stays visible and printable.
5. Corrections are made only through credit/debit notes linked to the original invoice.
6. Every record stores both BS (Nepali) and AD dates. The fiscal year runs from Shrawan 1 to the end of Asar.
7. Tax rates, thresholds and templates live in config, never hard-coded. VAT can change every budget.
8. Secrets stay server-side only, never in the frontend or mobile app. This covers Fonepay Secret Keys, CBMS passwords and payment credentials.
9. Customer billing data is hosted on servers in Nepal.

---

## Module 1: Data model and integrity

- Use a SQL DBMS, such as PostgreSQL.
- Tables:
  - branches
  - users and roles
  - items (with tax category and effective dates)
  - customers (PAN optional)
  - bills
  - bill_lines
  - credit_notes
  - debit_notes
  - payments
  - audit_log
  - print_log
  - cbms_sync
  - fonepay_qr_requests
- Invoice series: one per branch, per fiscal year, in chronological order.
- Export to Excel, XML and PDF. Support backup and restore. Keep 6 years of data and logs.
- Provide a materialized view of the CBMS fields for IRD auditors.
- Keep each tenant's data separate.

## Module 2: Tax engine

- Tax categories:
  - 13% standard
  - 5% reduced
  - 0% zero-rated
  - exempt
  - new rates added through config
- Keep zero-rated and exempt separate. Only zero-rated sales keep input credit.
- Allow mixed-rate carts on one bill.
- Support tax-inclusive and tax-exclusive pricing. The tax fraction is rate ÷ (100 + rate), so 13/113 at 13%.
- Add fields for excise, Health Service Tax (HST) and Education Service Fee (ESF).
- Flag purchase items with blocked input credit: petrol, alcohol and beverages, entertainment, and 60% of vehicle cost.
- Record the payment method on every bill: cash, Fonepay QR, card, wallet or bank.
- Add a digital-payment VAT discount rule (10% of the VAT), OFF by default. IRD has not published the merchant procedure for it.

## Module 3: Invoices and notes

**Full tax invoice (Rule 17)** must show:
- "कर बीजक" (Tax Invoice) on the face
- invoice number
- transaction date and issue date, as separate fields
- seller and buyer name, address and PAN
- payment method
- for each line: S.No., HS code, description, quantity, rate and amount
- discount, taxable value, VAT %, VAT amount, total, amount in words and signature

**Abbreviated invoice (Rule 18):**
- allowed for bills up to Rs 10,000 only
- switch to a full invoice automatically when the buyer enters a PAN
- the buyer cannot claim input credit on it

**Credit/debit notes** must show:
- a sequential number
- the original invoice number and date
- the reason
- the amount and the tax amount

**Other invoice rules:**
- For foreign-currency sales, show the NPR equivalent at the NRB rate on the transaction date.
- Provide a delivery-copy print option. Goods over Rs 10,000 must travel with the invoice.

## Module 4: CBMS sync (IRD)

- Endpoints:
  - bills: `POST https://cbapi.ird.gov.np/api/bill`
  - credit notes: `POST https://cbapi.ird.gov.np/api/billreturn`
- Payload fields:
  - username, password
  - seller_pan, buyer_pan, buyer_name
  - fiscal_year (`2083.084`)
  - invoice_number, invoice_date (BS `YYYY.MM.DD`)
  - total_sales
  - taxable_sales_vat, vat
  - excisable_amount, excise
  - taxable_sales_hst, hst
  - amount_for_esf, esf
  - export_sales, tax_exempted_sales
  - isrealtime
  - datetimeClient
- Return (credit note) payloads also need: ref_invoice_number, credit_note_number, credit_note_date and reason_for_return.
- Post asynchronously with a retry queue. Set `isrealtime = false` for late posts.
- Response codes:
  - 200 = success
  - 101 = bill already exists; treat it as synced
  - 105 = original bill not found
  - 100, 102, 103, 104 = show the error and retry
- Provide a per-customer credential store with an update screen. A portal password change breaks sync until the new password is entered.
- Show a sync-status dashboard (pending / synced / failed) and send alerts.
- Add a turnover meter that warns at Rs 10 crore and Rs 20 crore. CBMS is mandatory above Rs 20 crore, and Rs 10 crore has been announced.
- Limitation: the API has only one VAT bucket. Ask IRD how 5% lines should be reported.

## Module 5: Fonepay dynamic QR

**What the shop needs:**
- a Fonepay merchant account through its bank
- API access requested from the bank specifically "for POS / billing software integration"
- from the bank: Merchant Code, Secret Key, Username and Password

**Base URLs:**
- UAT: `https://uat-new-merchant-api.fonepay.com/api`
- Live: `https://merchantapi.fonepay.com/api`

**Endpoints:**
- Create QR: `POST /merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrDownload`
  - fields: amount, remarks1, remarks2, prn (unique, max 25 characters), merchantCode, dataValidation, username, password
- Check status: `POST /merchant/merchantDetailsForThirdParty/thirdPartyDynamicQrGetStatus`
  - fields: prn, merchantCode, dataValidation, username, password

**HMAC signing:**
- Algorithm: HMAC-SHA512, keyed with the Secret Key, output as hex. The spec's sample uses uppercase hex; test this in UAT.
- Create QR: sign `AMOUNT,PRN,MERCHANT-CODE,REMARKS1,REMARKS2`.
- Check status: sign `PRN,MERCHANT-CODE`.
- Separate the values with commas, with no URL encoding.

```js
const crypto = require('crypto');
const sign = (key, parts) =>
  crypto.createHmac('sha512', key).update(parts.join(','), 'utf8').digest('hex').toUpperCase();
```

**Flow:**
1. The backend creates the QR request.
2. Render the returned `qrMessage` as a QR image on the screen or the bill.
3. Open the `thirdpartyQrWebSocketUrl` websocket. It sends "QRVerified" when the customer scans, then "paymentSuccess" when they pay. `transactionStatus` is a JSON string inside JSON, so parse it twice.
4. Always confirm the result with the status API.
5. On success, mark the bill as paid and store the `fonepayTraceId`.

**Build items:**
- Store credentials per merchant and per outlet, encrypted.
- Add a polling fallback and a scheduled job that reconciles pending prns.
- One bill can have many prns, but only one can be marked paid. Raise an alert on double payment.
- Confirm payment first, then finalise the VAT bill and sync it to CBMS.
- Build a daily settlement report: POS QR totals vs the bank statement.
- The spec has no refund API. Handle refunds as a credit note plus a bank or manual refund.
- Add a manual "static QR, mark as paid" fallback with a reference field.

**Caveats:**
- The v1.1 spec (July 2024) is a third-party copy from GitHub, not an official Fonepay document. Get the current version from the bank.
- In the spec, the "pending" status example shows "failed".
- API pricing is not published. One vendor says POS use has no fees, while also mentioning a Rs 25,000 + VAT integration fee. Confirm with the bank.

## Module 6: Reports

- Sales book (Schedule-9) and purchase book (Schedule-8)
- Sales return and purchase return registers
- Monthly VAT return working, due within 25 days of the BS month end. Nil returns are also required.
- Annex 5 and Annex 13 (confirm the current formats)
- Audit trail, cancelled bills, CBMS sync status and Fonepay settlement reports
- Stock vs purchase-book reconciliation. Excess stock is fined at 50% of its market value.
- An accountant export
- Alerts when turnover reaches the VAT registration threshold: Rs 50 lakh for goods, Rs 30 lakh for services or mixed

## Module 7: Manual and help

- A user manual in Nepali or English, also available as in-app help.

---

## Non-code tasks (do these alongside coding)

**Ask IRD:**
1. How should 5% lines be reported to CBMS? Is there a newer API?
2. What is the merchant procedure for the digital-payment VAT discount?
3. What is the vendor listing process: fees, source-code requirement, test cases, re-listing per version?
4. When will a Department notice set the Rs 10 crore CBMS line?
5. What are the scope and timing of IRD's own billing system?
6. What are the current Annex 5 and Annex 13 formats?

**Get the software listed:**
- Freeze one exact version for the listing.
- Prepare test evidence: test bills, a credit note, a cancelled bill, a reprint, the audit trail and a CBMS test sync.

**For each customer:**
- Give them an acceptance letter, and tell IRD they use your software.
- Help them apply on the Taxpayer Portal under Electronic Billing User Permission. They need 20 sample bills in Excel, plus print samples and reports.
- Help them get Fonepay API credentials from their bank.

**Ongoing:**
- After every Jestha 15 budget, update the config for rates, Schedule 1 exemptions and thresholds.

---

## Unverified items

- One source says industries may not sell to retailers that are not VAT-registered. If true, add a block or flag in B2B sales.
- Whether IRD listing requires submitting source code.
- The Rs 10 crore CBMS line. It was announced in the budget, but there is no Department notice yet.

---

## Sources

- IRD CBMS API: https://ird.gov.np/public/pdf/976029276.pdf
- IRD CBMS user guide: https://www.ird.gov.np/public/pdf/1589285873.pdf
- Electronic Invoice Procedure 2074: https://ird.gov.np/content/5490/tax-laws-16910453028/
- Finance Act 2083: https://ird.gov.np/content/13566/economic-act--2083--date-of-publication-in/
- CBMS notice 2083/01/04: https://ird.gov.np/content/13488/cbms-notice-01-04/
- LookupTax Nepal VAT: https://lookuptax.com/docs/country/nepal-vat-guidelines-indirect-tax
- Nexus Advisory: https://nexusadvisory.com.np/ird-approved-billing-software-nepal/
- Fonepay Dynamic QR spec (third-party copy): https://github.com/DexA1948/fonepay-dynamic-qr-demo/blob/master/API_Details.md
- PayBridgeNP Fonepay guide: https://paybridgenp.com/blog/fonepay-merchant-registration-guide
- Blanxer Fonepay billing API: https://www.blanxer.com/resources/fonepay-billing-api-integration
- Full research doc: https://claude.ai/artifact/No7eajbCxcsttReQaBLFoQ