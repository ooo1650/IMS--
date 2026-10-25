import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ims.testutils import World
from inventory.models import InventoryItem, StockMovement
from sales import fonepay, services
from compliance.models import get_settings
from sales.models import CashierShift, CreditNote, DocumentSequence, Payment, Sale
from sales.numbering import fiscal_year_for, receipt_gaps


BUYER = {"buyer_name": "Ram Traders", "buyer_address": "Kathmandu"}


class CashCheckoutTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a, 100000)

    def test_cash_sale_decrements_stock_and_snapshots_cost(self):
        sale, created = self.w.sell((self.w.p1, 2), (self.w.p2, 1))
        self.assertTrue(created)
        self.assertEqual(sale.status, Sale.PAID)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 8)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p2), 9)
        line = sale.lines.get(product=self.w.p1)
        self.assertEqual(line.unit_cost_paisa, 7000)
        self.assertEqual(StockMovement.objects.filter(ref=str(sale.id), type="sale_out").count(), 2)
        # 2×100.00 excl 13% = 226.00, plus 56.50 incl = 282.50
        self.assertEqual(sale.total_paisa, 28250)
        self.assertEqual(sale.subtotal_paisa - sale.discount_total_paisa + sale.tax_total_paisa
                         + sale.round_adjust_paisa, sale.total_paisa)

    def test_receipt_numbers_are_gapless_per_register(self):
        fy = fiscal_year_for(timezone.localdate())
        s1, _ = self.w.sell((self.w.p1, 1))
        with self.assertRaises(services.StockUnavailable):
            self.w.sell((self.w.p1, 100), **BUYER)  # failed checkout must not burn a number
        s2, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual((s1.receipt_seq, s2.receipt_seq), (1, 2))
        # Per-branch, per-fiscal-year series (payment.md Step 2).
        self.assertEqual(s1.receipt_number, f"A-{fy}-000001")
        self.assertEqual(receipt_gaps(self.w.a, self.w.reg_a, fy), [])

    def test_failed_checkout_rolls_back_everything(self):
        before = self.w.on_hand(self.w.a, self.w.p1)
        with self.assertRaises(services.StockUnavailable):
            self.w.sell((self.w.p1, 1), (self.w.p2, 999), **BUYER)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), before)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertFalse(DocumentSequence.objects.filter(next_number__gt=1).exists())

    def test_idempotent_retry_returns_same_sale(self):
        key = str(uuid.uuid4())
        s1, c1 = self.w.sell((self.w.p1, 1), key=key)
        s2, c2 = self.w.sell((self.w.p1, 1), key=key)
        self.assertEqual(s1.pk, s2.pk)
        self.assertEqual((c1, c2), (True, False))
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 9)

    def test_allow_negative_lets_stock_go_below_zero(self):
        InventoryItem.objects.filter(branch=self.w.a, product=self.w.p3).update(allow_negative=True)
        sale, _ = self.w.sell((self.w.p3, 12))
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p3), -2)
        self.assertFalse(sale.stock_conflict)

    def test_requires_open_shift(self):
        CashierShift.objects.update(status=CashierShift.CLOSED)
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1))

    def test_tendered_must_cover_total(self):
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), tendered=100)

    def test_large_discount_needs_manager(self):
        cart_kw = dict(sale_discount_type="percent", sale_discount_value=2000, sale_discount_reason="damaged")
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), **cart_kw)
        sale, _ = self.w.sell((self.w.p1, 1), approver_username="manager_a", approver_password="test-pass-123",
                              **cart_kw)
        self.assertEqual(sale.discount_approved_by, self.w.manager_a)

    def test_archived_product_cannot_be_sold(self):
        self.w.p1.archived_at = timezone.now()
        self.w.p1.save()
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1))

    def test_void_restocks_and_keeps_number(self):
        sale, _ = self.w.sell((self.w.p1, 3))
        services.void_sale(sale, self.w.manager_a, "wrong item")
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.VOIDED)
        self.assertEqual(sale.receipt_seq, 1)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)

    def test_shift_close_variance(self):
        shift = CashierShift.objects.get()
        sale, _ = self.w.sell((self.w.p1, 1))
        shift = services.close_shift(shift, self.w.cashier_a, 100000 + sale.total_paisa - 500)
        self.assertEqual(shift.expected_cash_paisa, 100000 + sale.total_paisa)
        self.assertEqual(shift.variance_paisa, -500)


class FonepayTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.gw = fonepay.MockFonepayGateway()

    def initiate(self, *lines):
        sale, _ = services.initiate_fonepay(branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a,
                                            cart=self.w.cart(*lines), idempotency_key=str(uuid.uuid4()))
        return sale, sale.payments.get().fonepay_prn

    def test_initiate_does_not_touch_stock(self):
        sale, prn = self.initiate((self.w.p1, 2))
        self.assertEqual(sale.status, Sale.OPEN)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)
        self.assertEqual(sale.receipt_number, "")

    def test_polling_commits_on_success(self):
        sale, prn = self.initiate((self.w.p1, 2))
        self.assertEqual(services.refresh_fonepay_status(sale).status, Sale.OPEN)  # still pending
        self.gw.simulate_payment(prn)
        sale = services.refresh_fonepay_status(sale)
        self.assertEqual(sale.status, Sale.PAID)
        self.assertTrue(sale.receipt_number)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 8)

    def test_repeated_notifications_are_noops(self):
        sale, prn = self.initiate((self.w.p1, 1))
        self.gw.simulate_payment(prn)
        status = self.gw.check_status(prn)
        services.commit_fonepay(sale.id, status)
        services.commit_fonepay(sale.id, status)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 9)
        self.assertEqual(DocumentSequence.objects.get(doc_type="invoice").next_number, 2)

    def test_amount_mismatch_is_payment_orphan(self):
        sale, prn = self.initiate((self.w.p1, 1))
        self.gw.simulate_payment(prn, paid_paisa=100)
        sale = services.refresh_fonepay_status(sale)
        self.assertEqual(sale.status, Sale.PAYMENT_ORPHAN)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)

    def test_paid_but_out_of_stock_records_with_conflict(self):
        sale, prn = self.initiate((self.w.p1, 8))
        self.w.sell((self.w.p1, 5))  # another register sells during the QR wait
        self.gw.simulate_payment(prn)
        sale = services.refresh_fonepay_status(sale)
        self.assertEqual(sale.status, Sale.PAID)
        self.assertTrue(sale.stock_conflict)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), -3)

    def test_failed_payment_voids(self):
        sale, prn = self.initiate((self.w.p1, 1))
        self.gw.simulate_payment(prn, fail=True)
        self.assertEqual(services.refresh_fonepay_status(sale).status, Sale.VOIDED)

    def test_expiry_job_final_polls_then_voids(self):
        paid_sale, paid_prn = self.initiate((self.w.p1, 1))
        dead_sale, _ = self.initiate((self.w.p2, 1))
        Sale.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.gw.simulate_payment(paid_prn)  # paid just before expiry
        services.expire_open_sales()
        paid_sale.refresh_from_db()
        dead_sale.refresh_from_db()
        self.assertEqual(paid_sale.status, Sale.PAID)
        self.assertEqual(dead_sale.status, Sale.VOIDED)
        self.assertEqual(dead_sale.payments.get().status, Payment.EXPIRED)

    def test_callback_requires_valid_signature(self):
        sale, prn = self.initiate((self.w.p1, 1))
        self.gw.simulate_payment(prn)
        c = self.w.client(self.w.cashier_a)
        body = {"prn": prn, "status": "success", "amount": "113.00", "trx_id": "X"}
        self.assertEqual(c.post("/api/v1/pos/fonepay/callback", {**body, "signature": "nope"}, format="json")
                         .status_code, 400)
        sig = fonepay.sign(",".join(body[k] for k in ("prn", "status", "amount", "trx_id")))
        r = c.post("/api/v1/pos/fonepay/callback", {**body, "signature": sig}, format="json")
        self.assertEqual(r.status_code, 200)
        sale.refresh_from_db()
        self.assertEqual(sale.status, Sale.PAID)


class OfflineSyncTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.fy = fiscal_year_for(timezone.localdate())
        # Local offline numbering needs the per-register series.
        cfg = get_settings()
        cfg.invoice_series = "register"
        cfg.save()

    def sync(self, seq, *lines, key=None, prices=None):
        cart = self.w.cart(*lines)
        for cl, (p, _) in zip(cart.lines, lines):
            cl.unit_price_paisa = (prices or {}).get(p.id, p.sale_price_paisa)
            cl.tax_rate, cl.tax_inclusive = p.tax_rate, p.tax_inclusive
        return services.sync_offline_sale(
            branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a, cart=cart, tendered_paisa=10 ** 7,
            idempotency_key=key or str(uuid.uuid4()), receipt_seq=seq, fiscal_year=self.fy,
            created_at=timezone.now(),
        )

    def test_sync_records_with_offline_number_and_advances_series(self):
        sale, _ = self.sync(1, (self.w.p1, 1))
        self.assertTrue(sale.offline)
        self.assertEqual(sale.receipt_seq, 1)
        self.assertEqual(DocumentSequence.objects.get(doc_type="invoice").next_number, 2)
        online, _ = self.w.sell((self.w.p1, 1))
        self.assertEqual(online.receipt_seq, 2)

    def test_insufficient_stock_is_recorded_not_rejected(self):
        sale, _ = self.sync(1, (self.w.p1, 15))
        self.assertTrue(sale.stock_conflict)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), -5)

    def test_sync_is_idempotent(self):
        key = str(uuid.uuid4())
        self.sync(1, (self.w.p1, 1), key=key)
        _, created = self.sync(1, (self.w.p1, 1), key=key)
        self.assertFalse(created)
        self.assertEqual(Sale.objects.count(), 1)

    def test_gap_detection(self):
        self.sync(1, (self.w.p1, 1))
        self.sync(3, (self.w.p1, 1))  # sale #2 never synced (lost device storage)
        self.assertEqual(receipt_gaps(self.w.a, self.w.reg_a, self.fy), [2])

    def test_price_drift_flags_review(self):
        sale, _ = self.sync(1, (self.w.p1, 1), prices={self.w.p1.id: 9000})
        self.assertTrue(sale.needs_review)
        self.assertEqual(sale.lines.get().unit_price_paisa, 9000)

    def test_conflict_resolution_refund_voids_and_restocks(self):
        sale, _ = self.sync(1, (self.w.p1, 15))
        services.resolve_conflict(sale, self.w.manager_a, "refunded")
        sale.refresh_from_db()
        self.assertEqual((sale.status, sale.conflict_resolution), (Sale.VOIDED, "refunded"))
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)


class ReturnTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a, 0)
        self.sale, _ = self.w.sell((self.w.p1, 3))

    def ret(self, qty, **kw):
        line = self.sale.lines.get()
        return services.create_return(sale=self.sale, register=self.w.reg_a, cashier=self.w.cashier_a,
                                      items=[(line.id, qty)], reason="faulty", **kw)

    def test_partial_returns_refund_exact_line_total(self):
        line_total = self.sale.lines.get().line_total_paisa
        cn1 = self.ret(1)
        cn2 = self.ret(2)
        self.assertEqual(cn1.total_paisa + cn2.total_paisa, line_total)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)
        self.assertNotEqual(cn1.number, cn2.number)
        self.assertTrue(cn1.number.startswith("CN-A-"))
        with self.assertRaises(ValidationError):
            self.ret(1)  # nothing left to return

    def test_return_goes_back_at_current_average_cost(self):
        self.ret(1)
        mv = StockMovement.objects.get(type="return_in")
        self.assertEqual(mv.unit_cost_paisa, self.w.item(self.w.a, self.w.p1).weighted_avg_cost_paisa)

    def test_return_reduces_expected_cash(self):
        shift = CashierShift.objects.get()
        cn = self.ret(1)
        self.assertEqual(services.expected_cash(shift), self.sale.total_paisa - cn.total_paisa)

    def test_over_threshold_needs_manager(self):
        with self.settings(IMS={**settings.IMS, "RETURN_APPROVAL_THRESHOLD_PAISA": 100}):
            with self.assertRaises(ValidationError):
                self.ret(1)
            cn = self.ret(1, approver_username="manager_a", approver_password="test-pass-123")
            self.assertEqual(cn.approved_by, self.w.manager_a)
        self.assertEqual(CreditNote.objects.count(), 1)
