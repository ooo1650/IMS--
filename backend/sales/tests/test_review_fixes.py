"""Regression tests for the 2026-11-09 review (B1-B8, S1-S3)."""
import uuid
from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from admin_extras.models import AuditLog
from compliance.models import get_settings
from ims.testutils import World
from sales import fonepay, services
from sales.models import CashierShift, CreditNote, Customer, Sale
from sales.numbering import fiscal_year_for
from tenancy.models import Organization

DISCOUNT = dict(sale_discount_type="percent", sale_discount_value=2000, sale_discount_reason="damaged")


class CustomerScopeTests(TestCase):
    """S1: a sale may only name a customer of its own business (this branch or business-wide)."""

    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        other = Organization.objects.create(name="Other", code="other")
        self.foreign = Customer.objects.create(org=other, name="Secret Customer", phone="9")
        self.branch_b_only = Customer.objects.create(org=self.w.org, branch=self.w.b, name="B only", phone="8")
        self.org_wide = Customer.objects.create(org=self.w.org, name="Everyone", phone="7")

    def test_foreign_customer_refused_at_checkout(self):
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), customer_id=self.foreign.id)
        self.assertEqual(Sale.objects.count(), 0)

    def test_other_branch_customer_refused(self):
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), customer_id=self.branch_b_only.id)

    def test_business_wide_customer_allowed(self):
        sale, _ = self.w.sell((self.w.p1, 1), customer_id=self.org_wide.id)
        self.assertEqual(sale.customer_id, self.org_wide.id)

    def test_api_does_not_leak_foreign_customer_name(self):
        c = self.w.client(self.w.cashier_a, self.w.a)
        r = c.post("/api/v1/pos/checkout", {
            "lines": [{"product_id": self.w.p1.id, "qty": 1}], "register_id": self.w.reg_a.id,
            "idempotency_key": str(uuid.uuid4()), "tendered_paisa": 10 ** 7, "customer_id": self.foreign.id,
        }, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("Secret Customer", str(r.data))

    def test_offline_sync_drops_foreign_customer_and_flags_review(self):
        cfg = get_settings(self.w.org)
        cfg.invoice_series = "register"
        cfg.save()
        cart = self.w.cart((self.w.p1, 1), customer_id=self.foreign.id)
        line = cart.lines[0]
        line.unit_price_paisa, line.tax_rate, line.tax_inclusive = (
            self.w.p1.sale_price_paisa, self.w.p1.tax_rate, self.w.p1.tax_inclusive)
        sale, _ = services.sync_offline_sale(
            branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a, cart=cart, tendered_paisa=10 ** 7,
            idempotency_key=str(uuid.uuid4()), receipt_seq=1, fiscal_year=fiscal_year_for(timezone.localdate()),
            created_at=timezone.now())
        self.assertIsNone(sale.customer_id)
        self.assertTrue(sale.needs_review)


class IdempotencyScopeTests(TestCase):
    """S2: a key used at another branch is refused, never answered with that sale."""

    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.w.open_shift(self.w.reg_b, self.w.cashier_b)

    def test_key_from_other_branch_is_refused(self):
        key = str(uuid.uuid4())
        self.w.sell((self.w.p1, 1), key=key)
        with self.assertRaises(services.CheckoutError):
            self.w.sell((self.w.p1, 1), key=key, branch=self.w.b, register=self.w.reg_b, cashier=self.w.cashier_b)
        self.assertEqual(Sale.objects.count(), 1)

    def test_same_branch_retry_still_returns_sale(self):
        key = str(uuid.uuid4())
        s1, _ = self.w.sell((self.w.p1, 1), key=key)
        s2, created = self.w.sell((self.w.p1, 1), key=key)
        self.assertEqual((s1.pk, created), (s2.pk, False))


class ApprovalLockoutTests(TestCase):
    """S3: wrong manager passwords at the register count toward that manager's lockout."""

    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.c = self.w.client(self.w.cashier_a, self.w.a)

    def checkout(self, password):
        return self.c.post("/api/v1/pos/checkout", {
            "lines": [{"product_id": self.w.p1.id, "qty": 1}], "register_id": self.w.reg_a.id,
            "idempotency_key": str(uuid.uuid4()), "tendered_paisa": 10 ** 7, **DISCOUNT,
            "approver_username": "manager_a", "approver_password": password,
        }, format="json")

    def test_failures_are_logged_and_lock_the_manager(self):
        for _ in range(5):
            self.assertEqual(self.checkout("wrong").status_code, 400)
        self.assertEqual(AuditLog.objects.filter(action="auth.login_failed", data__username="manager_a").count(), 5)
        r = self.checkout("test-pass-123")  # right password, but locked now
        self.assertEqual(r.status_code, 400)
        self.assertIn("Too many", r.data["detail"])
        self.assertEqual(Sale.objects.count(), 0)

    def test_right_password_still_works(self):
        r = self.checkout("test-pass-123")
        self.assertEqual(r.status_code, 201, r.data)


class ReturnAfterCancelTests(TestCase):
    """B1: the paid check is repeated under the row lock."""

    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)

    def test_return_on_stale_paid_object_after_cancel_is_refused(self):
        sale, _ = self.w.sell((self.w.p1, 2))
        stale = Sale.objects.get(pk=sale.pk)  # loaded as PAID, like a request that started first
        services.void_sale(Sale.objects.get(pk=sale.pk), self.w.manager_a, "wrong item")
        with self.assertRaises(ValidationError):
            services.create_return(sale=stale, register=self.w.reg_a, cashier=self.w.cashier_a,
                                   items=[(stale.lines.get().id, 1)], reason="faulty")
        self.assertEqual(CreditNote.objects.count(), 0)
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p1), 10)  # restocked once, by the cancel


class FonepayJobTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.gw = fonepay.MockFonepayGateway()

    def initiate(self, *lines):
        sale, _ = services.initiate_fonepay(branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a,
                                            cart=self.w.cart(*lines), idempotency_key=str(uuid.uuid4()))
        return sale, sale.payments.get().fonepay_prn

    def test_expiry_continues_past_a_failing_sale(self):
        """B4: one FonePay error must not stop expiry of the others."""
        bad, _ = self.initiate((self.w.p1, 1))
        good, _ = self.initiate((self.w.p2, 1))
        Sale.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        real = services.refresh_fonepay_status

        def flaky(sale, by=None):
            if sale.pk == bad.pk:
                raise OSError("FonePay unreachable")
            return real(sale, by)

        with mock.patch.object(services, "refresh_fonepay_status", side_effect=flaky):
            services.expire_open_sales()
        bad.refresh_from_db()
        good.refresh_from_db()
        self.assertEqual(bad.status, Sale.OPEN)  # asked again next minute
        self.assertEqual(good.status, Sale.VOIDED)

    def test_paid_qr_issues_full_invoice_when_rules_changed(self):
        """B5: invoice rules tightened while the QR was open; the customer paid anyway."""
        sale, prn = self.initiate((self.w.p1, 1))
        cfg = get_settings(self.w.org)
        cfg.abbreviated_invoice_limit_paisa = 1
        cfg.save()
        self.gw.simulate_payment(prn)
        sale = services.refresh_fonepay_status(sale)
        self.assertEqual(sale.status, Sale.PAID)
        self.assertEqual(sale.invoice_type, Sale.FULL)
        self.assertTrue(sale.needs_review)


class ShiftAndInputTests(TestCase):
    def setUp(self):
        self.w = World()
        self.shift = self.w.open_shift(self.w.reg_a, self.w.cashier_a)

    def test_stale_shift_cannot_close_twice(self):
        """B7: close re-reads the shift under its lock."""
        stale = CashierShift.objects.get(pk=self.shift.pk)
        services.close_shift(self.shift, self.w.cashier_a, 0)
        with self.assertRaises(ValidationError):
            services.close_shift(stale, self.w.cashier_a, 500)

    def test_bad_numbers_are_400_not_500(self):
        """B6"""
        c = self.w.client(self.w.cashier_a, self.w.a)
        r = c.post(f"/api/v1/pos/shifts/{self.shift.id}/close/", {"counted_cash_paisa": "lots"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = c.post(f"/api/v1/pos/shifts/{self.shift.id}/close/", {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_debit_note_needs_explicit_rate(self):
        """B8"""
        sale, _ = self.w.sell((self.w.p1, 1), (self.w.p3, 1))
        with self.assertRaises(ValidationError):
            services.create_debit_note(sale=sale, by=self.w.manager_a, taxable_paisa=1000, reason="undercharged")
        dn = services.create_debit_note(sale=sale, by=self.w.manager_a, taxable_paisa=1000, reason="undercharged",
                                        tax_rate="0")
        self.assertEqual(dn.tax_paisa, 0)
