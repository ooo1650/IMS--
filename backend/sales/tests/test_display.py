"""Tablet customer display: pairing, what the token can reach, revocation, staleness."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from admin_extras.models import AuditLog
from ims.testutils import PASSWORD, World
from sales.models import RegisterDisplay
from tenancy.models import Branch, Organization, Register

P = "/api/v1/"
BILL = {"mode": "cart", "store": "Org", "total": "113.00", "tax": "13.00",
        "lines": [{"name": "Milk", "qty": 2, "amount": "113.00"}]}


class TabletDisplayTests(TestCase):
    def setUp(self):
        self.w = World()
        self.till = self.w.client(self.w.cashier_a, self.w.a)
        self.reg = self.w.reg_a.id
        self.tablet = APIClient(enforce_csrf_checks=True)  # no login, no cookies, no CSRF token

    def post(self, path, body, client=None):
        return (client or self.till).post(P + path, body, format="json")

    def code(self, client=None, register=None):
        r = self.post("pos/display/pair", {"register_id": register or self.reg}, client)
        self.assertEqual(r.status_code, 201, r.data)
        return r.data["code"]

    def pair(self):
        r = self.post("display/claim", {"code": self.code()}, self.tablet)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["token"]

    def screen(self, token, query=""):
        return self.tablet.get(f"{P}display/state{query}", HTTP_X_DISPLAY_TOKEN=token)

    def send(self, state=BILL):
        return self.post("pos/display/state", {"register_id": self.reg, "state": state})

    def test_bill_reaches_the_tablet(self):
        token = self.pair()
        self.assertEqual(self.screen(token).data["state"]["mode"], "idle")
        self.assertTrue(self.send().data["paired"])
        r = self.screen(token)
        self.assertEqual(r.data["state"], BILL)
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_unchanged_screen_is_a_small_reply(self):
        token = self.pair()
        self.send()
        version = self.screen(token).data["version"]
        self.send()  # the till's repeat
        r = self.screen(token, f"?version={version}")
        self.assertNotIn("state", r.data)
        self.assertEqual(r.data["version"], version)

    def test_typed_code_forgives_case_and_separators(self):
        code = self.code().lower().replace("-", " ")
        self.assertEqual(self.post("display/claim", {"code": code}, self.tablet).status_code, 200)

    def test_code_works_once(self):
        code = self.code()
        self.assertEqual(self.post("display/claim", {"code": code}, self.tablet).status_code, 200)
        r = self.post("display/claim", {"code": code}, self.tablet)
        self.assertEqual((r.status_code, r.data["code"]), (400, "display_code_invalid"))

    def test_code_expires(self):
        code = self.code()
        RegisterDisplay.objects.update(code_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post("display/claim", {"code": code}, self.tablet).status_code, 400)

    def test_junk_codes_are_400(self):
        for body in ({}, {"code": None}, {"code": 5}, {"code": ["A"]}, ["A"], {"code": "X" * 5000}):
            self.assertEqual(self.post("display/claim", body, self.tablet).status_code, 400, str(body)[:40])

    def test_secrets_are_stored_only_as_hashes(self):
        code = self.code().replace("-", "")
        self.assertNotIn(code, str(RegisterDisplay.objects.values().get()))
        token = self.post("display/claim", {"code": code}, self.tablet).data["token"]
        self.assertNotIn(token, str(RegisterDisplay.objects.values().get()))
        logged = str(list(AuditLog.objects.values()))
        self.assertNotIn(token, logged)
        self.assertNotIn(code, logged)

    def test_new_pairing_replaces_the_old_tablet_only_when_used(self):
        old = self.pair()
        code = self.code()
        self.assertEqual(self.screen(old).status_code, 200)
        new = self.post("display/claim", {"code": code}, self.tablet).data["token"]
        self.assertEqual(self.screen(old).status_code, 401)
        self.assertEqual(self.screen(new).status_code, 200)

    def test_disconnect_revokes(self):
        token = self.pair()
        self.post("pos/display/disconnect", {"register_id": self.reg})
        r = self.screen(token)
        self.assertEqual((r.status_code, r.data["code"]), (401, "display_unpaired"))
        self.assertFalse(self.send().data["paired"])

    def test_no_or_wrong_token(self):
        self.pair()
        self.assertEqual(self.tablet.get(f"{P}display/state").status_code, 401)
        self.assertEqual(self.screen("guess").status_code, 401)

    def test_stale_bill_is_not_left_on_screen(self):
        token = self.pair()
        self.send()
        live = self.screen(token).data["version"]
        RegisterDisplay.objects.update(sent_at=timezone.now() - timedelta(minutes=2))
        r = self.screen(token, f"?version={live}")
        self.assertEqual((r.data["state"]["mode"], r.data["state"]["lines"]), ("idle", []))
        self.send()  # the till is back with the same bill
        self.assertEqual(self.screen(token).data["state"], BILL)

    def test_switched_off_till_branch_or_business(self):
        token = self.pair()
        for obj in (self.w.reg_a, self.w.a, self.w.org):
            type(obj).objects.filter(pk=obj.pk).update(is_active=False)
            self.assertEqual(self.screen(token).status_code, 401, obj)
            type(obj).objects.filter(pk=obj.pk).update(is_active=True)

    def test_token_is_not_a_login(self):
        token = self.pair()
        for path in ("auth/me", "products/", "pos/config", f"pos/display?register_id={self.reg}"):
            self.assertIn(self.tablet.get(P + path, HTTP_X_DISPLAY_TOKEN=token).status_code, (401, 403), path)

    def test_other_branch_and_business_cannot_touch_it(self):
        self.pair()
        org = Organization.objects.create(name="Other", code="other")
        branch = Branch.objects.create(org=org, name="X", code="X")
        Register.objects.create(branch=branch, code="R1", name="Till")
        outsider = self.w.user("outsider", [])
        outsider.branch_roles.create(org=org, branch=branch, role="Cashier")
        for client in (self.w.client(self.w.cashier_b, self.w.b), self.w.client(outsider, branch)):
            for path in ("pos/display/pair", "pos/display/disconnect", "pos/display/state"):
                self.assertEqual(self.post(path, {"register_id": self.reg, "state": BILL}, client).status_code, 403, path)
            self.assertEqual(client.get(f"{P}pos/display?register_id={self.reg}").status_code, 403)

    def test_needs_pos_permission(self):
        clerk = self.w.client(self.w.clerk_a, self.w.a)
        self.assertEqual(self.post("pos/display/pair", {"register_id": self.reg}, clerk).status_code, 403)

    def test_till_endpoints_keep_csrf(self):
        c = APIClient(enforce_csrf_checks=True)
        c.login(username=self.w.cashier_a.username, password=PASSWORD)
        c.credentials(HTTP_X_BRANCH_ID=str(self.w.a.id))
        self.assertEqual(self.post("pos/display/pair", {"register_id": self.reg}, c).status_code, 403)

    def test_bad_screens_are_refused(self):
        self.pair()
        for state in (
            {**BILL, "qr": "https://evil.example/qr.png"},
            {**BILL, "qr": "data:image/svg+xml;base64,AAAA"},
            {**BILL, "mode": "admin"},
            {**BILL, "lines": BILL["lines"] * 101},
            None,
            "x",
        ):
            self.assertEqual(self.send(state).status_code, 400, str(state)[:50])

    def test_status(self):
        get = lambda: self.till.get(f"{P}pos/display?register_id={self.reg}").data  # noqa: E731
        self.assertEqual(get(), {"paired": False, "online": False, "code_pending": False})
        code = self.code()
        self.assertTrue(get()["code_pending"])
        self.post("display/claim", {"code": code}, self.tablet)
        self.assertEqual(get(), {"paired": True, "online": True, "code_pending": False})
        RegisterDisplay.objects.update(last_seen_at=timezone.now() - timedelta(minutes=2))
        self.assertFalse(get()["online"])
