"""Collect the evidence IRD asks for (payment.md Steps 6–7):

  * 20 sample bills in Excel
  * print samples: first copy (original), reprint (copy of original), credit
    note, cancelled bill — as PDFs
  * sales report, audit-trail report, CBMS test-sync report

By default it only reads existing documents. ``--create-test-bills`` rings up
a small set of test transactions first; it refuses unless DEBUG is on, because
issued bills are append-only and must never be faked in a live database.
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from compliance import cbms, documents, exports, reports
from compliance.models import get_settings
from sales import services
from sales.models import CreditNote, PrintLog, Sale
from tenancy.models import Branch


class Command(BaseCommand):
    help = "Write IRD listing / e-billing permission evidence to a folder."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True)
        parser.add_argument("--branch", help="branch code (default: first branch)")
        parser.add_argument("--create-test-bills", action="store_true", help="DEBUG only")

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        branch = (Branch.objects.get(code=opts["branch"]) if opts["branch"] else Branch.objects.order_by("id").first())
        if branch is None:
            raise CommandError("No branch")
        if opts["create_test_bills"]:
            if not settings.DEBUG:
                raise CommandError("--create-test-bills only runs with DJANGO_DEBUG=true (never on live data)")
            self._make_test_bills(branch)
        written = []

        sales = Sale.objects.filter(branch=branch).exclude(receipt_number="").order_by("-issued_at")
        period = reports.Period()
        sample = reports.sales_book(period, [branch])
        sample["rows"] = sample["rows"][-20:]
        sample["title"] = "Sample bills (20 most recent)"
        written.append(self._write(out / "01-sample-bills.xlsx", exports.to_xlsx(sample)))

        live = sales.exclude(status=Sale.VOIDED).values("id")
        # Prefer a live invoice that has both its original print and a reprint.
        copy = PrintLog.objects.filter(doc_type="invoice", kind="copy", doc_id__in=live).order_by("-at").first()
        original = (PrintLog.objects.filter(doc_type="invoice", kind="original", doc_id=copy.doc_id).first() if copy
                    else PrintLog.objects.filter(doc_type="invoice", kind="original", doc_id__in=live)
                    .order_by("-at").first())
        if original:
            sale = Sale.objects.get(pk=original.doc_id)
            written.append(self._write(out / "02-invoice-first-print.pdf",
                                       documents.render_pdf(documents.invoice_context(sale, original))))
            if copy:
                written.append(self._write(out / "03-invoice-reprint.pdf",
                                           documents.render_pdf(documents.invoice_context(sale, copy))))
        cn = CreditNote.objects.filter(branch=branch).order_by("-at").first()
        if cn:
            log = PrintLog.objects.filter(doc_type="credit_note", doc_id=cn.id).order_by("at").first()
            written.append(self._write(out / "04-credit-note.pdf",
                                       documents.render_pdf(documents.credit_note_context(cn, log))))
        cancelled = sales.filter(status=Sale.VOIDED).first()
        if cancelled:
            log = PrintLog.objects.filter(doc_type="invoice", doc_id=cancelled.id).order_by("at").first()
            written.append(self._write(out / "05-cancelled-bill.pdf",
                                       documents.render_pdf(documents.invoice_context(cancelled, log))))
        book = reports.sales_book(period, [branch])
        written.append(self._write(out / "06-sales-report.xlsx", exports.to_xlsx(book)))
        written.append(self._write(out / "06-sales-report.pdf", exports.to_pdf(book)))
        audit = reports.audit_trail(period, [branch], org_wide=True)
        written.append(self._write(out / "07-audit-trail.xlsx", exports.to_xlsx(audit)))
        written.append(self._write(out / "07-audit-trail.pdf", exports.to_pdf(audit)))
        sync = reports.cbms_sync_report(period, [branch])
        written.append(self._write(out / "08-cbms-test-sync.xlsx", exports.to_xlsx(sync)))
        missing = [name for name, ok in (("first print", original), ("reprint", copy), ("credit note", cn),
                                         ("cancelled bill", cancelled)) if not ok]
        (out / "README.txt").write_text(
            f"IMS listing evidence — branch {branch.code} — generated {timezone.localtime():%Y-%m-%d %H:%M}\n\n"
            + "\n".join(p.name for p in written)
            + ("\n\nMISSING (issue one and re-run): " + ", ".join(missing) if missing else "")
            + "\n\nAlso attach: PAN & registration certificates, device/server/backup details "
              "(docs/onboarding-checklist.md).\n")
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(written)} files to {out}"))
        if missing:
            self.stdout.write(self.style.WARNING("Missing: " + ", ".join(missing)))

    def _write(self, path, data):
        path.write_bytes(data)
        return path

    def _make_test_bills(self, branch):
        from accounts.models import User
        from catalog.models import Product

        register = branch.registers.filter(is_active=True).first()
        user = User.objects.filter(branch_roles__branch__isnull=True).first() or User.objects.first()
        if services.open_shift_for(register) is None:
            services.open_shift(branch, register, user, 0)
        products = list(Product.objects.active()[:3])
        cart = lambda q: services.Cart(  # noqa: E731
            lines=[services.CartLine(p.id, q) for p in products], buyer_name="IRD Test Buyer",
            buyer_address="Kathmandu", buyer_pan="999999999")
        first, _ = services.checkout_cash(branch=branch, register=register, cashier=user, cart=cart(1),
                                          tendered_paisa=10 ** 8, idempotency_key=f"evidence-{uuid.uuid4()}")
        services.record_print("invoice", first, user)
        services.record_print("invoice", first, user)  # reprint -> Copy of Original
        second, _ = services.checkout_cash(branch=branch, register=register, cashier=user, cart=cart(2),
                                           tendered_paisa=10 ** 8, idempotency_key=f"evidence-{uuid.uuid4()}")
        cn = services.create_return(sale=second, register=register, cashier=user,
                                    items=[(second.lines.first().id, 1)], reason="IRD test credit note")
        services.record_print("credit_note", cn, user)
        third, _ = services.checkout_cash(branch=branch, register=register, cashier=user, cart=cart(1),
                                          tendered_paisa=10 ** 8, idempotency_key=f"evidence-{uuid.uuid4()}")
        services.record_print("invoice", third, user)
        services.void_sale(third, user, "IRD test cancellation")
        if get_settings(branch.org).cbms_enabled:
            cbms.sync_pending(force=True, org=branch.org)
