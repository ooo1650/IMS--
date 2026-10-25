"""payment.md compliance: dates, invoices, append-only, printing, CBMS, tax
engine, purchase VAT, reports and exports."""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from admin_extras.models import AuditLog
from catalog.models import Supplier
from compliance import bs, cbms, documents, exports, reports
from compliance.models import CbmsPost, MockCbmsRecord, TaxCategory, TaxCategoryRate, get_settings
from compliance.words import amount_in_words
from ims.testutils import World
from inventory import services as inv
from inventory.models import InventoryItem
from purchasing import services as purchasing
from purchasing.models import PurchaseOrder, PurchaseOrderLine
from sales import services
from sales.models import AppendOnlyError, PrintLog, Sale

BUYER = {"buyer_name": "Ram Traders", "buyer_address": "Kathmandu"}


class DatesAndWordsTests(SimpleTestCase):
    def test_bs_conversion_and_fiscal_year(self):
        self.assertEqual(bs.bs_str(date(2026, 10, 25)), "2083.07.08")  # Kartik 8 (Kartik 1 = 18 Oct 2026)
        self.assertEqual(bs.fiscal_year_label(date(2026, 7, 16)), "2082-83")  # Asar end
        self.assertEqual(bs.fiscal_year_label(date(2026, 7, 17)), "2083-84")  # Shrawan 1
        self.assertEqual(bs.cbms_fiscal_year("2083-84"), "2083.084")
        first, last = bs.fy_bounds("2083-84")
        self.assertEqual((first, last), (date(2026, 7, 17), date(2027, 7, 16)))

    def test_amount_in_words_uses_lakh_crore(self):
        self.assertEqual(amount_in_words(123456750),
                         "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paisa Only")
        self.assertEqual(amount_in_words(1000000000), "Rupees One Crore Only")
        self.assertEqual(amount_in_words(100), "Rupees One Only")


class ComplianceBase(TestCase):
    def setUp(self):
        self.w = World()
        self.w.org.pan = "999999999"
        self.w.org.save()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.cfg = get_settings()

    def enable_cbms(self, password="test@321"):
        self.cfg.cbms_enabled, self.cfg.cbms_base_url, self.cfg.cbms_username = True, "mock://", "Test_CBMS"
        self.cfg.cbms_password = password
        self.cfg.save()


class InvoiceRulesTests(ComplianceBase):
    def test_small_walk_in_sale_is_abbreviated(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual(sale.invoice_type, Sale.ABBREVIATED)
        self.assertEqual(sale.issued_bs, bs.bs_str(timezone.localdate()))
        self.assertTrue(sale.transaction_bs)

    def test_over_limit_needs_buyer_and_becomes_full(self):
        self.cfg.abbreviated_invoice_limit_paisa = 20000  # Rs 200 (configurable; default Rs 10,000)
        self.cfg.save()
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 2))  # Rs 226
        sale, _ = self.w.sell((self.w.p1, 2), **BUYER)
        self.assertEqual((sale.invoice_type, sale.buyer_name), (Sale.FULL, "Ram Traders"))

    def test_buyer_pan_switches_to_full_and_is_validated(self):
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), buyer_name="X", buyer_pan="12")
        sale, _ = self.w.sell((self.w.p1, 1), buyer_name="X Pvt Ltd", buyer_pan="301234567")
        self.assertEqual(sale.invoice_type, Sale.FULL)

    def test_card_payment_needs_reference_and_is_recorded(self):
        cart = self.w.cart((self.w.p1, 1))
        with self.assertRaises(services.CheckoutError):
            services.checkout_cash(branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a, cart=cart,
                                   tendered_paisa=0, idempotency_key="c1", method="card")
        sale, _ = services.checkout_cash(branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a,
                                         cart=cart, tendered_paisa=0, idempotency_key="c2", method="card",
                                         payment_ref="APPR123")
        self.assertEqual((sale.payment_method, sale.payments.get().ref), ("card", "APPR123"))


class AppendOnlyTests(ComplianceBase):
    def test_issued_invoice_cannot_be_edited_or_deleted(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        sale.total_paisa = 1
        with self.assertRaises(AppendOnlyError):
            sale.save()
        with self.assertRaises(AppendOnlyError):
            Sale.objects.get(pk=sale.pk).delete()
        line = sale.lines.get()
        line.qty = 5
        with self.assertRaises(AppendOnlyError):
            line.save()

    def test_cancel_requires_reason_and_keeps_invoice_printable(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        with self.assertRaises(ValidationError):
            services.void_sale(sale, self.w.manager_a, "  ")
        services.void_sale(sale, self.w.manager_a, "keyed twice")
        sale.refresh_from_db()
        self.assertTrue(sale.is_cancelled)
        ctx = documents.invoice_context(sale)
        self.assertTrue(ctx["cancelled"])
        self.assertIn("CANCELLED", documents.render_html(ctx))

    def test_credit_note_and_audit_log_are_immutable(self):
        sale, _ = self.w.sell((self.w.p1, 2))
        cn = services.create_return(sale=sale, register=self.w.reg_a, cashier=self.w.cashier_a,
                                    items=[(sale.lines.get().id, 1)], reason="damaged")
        cn.reason = "edited"
        with self.assertRaises(AppendOnlyError):
            cn.save()
        with self.assertRaises(AppendOnlyError):
            AuditLog.objects.first().delete()


class PrintOnceTests(ComplianceBase):
    def test_first_print_is_original_then_numbered_copies(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        kinds = [services.record_print("invoice", sale, self.w.cashier_a) for _ in range(3)]
        self.assertEqual([(k.kind, k.copy_no) for k in kinds], [("original", 0), ("copy", 1), ("copy", 2)])
        ctx = documents.invoice_context(sale, kinds[2])
        self.assertEqual(ctx["stamp"]["label"], "COPY OF ORIGINAL — Copy #2")
        self.assertFalse(ctx["open_drawer"])  # reprints never pop the drawer
        delivery = services.record_print("invoice", sale, self.w.cashier_a, PrintLog.DELIVERY)
        self.assertEqual(documents.invoice_context(sale, delivery)["stamp"]["label"], "DELIVERY COPY")
        self.assertEqual(sale.receipt.print_count, 0)  # cached object; DB is authoritative
        sale.receipt.refresh_from_db()
        self.assertEqual(sale.receipt.print_count, 4)


class CbmsTests(ComplianceBase):
    def test_bill_is_queued_in_the_sale_transaction_and_synced(self):
        self.enable_cbms()
        sale, _ = self.w.sell((self.w.p1, 1), buyer_name="X Pvt Ltd", buyer_pan="301234567")
        post = CbmsPost.objects.get(key=f"bill:{sale.id}")
        self.assertEqual(post.payload["fiscal_year"], bs.cbms_fiscal_year(sale.fiscal_year))
        self.assertEqual(post.payload["invoice_date"], sale.issued_bs)
        self.assertEqual(post.payload["vat"], 13.0)
        cbms.sync_pending()
        post.refresh_from_db()
        self.assertEqual((post.status, post.last_code, post.isrealtime), ("synced", "200", True))
        rec = MockCbmsRecord.objects.get(endpoint="bill")
        self.assertEqual(rec.payload["seller_pan"], "999999999")
        self.assertNotIn("password", rec.payload)

    def test_duplicate_is_treated_as_synced_and_late_posts_are_not_realtime(self):
        self.enable_cbms()
        sale, _ = self.w.sell((self.w.p1, 1))
        post = CbmsPost.objects.get()
        MockCbmsRecord.objects.create(endpoint="bill", document_number=sale.receipt_number, payload={})
        cbms.sync_pending(now=timezone.now() + timedelta(hours=3))
        post.refresh_from_db()
        self.assertEqual((post.status, post.last_code, post.isrealtime), ("synced", "101", False))

    def test_return_waits_for_its_bill(self):
        self.enable_cbms()
        sale, _ = self.w.sell((self.w.p1, 2))
        cn = services.create_return(sale=sale, register=self.w.reg_a, cashier=self.w.cashier_a,
                                    items=[(sale.lines.get().id, 1)], reason="damaged")
        ret = CbmsPost.objects.get(key=f"billreturn:cn:{cn.id}")
        cbms.post_one(ret)  # bill not posted yet -> 105, retry later
        self.assertEqual((ret.status, ret.last_code), ("pending", "105"))
        cbms.sync_pending(force=True)  # bills before returns
        ret.refresh_from_db()
        self.assertEqual(ret.status, "synced")
        self.assertEqual(ret.payload["ref_invoice_number"], sale.receipt_number)
        self.assertEqual(ret.payload["credit_note_date"], cn.issued_bs)

    def test_bad_credentials_pause_posting_until_updated(self):
        self.enable_cbms(password="wrong")
        self.w.sell((self.w.p1, 1))
        cbms.sync_pending()
        self.cfg.refresh_from_db()
        self.assertIn("100", self.cfg.cbms_paused_reason)
        self.assertEqual(cbms.sync_pending(), [])  # paused
        self.assertEqual(CbmsPost.objects.get().status, "pending")

    def test_disabled_cbms_queues_nothing(self):
        self.w.sell((self.w.p1, 1))
        self.assertFalse(CbmsPost.objects.exists())


class TaxEngineTests(ComplianceBase):
    def test_effective_dated_rate_applies_from_its_date(self):
        cat = TaxCategory.objects.get(code="VAT13")
        self.w.p1.tax_category = cat
        self.w.p1.save()
        TaxCategoryRate.objects.create(category=cat, effective_from=timezone.localdate() + timedelta(days=1),
                                       vat_rate=Decimal("15"))
        sale, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual(sale.lines.get().tax_rate, Decimal("13"))
        TaxCategoryRate.objects.filter(vat_rate=15).update(effective_from=timezone.localdate())
        cat = TaxCategory.objects.get(pk=cat.pk)  # drop the rate cache
        self.w.p1.tax_category = cat
        self.w.p1.save()
        sale2, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual((sale2.lines.get().tax_rate, sale2.tax_total_paisa), (Decimal("15"), 1500))

    def test_zero_rated_and_exempt_are_reported_separately(self):
        self.w.p3.tax_category = TaxCategory.objects.get(code="ZERO")
        self.w.p3.save()
        sale, _ = self.w.sell((self.w.p3, 1), (self.w.p1, 1))
        self.assertEqual((sale.zero_rated_sales_paisa, sale.tax_exempted_sales_paisa), (2000, 0))
        self.assertEqual(sale.lines.get(product=self.w.p3).tax_kind, "zero")

    def test_digital_vat_discount_off_by_default_then_applies_to_digital_only(self):
        cart = lambda: self.w.cart((self.w.p1, 1))  # noqa: E731
        card = lambda k: services.checkout_cash(  # noqa: E731
            branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a, cart=cart(), tendered_paisa=0,
            idempotency_key=k, method="card", payment_ref="R")[0]
        self.assertEqual(card("d0").digital_vat_discount_paisa, 0)
        self.cfg.digital_vat_discount_enabled = True
        self.cfg.save()
        sale = card("d1")
        self.assertEqual((sale.digital_vat_discount_paisa, sale.total_paisa), (130, 11170))
        cash, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual(cash.digital_vat_discount_paisa, 0)


class PurchaseTests(ComplianceBase):
    def setUp(self):
        super().setUp()
        self.supplier = Supplier.objects.create(name="S", pan="301234567")
        self.w.p2.input_credit_blocked_pct = 100  # e.g. beverages
        self.w.p2.save()
        self.po = PurchaseOrder.objects.create(branch=self.w.a, supplier=self.supplier, created_by=self.w.manager_a)
        self.l1 = PurchaseOrderLine.objects.create(po=self.po, product=self.w.p1, qty_ordered=10, unit_cost_paisa=7000)
        self.l2 = PurchaseOrderLine.objects.create(po=self.po, product=self.w.p2, qty_ordered=10, unit_cost_paisa=4000)
        purchasing.mark_ordered(self.po, self.w.manager_a)

    def test_purchase_vat_and_blocked_input_credit(self):
        gr = purchasing.receive_goods(self.po, self.w.manager_a, [(self.l1.id, 10, None), (self.l2.id, 10, None)],
                                      "INV-77", "2026-10-20")
        self.assertEqual((gr.taxable_paisa, gr.vat_paisa), (110000, 14300))
        self.assertEqual(gr.claimable_vat_paisa, 9100)  # p2's 5,200 VAT is blocked
        self.assertEqual(gr.supplier_invoice_bs, "2083.07.03")
        book = reports.purchase_book(reports.Period(), [self.w.a])
        self.assertEqual((book["totals"]["vat"], book["totals"]["claimable"]), (14300, 9100))

    def test_purchase_return_reduces_stock_and_reverses_vat(self):
        purchasing.receive_goods(self.po, self.w.manager_a, [(self.l1.id, 10, None)], "INV-1")
        pr = purchasing.create_purchase_return(branch=self.w.a, supplier=self.supplier, by=self.w.manager_a,
                                               lines=[(self.w.p1.id, 2, 7000)], reason="expired")
        self.assertTrue(pr.number.startswith("PR-A-"))
        self.assertEqual((pr.vat_paisa, self.w.on_hand(self.w.a, self.w.p1)), (1820, 18))
        vat = reports.vat_return(reports.Period(), [self.w.a])["summary"]
        self.assertEqual(vat["input_vat"], 9100 - 1820)


class NotesAndReportsTests(ComplianceBase):
    def test_debit_note(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        dn = services.create_debit_note(sale=sale, by=self.w.manager_a, taxable_paisa=1000, reason="undercharged")
        self.assertEqual((dn.tax_paisa, dn.total_paisa), (130, 1130))
        self.assertTrue(dn.number.startswith("DN-A-"))
        with self.assertRaises(ValidationError):
            services.void_sale(sale, self.w.manager_a, "too late")  # notes exist: no cancellation

    def test_sales_book_and_vat_return(self):
        s1, _ = self.w.sell((self.w.p1, 2))
        s2, _ = self.w.sell((self.w.p1, 1))
        services.void_sale(s2, self.w.manager_a, "wrong")
        services.create_return(sale=s1, register=self.w.reg_a, cashier=self.w.cashier_a,
                               items=[(s1.lines.get().id, 1)], reason="faulty")
        p = reports.Period(month=reports.fiscal_years()["current_month"])
        book = reports.sales_book(p, [self.w.a])
        self.assertEqual(len(book["rows"]), 2)
        self.assertEqual(book["totals"]["vat"], 2600)  # cancelled bill counted as zero
        vat = reports.vat_return(p, [self.w.a])["summary"]
        self.assertEqual(vat["output_vat"], 2600 - 1300)
        self.assertFalse(vat["nil_return"])
        self.assertEqual(len(reports.cancelled_bills(p, [self.w.a])["rows"]), 1)

    def test_stock_reconciliation_flags_unsupported_stock(self):
        inv.adjust_stock(self.w.a, self.w.p1.id, 5, self.w.clerk_a, "found in back room")
        rec = reports.stock_reconciliation(None, [self.w.a])
        row = next(r for r in rec["rows"] if r["sku"] == "P1")
        self.assertEqual((row["excess"], row["market_value"], row["potential_fine"]), (5, 50000, 25000))

    def test_exports_and_pdf_render(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        report = reports.sales_book(reports.Period(), [self.w.a])
        for fmt, magic in (("xlsx", b"PK"), ("pdf", b"%PDF"), ("xml", b"<?xml"), ("csv", b"\xef\xbb\xbf")):
            self.assertTrue(exports.RENDERERS[fmt](report).startswith(magic), fmt)
        self.assertTrue(documents.render_pdf(documents.invoice_context(sale)).startswith(b"%PDF"))

    def test_audit_records_before_and_after(self):
        self.w.p1.sale_price_paisa = 12345
        self.w.p1.save()
        log = AuditLog.objects.filter(action="product.updated").latest("at")
        self.assertEqual(log.changes["sale_price_paisa"], [10000, 12345])
        self.assertTrue(log.at_bs)

    def test_turnover_alerts(self):
        self.cfg.cbms_threshold_paisa = 100
        self.cfg.save()
        self.w.sell((self.w.p1, 1))
        t = reports.turnover([self.w.a])
        self.assertTrue(any("CBMS line" in a["message"] for a in t["alerts"]))


class ComplianceApiTests(ComplianceBase):
    def test_print_endpoint_and_report_permissions(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        c = self.w.client(self.w.cashier_a, self.w.a)
        r1 = c.post(f"/api/v1/sales/{sale.id}/print/", {}, format="json")
        r2 = c.post(f"/api/v1/sales/{sale.id}/print/", {}, format="json")
        self.assertEqual((r1.data["kind"], r2.data["kind"], r2.data["copy_no"]), ("original", "copy", 1))
        self.assertIn("कर बीजक", r1.data["html"])
        self.assertEqual(c.get("/api/v1/compliance/reports/sales-book").status_code, 403)
        self.assertEqual(c.get("/api/v1/compliance/settings").status_code, 403)
        m = self.w.client(self.w.manager_a, self.w.a)
        self.assertEqual(m.get("/api/v1/compliance/reports/sales-book").status_code, 200)
        self.assertEqual(m.get("/api/v1/compliance/reports/vat-return?export=xlsx").status_code, 200)
        a = self.w.client(self.w.admin)
        r = a.patch("/api/v1/compliance/settings", {"org_pan": "12", "cbms_password": "x"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = a.patch("/api/v1/compliance/settings", {"org_pan": "301234567", "cbms_password": "secret"},
                    format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["cbms_password_set"])
        self.assertNotIn("cbms_password", r.data)
        self.assertEqual(get_settings().cbms_password, "secret")  # stored encrypted, readable by the app
        self.assertNotIn("secret", get_settings().cbms_password_enc)

    def test_org_reports_cover_all_branches_even_with_an_active_branch(self):
        self.enable_cbms()
        self.w.sell((self.w.p1, 1))  # branch A
        self.w.open_shift(self.w.reg_b, self.w.cashier_b)
        self.w.sell((self.w.p1, 1), branch=self.w.b, register=self.w.reg_b, cashier=self.w.cashier_b)
        a = self.w.client(self.w.admin, self.w.b)  # admin working at branch B
        self.assertEqual(len(a.get("/api/v1/compliance/reports/sales-book").data["rows"]), 2)
        self.assertEqual(a.get("/api/v1/compliance/cbms/status/").data["pending"], 2)
        self.assertEqual(len(a.get("/api/v1/compliance/cbms/?status=pending").data["results"]), 2)
        self.assertEqual(len(a.get(f"/api/v1/compliance/reports/sales-book?branch={self.w.a.id}").data["rows"]), 1)
        m = self.w.client(self.w.manager_a, self.w.a)  # branch manager: own branch only
        self.assertEqual(len(m.get("/api/v1/compliance/reports/sales-book").data["rows"]), 1)

    def test_checkout_api_requires_buyer_over_limit(self):
        self.cfg.abbreviated_invoice_limit_paisa = 20000
        self.cfg.save()
        c = self.w.client(self.w.cashier_a, self.w.a)
        body = {"register_id": self.w.reg_a.id, "idempotency_key": str(uuid.uuid4()), "tendered_paisa": 10 ** 7,
                "lines": [{"product_id": self.w.p1.id, "qty": 2}]}
        self.assertEqual(c.post("/api/v1/pos/checkout", body, format="json").status_code, 400)
        r = c.post("/api/v1/pos/checkout", {**body, **BUYER}, format="json")
        self.assertEqual((r.status_code, r.data["invoice_type"]), (201, "full"))
        self.assertTrue(InventoryItem.objects.exists())


import unittest  # noqa: E402

from django.db import connection, transaction  # noqa: E402
from django.db.utils import DatabaseError  # noqa: E402


@unittest.skipUnless(connection.vendor == "postgresql", "DB triggers are PostgreSQL-only")
class DatabaseTriggerTests(ComplianceBase):
    """Append-only holds even for raw SQL (payment.md: 'no UPDATE or DELETE path
    in the app, the API or the database')."""

    def raw(self, sql, params):
        with self.assertRaises(DatabaseError), transaction.atomic(), connection.cursor() as c:
            c.execute(sql, params)

    def test_raw_sql_cannot_change_or_delete_issued_documents(self):
        sale, _ = self.w.sell((self.w.p1, 2))
        cn = services.create_return(sale=sale, register=self.w.reg_a, cashier=self.w.cashier_a,
                                    items=[(sale.lines.get().id, 1)], reason="damaged")
        self.raw("UPDATE sales_sale SET total_paisa = 1 WHERE id = %s", [sale.id])
        self.raw("DELETE FROM sales_sale WHERE id = %s", [sale.id])
        self.raw("UPDATE sales_saleline SET qty = 9 WHERE sale_id = %s", [sale.id])
        self.raw("UPDATE sales_creditnote SET reason = 'x' WHERE id = %s", [cn.id])
        self.raw("DELETE FROM admin_extras_auditlog", [])
        self.raw("UPDATE sales_receipt SET print_count = 0, number = 'X' WHERE sale_id = %s", [sale.id])

    def test_cancellation_columns_may_still_change(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        services.void_sale(sale, self.w.manager_a, "test")  # allowed: status/void columns only
        self.raw("UPDATE sales_sale SET status = 'paid' WHERE id = %s", [sale.id])  # no reinstating
