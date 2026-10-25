# Customer onboarding (payment.md Step 7) and release listing (Step 6)

## Before listing (once per release)
1. Freeze the version: `git tag vX.Y.Z`. The IRD listing covers **this exact version**. Build listed releases only from tags, and keep unlisted builds clearly separate (e.g. a `-dev` suffix shown in the app footer).
2. On a **test** database (`DJANGO_DEBUG=true`):
   ```bash
   python manage.py migrate && python manage.py seed_demo
   python manage.py generate_listing_evidence --out evidence/ --create-test-bills
   ```
   This writes 20 sample bills (Excel), the first print, a reprint, a credit note and a cancelled bill (PDF), the sales report, the audit trail and the CBMS sync report.
3. Run the CBMS test sync against IRD's **test** server: in Tax & IRD → Settings, set the test base URL with username `Test_CBMS` and seller PAN `999999999`, then use "Sync now". Export the CBMS sync report.
4. Apply to IRD with the evidence and the answers recorded in `docs/ird-questions.md`.
5. After a new version is listed, repeat from step 1. **Every new version may need re-listing** (confirm, Step 0 #3).

## For each customer
- [ ] Sign the acceptance letter (`docs/acceptance-letter-template.md`) and inform IRD that the customer uses the software.
- [ ] If hosted: sign the three-way hosting agreement (`docs/hosting-agreement-template.md`). The server must be in Nepal.
- [ ] Enter the seller details: Tax & IRD → Settings (legal name, Nepali name, PAN, address).
- [ ] Set up tax categories and assign products. Check the VAT5 scope and the exempt items (Schedule 1).
- [ ] Add every branch to the system. **All branches must use the same system** and be listed in the application.
- [ ] Run test bills on their own setup. Use a separate test database, never production (append-only).
- [ ] Taxpayer Portal → **Electronic Billing User Permission**. Attach:
  - PAN and business registration certificates
  - 20 sample bills in Excel → `01-sample-bills.xlsx`
  - Print samples: first copy, reprint, credit note, cancelled bill → `02`–`05` PDFs
  - Sales report and audit-trail report → `06`, `07`
  - CBMS test report, if they must connect → `08-cbms-test-sync.xlsx`
  - Device, server and backup details (below)
- [ ] When the permission is granted: enter the customer's **live** CBMS credentials, turn CBMS on, and check that the first bills show *synced*.

## Device / server / backup details (fill in per customer)
| Item | Value |
|---|---|
| Server location (must be in Nepal) | |
| Hosting operator | |
| Database | PostgreSQL 16, WAL archiving on |
| Backups | Nightly `pg_dump` (`deploy/backup.sh`) + continuous WAL; retained ≥ 6 years |
| Restore test | Weekly `deploy/restore-drill.sh`, last run: |
| POS devices (per branch) | |
| Printers | |
