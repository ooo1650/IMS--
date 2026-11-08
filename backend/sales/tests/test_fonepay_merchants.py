"""Per-branch FonePay merchant accounts: admin API, secrets handling, and the
gateway / POS config / callback picking the right branch's merchant."""
import uuid
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from admin_extras.models import AuditLog
from ims.testutils import World
from sales import fonepay, services
from sales.models import FonepayMerchant, FonepaySettings

URL = "/api/v1/fonepay-merchants/"


LIVE_URL = "https://dev-merchantapi.fonepay.com"


def set_mode(org, mode, base_url=""):
    """FonePay mode and API address are stored in the database now."""
    FonepaySettings.objects.update_or_create(org=org, defaults={"mode": mode, "base_url": base_url})


class MerchantApiTests(TestCase):
    def setUp(self):
        self.w = World()
        self.admin = self.w.client(self.w.admin)

    def test_list_shows_every_branch_without_secrets(self):
        FonepayMerchant.objects.create(branch=self.w.a, merchant_code="M-A", username="ua")
        r = self.admin.get(URL)
        self.assertEqual(r.status_code, 200)
        rows = {row["branch_code"]: row for row in r.data["results"]}
        self.assertEqual(set(rows), {"A", "B"})
        self.assertEqual(rows["A"]["merchant_code"], "M-A")
        self.assertEqual(rows["B"]["merchant_code"], "")
        self.assertNotIn("password", rows["A"])
        self.assertNotIn("secret", rows["A"])

    def test_put_creates_then_blank_secret_keeps_stored_value(self):
        r = self.admin.put(f"{URL}{self.w.a.id}/", {"merchant_code": "M-A", "username": "ua", "password": "pw1",
                                                    "secret": "s1", "enabled": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["password_set"] and r.data["secret_set"])
        m = FonepayMerchant.objects.get(branch=self.w.a)
        self.assertEqual((m.password, m.secret), ("pw1", "s1"))
        self.assertNotIn("pw1", m.password_enc)  # encrypted at rest

        r = self.admin.patch(f"{URL}{self.w.a.id}/", {"merchant_code": "M-A2", "password": ""}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        m.refresh_from_db()
        self.assertEqual((m.merchant_code, m.password, m.secret), ("M-A2", "pw1", "s1"))

        r = self.admin.patch(f"{URL}{self.w.a.id}/", {"clear_secret": True}, format="json")
        m.refresh_from_db()
        self.assertEqual(m.secret_enc, "")
        self.assertFalse(r.data["secret_set"])

    def test_secret_changes_are_audited_without_values(self):
        self.admin.put(f"{URL}{self.w.a.id}/", {"merchant_code": "M", "password": "hunter2-pw"}, format="json")
        log = AuditLog.objects.filter(action="fonepay.credentials_changed").get()
        self.assertEqual(log.data["fields"], ["password"])
        self.assertFalse(AuditLog.objects.filter(data__icontains="hunter2-pw").exists())

    def test_live_mode_refuses_enabling_an_incomplete_merchant(self):
        set_mode(self.w.org, "http", LIVE_URL)
        r = self.admin.put(f"{URL}{self.w.a.id}/", {"merchant_code": "M", "enabled": True}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("password", str(r.data["enabled"]))

    def test_rejects_odd_merchant_code(self):
        r = self.admin.put(f"{URL}{self.w.a.id}/", {"merchant_code": "M A;--"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_branch_staff_cannot_read_or_edit(self):
        for user in (self.w.manager_a, self.w.cashier_a):
            c = self.w.client(user, self.w.a)
            self.assertEqual(c.get(URL).status_code, 403)
            self.assertEqual(c.put(f"{URL}{self.w.a.id}/", {"merchant_code": "X"}, format="json").status_code, 403)

    def test_unknown_branch_is_404(self):
        self.assertEqual(self.admin.put(f"{URL}999999/", {"merchant_code": "X"}, format="json").status_code, 404)


class BranchGatewayTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)

    def initiate(self):
        return services.initiate_fonepay(branch=self.w.a, register=self.w.reg_a, cashier=self.w.cashier_a,
                                         cart=self.w.cart((self.w.p1, 1)), idempotency_key=str(uuid.uuid4()))[0]

    def test_mock_works_without_merchant_but_respects_switch_off(self):
        self.assertEqual(fonepay.branch_backend(self.w.a), "mock")
        FonepayMerchant.objects.create(branch=self.w.a, enabled=False)
        self.assertEqual(fonepay.branch_backend(self.w.a), "disabled")
        self.assertEqual(fonepay.branch_backend(self.w.b), "mock")  # other branch unaffected
        with self.assertRaises(ValidationError):
            self.initiate()

    def test_pos_config_reports_branch_status(self):
        FonepayMerchant.objects.create(branch=self.w.a, enabled=False)
        r = self.w.client(self.w.cashier_a, self.w.a).get("/api/v1/pos/config")
        self.assertEqual(r.data["fonepay_backend"], "disabled")

    def test_live_gateway_uses_the_branch_credentials(self):
        m = FonepayMerchant(branch=self.w.a, enabled=True, merchant_code="MA", username="ua")
        m.password, m.secret = "pw-a", "sec-a"
        m.save()
        FonepayMerchant.objects.create(branch=self.w.b, enabled=False, merchant_code="MB")
        sent = []

        def fake_post(gw, path, body):
            sent.append((gw, body))
            return {"qrMessage": "QR"}

        set_mode(self.w.org, "http", LIVE_URL)
        with mock.patch.object(fonepay.HttpFonepayGateway, "_post", fake_post):
            self.assertEqual(fonepay.branch_backend(self.w.a), "http")
            self.assertEqual(fonepay.branch_backend(self.w.b), "disabled")
            self.initiate()
        gw, body = sent[0]
        self.assertEqual((body["merchantCode"], body["username"], body["password"]), ("MA", "ua", "pw-a"))
        self.assertEqual(gw.secret, "sec-a")

    def test_callback_is_verified_with_the_branch_secret(self):
        m = FonepayMerchant(branch=self.w.a, enabled=True)
        m.secret = "branch-a-secret"
        m.save()
        sale = self.initiate()
        prn = sale.payments.get().fonepay_prn
        fonepay.MockFonepayGateway().simulate_payment(prn)
        body = {"prn": prn, "status": "success", "amount": "113.00", "trx_id": "X"}
        msg = ",".join(body[k] for k in ("prn", "status", "amount", "trx_id"))
        c = self.w.client(self.w.cashier_a)
        # Signed with the server-wide key: refused, this branch has its own.
        r = c.post("/api/v1/pos/fonepay/callback", {**body, "signature": fonepay.sign(msg)}, format="json")
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/v1/pos/fonepay/callback", {**body, "signature": fonepay.sign(msg, "branch-a-secret")},
                   format="json")
        self.assertEqual(r.status_code, 200)

    def test_empty_secret_never_verifies(self):
        self.assertFalse(fonepay.verify_signature("m", fonepay.sign("m", ""), ""))


SETTINGS_URL = "/api/v1/fonepay-settings"


class FonepaySettingsApiTests(TestCase):
    """Mode and API address are edited in the app and stored in the database."""

    def setUp(self):
        self.w = World()
        self.admin = self.w.client(self.w.admin)

    def test_saved_in_db_and_used_by_branches(self):
        r = self.admin.patch(SETTINGS_URL, {"mode": "disabled"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(FonepaySettings.objects.get().mode, "disabled")
        self.assertEqual(fonepay.branch_backend(self.w.a), "disabled")
        r = self.admin.patch(SETTINGS_URL, {"mode": "http", "base_url": LIVE_URL + "/"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual((r.data["backend"], r.data["base_url"]), ("http", LIVE_URL))
        self.assertTrue(AuditLog.objects.filter(action__startswith="fonepaysettings.").exists())

    def test_api_address_must_be_https_on_a_fonepay_domain(self):
        for bad in ("http://dev-merchantapi.fonepay.com", "https://evil.example/fonepay.com", "https://fonepay.com.evil.net"):
            r = self.admin.patch(SETTINGS_URL, {"mode": "http", "base_url": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(self.admin.patch(SETTINGS_URL, {"mode": "http"}, format="json").status_code, 400)  # no address

    @override_settings(FONEPAY_MOCK_ALLOWED=False)
    def test_demo_mode_needs_server_permission(self):
        r = self.admin.patch(SETTINGS_URL, {"mode": "mock"}, format="json")
        self.assertEqual(r.status_code, 400)
        # A demo row saved earlier (e.g. on a dev box) counts as off on such a server.
        FonepaySettings.objects.create(org=self.w.org, mode="mock")
        self.assertEqual(fonepay.server_mode(self.w.org), "disabled")

    def test_only_org_admins(self):
        c = self.w.client(self.w.manager_a, self.w.a)
        self.assertEqual(c.get(SETTINGS_URL).status_code, 403)
        self.assertEqual(c.patch(SETTINGS_URL, {"mode": "disabled"}, format="json").status_code, 403)

    def test_callback_without_branch_secret_is_refused_in_live_mode(self):
        set_mode(self.w.org, "http", LIVE_URL)
        self.assertEqual(fonepay.callback_secret(self.w.a), "")
