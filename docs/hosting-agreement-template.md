# Three-way hosting agreement — TEMPLATE

> Template only, not legal advice. Have a Nepali lawyer review it before use and file it with IRD as required.

**Parties**
1. **Software provider:** ________________________ (PAN ________), "the Provider"
2. **Hosting operator:** ________________________ (PAN ________), "the Host"
3. **Taxpayer (customer):** ________________________ (PAN ________), "the Taxpayer"

**1. Purpose.** The Taxpayer uses the Provider's billing and inventory software, version __________ (IRD listing ref. __________), hosted by the Host, to issue tax invoices under the VAT Act 2052, the VAT Rules 2053 and the Electronic Invoice Procedure 2074.

**2. Server location.** All servers, databases and backups holding the Taxpayer's data are located **in Nepal** at: __________________________.

**3. Data ownership and access.** The Taxpayer owns its data. The Inland Revenue Department may access the data, the audit trail and the auditor views at any time on request. The Provider and Host will give IRD officials the access they need.

**4. Integrity.** Issued invoices, credit/debit notes and audit logs cannot be changed or deleted by any party, and this is enforced at the database level. No party may disable these controls.

**5. Retention and backup.** Data and logs are kept for at least **six (6) years**. The Host makes daily backups with continuous transaction-log archiving and tests a restore at least weekly. Backup location: __________________________.

**6. Availability and outage.** Target availability: ______%. During an outage, cash sales continue offline and are synced (and reported to CBMS with `isrealtime=false`) when service is restored.

**7. CBMS credentials.** The Taxpayer's CBMS credentials are stored encrypted and used only to report its own invoices to IRD.

**8. Change control.** The Provider will notify the Taxpayer and IRD of new software versions as required, and will deploy only IRD-listed versions to production.

**9. Termination.** On termination the Host hands the Taxpayer a complete export (database dump plus Excel/XML books) and keeps backups until the 6-year retention period ends, unless the Taxpayer takes custody of them.

**10. Governing law.** The laws of Nepal.

| | Provider | Host | Taxpayer |
|---|---|---|---|
| Name | | | |
| Signature | | | |
| Date (BS) | | | |
