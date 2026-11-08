"""Email + password sign-in, emailed codes (2FA), password reset, account changes."""
import re

from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import OneTimeCode, User, UserBranchRole
from admin_extras.models import AuditLog
from ims.testutils import PASSWORD, World

NEW_PASSWORD = "brand-new-pass-456"


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


@override_settings(IMS_EMAIL_ASYNC=False)
class AuthFlowTests(TestCase):
    def setUp(self):
        self.w = World()
        for u in (self.w.cashier_a, self.w.manager_a, self.w.admin):
            u.email = f"{u.username}@shop.test"
            u.save()

    def post(self, client, path, body):
        return client.post(f"/api/v1/{path}", body, format="json")

    def signed_in(self, client):
        return client.get("/api/v1/auth/me").status_code == 200

    def test_cashier_signs_in_with_email_without_a_code(self):
        c = APIClient()
        r = self.post(c, "auth/login", {"username": "CASHIER_A@shop.test", "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["username"], "cashier_a")
        self.assertEqual(len(mail.outbox), 0)

    def test_manager_needs_the_emailed_code(self):
        c = APIClient()
        r = self.post(c, "auth/login", {"username": "manager_a", "password": PASSWORD})
        self.assertTrue(r.data["otp_required"])
        self.assertEqual(r.data["sent_to"], "m********@shop.test")
        self.assertFalse(self.signed_in(c))
        code = last_code()
        self.assertFalse(OneTimeCode.objects.filter(code_hash__contains=code).exists(), "only a hash is stored")
        self.assertEqual(self.post(c, "auth/login/verify", {"code": "000000" if code != "000000" else "111111"}).status_code, 400)
        r = self.post(c, "auth/login/verify", {"code": code})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(self.signed_in(c))
        self.assertTrue(User.objects.get(pk=self.w.manager_a.pk).email_verified)
        self.assertEqual(self.post(APIClient(), "auth/login/verify", {"code": code}).status_code, 400, "single use")

    def test_code_dies_after_five_wrong_guesses(self):
        c = APIClient()
        self.post(c, "auth/login", {"username": "manager_a", "password": PASSWORD})
        code = last_code()
        wrong = "%06d" % ((int(code) + 1) % 10 ** 6)
        for _ in range(5):
            self.post(c, "auth/login/verify", {"code": wrong})
        # The spent code (400) or, by default, the account lockout (429) stops it.
        self.assertIn(self.post(c, "auth/login/verify", {"code": code}).status_code, (400, 429))
        from admin_extras.system import system_settings

        s = system_settings()
        s.login_max_per_username = s.login_max_per_ip = 50
        s.save()
        self.assertEqual(self.post(c, "auth/login/verify", {"code": code}).status_code, 400)
        self.assertFalse(self.signed_in(c))

    def test_code_required_role_without_email_is_blocked(self):
        User.objects.filter(pk=self.w.manager_a.pk).update(email="")
        r = self.post(APIClient(), "auth/login", {"username": "manager_a", "password": PASSWORD})
        self.assertEqual(r.status_code, 400)
        self.assertIn("no email", r.data["detail"])

    def test_forgot_password_does_not_reveal_accounts(self):
        known = self.post(APIClient(), "auth/password/forgot", {"email": "cashier_a@shop.test"})
        unknown = self.post(APIClient(), "auth/password/forgot", {"email": "nobody@shop.test"})
        self.assertEqual((known.status_code, known.data), (unknown.status_code, unknown.data))
        self.assertEqual(len(mail.outbox), 1)

    def test_reset_password_ends_old_sessions(self):
        old = self.w.client(self.w.cashier_a, self.w.a)
        self.assertTrue(self.signed_in(old))
        self.post(APIClient(), "auth/password/forgot", {"email": "cashier_a@shop.test"})
        weak = self.post(APIClient(), "auth/password/reset", {"email": "cashier_a@shop.test", "code": last_code(),
                                                              "password": "123"})
        self.assertEqual(weak.status_code, 400)
        r = self.post(APIClient(), "auth/password/reset", {"email": "cashier_a@shop.test", "code": last_code(),
                                                           "password": NEW_PASSWORD})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(self.signed_in(APIClient(HTTP_COOKIE=old.cookies.output(header="", sep=";"))))
        c = APIClient()
        self.assertEqual(self.post(c, "auth/login", {"username": "cashier_a", "password": NEW_PASSWORD}).status_code, 200)

    def test_change_password_keeps_this_session_only(self):
        here = APIClient()
        self.post(here, "auth/login", {"username": "cashier_a", "password": PASSWORD})
        other = APIClient()
        self.post(other, "auth/login", {"username": "cashier_a", "password": PASSWORD})
        bad = self.post(here, "auth/password/change", {"current_password": "nope", "new_password": NEW_PASSWORD})
        self.assertEqual(bad.status_code, 400)
        r = self.post(here, "auth/password/change", {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(self.signed_in(here))
        self.assertFalse(self.signed_in(other))

    def test_sign_out_everywhere_and_admin_sign_out(self):
        here, other = APIClient(), APIClient()
        for c in (here, other):
            self.post(c, "auth/login", {"username": "cashier_a", "password": PASSWORD})
        self.post(here, "auth/sign-out-everywhere", {})
        self.assertEqual((self.signed_in(here), self.signed_in(other)), (True, False))
        admin = self.w.client(self.w.admin)
        self.assertEqual(admin.post(f"/api/v1/users/{self.w.cashier_a.id}/sign-out/").status_code, 200)
        self.assertFalse(self.signed_in(here))

    def test_change_email_is_confirmed_by_a_code_to_the_new_address(self):
        c = APIClient()
        self.post(c, "auth/login", {"username": "cashier_a", "password": PASSWORD})
        r = self.post(c, "auth/email", {"email": "Ram.New@shop.test", "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(mail.outbox[-1].to, ["ram.new@shop.test"])
        r = self.post(c, "auth/email/verify", {"code": last_code()})
        self.assertEqual(r.data["email"], "ram.new@shop.test")
        self.assertTrue(r.data["email_verified"])
        taken = self.post(c, "auth/email", {"email": "manager_a@shop.test", "password": PASSWORD})
        self.assertEqual(taken.status_code, 400)

    def test_users_can_opt_in_but_required_roles_cannot_opt_out(self):
        c = APIClient()
        self.post(c, "auth/login", {"username": "cashier_a", "password": PASSWORD})
        self.assertTrue(self.post(c, "auth/2fa", {"enabled": True, "password": PASSWORD}).data["otp_required"])
        self.assertTrue(self.post(APIClient(), "auth/login", {"username": "cashier_a", "password": PASSWORD}).data["otp_required"])
        m = APIClient()
        self.post(m, "auth/login", {"username": "manager_a", "password": PASSWORD})
        self.post(m, "auth/login/verify", {"code": last_code()})
        self.assertEqual(self.post(m, "auth/2fa", {"enabled": False, "password": PASSWORD}).status_code, 400)

    def test_admin_sends_reset_and_every_step_is_audited(self):
        admin = self.w.client(self.w.admin)
        self.assertEqual(admin.post(f"/api/v1/users/{self.w.cashier_a.id}/send-reset/").status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["cashier_a@shop.test"])
        self.post(APIClient(), "auth/password/reset", {"email": "cashier_a@shop.test", "code": last_code(),
                                                       "password": NEW_PASSWORD})
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"user.reset_sent", "auth.password_reset"} <= actions)

    def test_unauthenticated_auth_posts_need_csrf(self):
        c = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.post(c, "auth/login", {"username": "cashier_a", "password": PASSWORD}).status_code, 403)
        self.assertEqual(self.post(c, "auth/password/forgot", {"email": "cashier_a@shop.test"}).status_code, 403)


class BrandTests(TestCase):
    def test_public_and_minimal(self):
        from tenancy.models import Organization

        Organization.objects.create(name="Himalaya Mart", name_np="हिमालय मार्ट")
        r = APIClient().get("/api/v1/auth/brand")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data), {"name", "name_np", "today_ad", "today_bs", "today_bs_text"})
        self.assertEqual(r.data["name"], "Himalaya Mart")
        self.assertRegex(r.data["today_bs"], r"^20[89]\d\.\d{2}\.\d{2}$")


@override_settings(IMS_EMAIL_ASYNC=False)
class CodeEmailTests(TestCase):
    """Code first in the subject (Gmail's copy-code chip, iOS/macOS autofill),
    plus an HTML version with a selectable code block."""

    def setUp(self):
        self.w = World()
        self.u = User.objects.get(pk=self.w.manager_a.pk)
        self.u.email = "m@example.com"
        self.u.save()

    def test_sign_in_code_email(self):
        from accounts import otp

        otp.issue(self.u, OneTimeCode.LOGIN)
        m = mail.outbox[-1]
        code = last_code()
        self.assertTrue(m.subject.startswith(f"{code} is your IMS sign-in code"))
        html = m.alternatives[0][0]
        self.assertIn(code, html)
        self.assertIn("user-select:all", html)
        self.assertIn("10 minutes", m.body)
        self.assertIn("Never share this code", m.body)

    def test_new_account_gets_setup_wording(self):
        from accounts import otp

        self.u.set_unusable_password()
        self.u.save()
        otp.issue(self.u, OneTimeCode.RESET)
        self.assertIn("account setup code", mail.outbox[-1].subject)
        self.assertIn("Create your IMS account", mail.outbox[-1].body)
        self.assertIn("/signup", mail.outbox[-1].body)

    def test_html_escapes_names(self):
        from accounts import otp

        self.u.first_name = "<b>Ram</b>"
        self.u.save()
        otp.issue(self.u, OneTimeCode.LOGIN)
        self.assertNotIn("<b>Ram</b>", mail.outbox[-1].alternatives[0][0])


@override_settings(IMS_EMAIL_ASYNC=False)
class SignupTests(TestCase):
    """Invited people create their account with the emailed setup code."""

    def setUp(self):
        from accounts import otp

        self.w = World()
        self.u = User.objects.create(username="newbie", email="new@example.com")
        self.u.set_unusable_password()
        self.u.save()
        UserBranchRole.objects.create(user=self.u, org=self.w.org, branch=self.w.a, role="Cashier")
        otp.issue(self.u, OneTimeCode.RESET)
        self.code = last_code()

    def post(self, c, body):
        c.get("/api/v1/auth/csrf")
        return c.post("/api/v1/auth/signup", body, format="json", HTTP_X_CSRFTOKEN=c.cookies["csrftoken"].value)

    def test_signup_sets_password_name_and_signs_in(self):
        c = APIClient(enforce_csrf_checks=True)
        r = self.post(c, {"email": "NEW@example.com", "code": self.code, "password": "a-long-pass-123",
                          "first_name": "Nabin", "last_name": "Rai"})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["username"], "newbie")
        u = User.objects.get(pk=self.u.pk)
        self.assertTrue(u.check_password("a-long-pass-123"))
        self.assertEqual((u.first_name, u.email_verified), ("Nabin", True))
        self.assertEqual(c.get("/api/v1/auth/me").data["username"], "newbie")

    def test_wrong_code_or_unknown_email_get_the_same_answer(self):
        c = APIClient(enforce_csrf_checks=True)
        r1 = self.post(c, {"email": "new@example.com", "code": "000000", "password": "a-long-pass-123"})
        r2 = self.post(c, {"email": "nobody@example.com", "code": self.code, "password": "a-long-pass-123"})
        self.assertEqual((r1.status_code, r2.status_code), (400, 400))
        self.assertEqual(r1.data["detail"], r2.data["detail"])
        self.assertFalse(User.objects.get(pk=self.u.pk).has_usable_password())

    def test_weak_password_does_not_spend_the_code(self):
        c = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.post(c, {"email": "new@example.com", "code": self.code, "password": "123"}).status_code, 400)
        r = self.post(c, {"email": "new@example.com", "code": self.code, "password": "a-long-pass-123"})
        self.assertEqual(r.status_code, 200)
