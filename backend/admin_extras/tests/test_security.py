"""Security controls: client IP behind a proxy, login lockout, health probe,
FonePay switched off, scheduler timing."""
from datetime import datetime

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from admin_extras.management.commands.run_scheduler import Job
from admin_extras.models import AuditLog
from ims.net import client_ip
from ims.testutils import PASSWORD, World
from sales.models import Sale


class ClientIpTests(SimpleTestCase):
    def req(self, xff=None):
        meta = {"REMOTE_ADDR": "10.0.0.2"}
        if xff is not None:
            meta["HTTP_X_FORWARDED_FOR"] = xff
        return RequestFactory().get("/", **meta)

    @override_settings(IMS_TRUSTED_PROXIES=1)
    def test_ignores_spoofed_left_entries(self):
        # Client sent "1.2.3.4"; the proxy appended the real address.
        self.assertEqual(client_ip(self.req("1.2.3.4, 203.0.113.9")), "203.0.113.9")
        self.assertEqual(client_ip(self.req("203.0.113.9")), "203.0.113.9")

    @override_settings(IMS_TRUSTED_PROXIES=0)
    def test_no_proxy_uses_socket(self):
        self.assertEqual(client_ip(self.req("1.2.3.4")), "10.0.0.2")

    @override_settings(IMS_TRUSTED_PROXIES=2)
    def test_missing_header_falls_back(self):
        self.assertEqual(client_ip(self.req("only-one")), "10.0.0.2")


@override_settings(IMS_TRUSTED_PROXIES=1)
class LoginLockoutTests(TestCase):
    def setUp(self):
        self.w = World()
        from admin_extras.system import system_settings

        s = system_settings()  # lockout limits live in the database now
        s.login_window_minutes, s.login_max_per_username, s.login_max_per_ip = 15, 3, 5
        s.save()

    def login(self, username, password, ip="198.51.100.7"):
        return APIClient().post("/api/v1/auth/login", {"username": username, "password": password},
                                format="json", HTTP_X_FORWARDED_FOR=ip)

    def test_locks_username_after_failures_even_with_right_password(self):
        for _ in range(3):
            self.assertEqual(self.login("cashier_a", "wrong").status_code, 400)
        r = self.login("cashier_a", PASSWORD)
        self.assertEqual(r.status_code, 429)
        self.assertTrue(AuditLog.objects.filter(action="auth.login_locked").exists())
        # Other users are unaffected (a clerk: managers also need an emailed code).
        self.assertEqual(self.login("clerk_a", PASSWORD, ip="198.51.100.8").status_code, 200)

    def test_locks_ip_spraying_many_usernames(self):
        for i in range(5):
            self.login(f"nobody{i}", "wrong", ip="192.0.2.50")
        self.assertEqual(self.login("cashier_a", PASSWORD, ip="192.0.2.50").status_code, 429)

    def test_spoofed_forwarded_for_does_not_dodge_ip_lockout(self):
        for i in range(5):
            self.login(f"nobody{i}", "wrong", ip=f"10.9.9.{i}, 192.0.2.60")
        self.assertEqual(self.login("cashier_a", PASSWORD, ip="10.9.9.99, 192.0.2.60").status_code, 429)


class HealthTests(TestCase):
    def test_health_needs_no_login(self):
        r = APIClient().get("/api/v1/health")
        self.assertEqual((r.status_code, r.data["status"]), (200, "ok"))


class FonepayDisabledTests(TestCase):
    def setUp(self):
        self.w = World()
        self.c = self.w.client(self.w.cashier_a, self.w.a)
        self.c.post("/api/v1/pos/shifts/open/", {"register_id": self.w.reg_a.id, "opening_cash_paisa": 0})

    def test_initiate_refused_without_side_effects(self):
        from sales.models import FonepaySettings

        FonepaySettings.objects.create(org=self.w.org, mode=FonepaySettings.DISABLED)
        r = self.c.post("/api/v1/pos/checkout/initiate", {
            "register_id": self.w.reg_a.id, "idempotency_key": "fd1",
            "lines": [{"product_id": self.w.p2.id, "qty": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("FonePay is not set up", str(r.data))
        self.assertFalse(Sale.objects.filter(idempotency_key="fd1").exists())
        self.assertEqual(self.w.on_hand(self.w.a, self.w.p2), 10)
        # Mock "pay" endpoint is unavailable too.
        self.assertEqual(self.c.post("/api/v1/pos/fonepay/mock-pay/ANY", {}).status_code, 404)


class SchedulerTests(SimpleTestCase):
    def test_due(self):
        t = datetime(2026, 10, 26, 3, 15)
        self.assertTrue(Job("x", every_minutes=1).due(t))
        self.assertTrue(Job("x", every_minutes=5).due(t))
        self.assertFalse(Job("x", every_minutes=10).due(t))
        self.assertTrue(Job("x", at="03:15").due(t))
        self.assertFalse(Job("x", at="03:16").due(t))


class ExportInjectionTests(SimpleTestCase):
    report = {
        "title": "Sales book", "subtitle": "",
        "columns": [("buyer", "Buyer", "text"), ("total", "Total", "money")],
        "rows": [{"buyer": '=HYPERLINK("http://evil","x")', "total": -500}, {"buyer": "Ram Traders", "total": 100}],
    }

    def test_csv_text_cells_cannot_start_a_formula(self):
        from compliance.exports import to_csv
        text = to_csv(self.report).decode("utf-8-sig")
        self.assertIn("'=HYPERLINK", text)
        self.assertIn("-5.00", text)  # negative money stays a number
        self.assertIn("Ram Traders", text)

    def test_xlsx_stores_text_not_formula(self):
        import io

        from openpyxl import load_workbook

        from compliance.exports import to_xlsx
        ws = load_workbook(io.BytesIO(to_xlsx(self.report))).active
        cell = ws.cell(row=5, column=1)
        self.assertEqual(cell.data_type, "s")
        self.assertTrue(str(cell.value).startswith("'="))
        self.assertEqual(ws.cell(row=5, column=2).value, -5)
