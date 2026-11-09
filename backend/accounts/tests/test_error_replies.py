"""Error replies the app acts on: every rate limit says how long to wait
(``retry_after`` + Retry-After) and why (``code``); sign-in code errors say
what to do next; Django's own errors are JSON on /api/."""
import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OneTimeCode
from admin_extras.models import AuditLog
from ims import errors
from ims.testutils import PASSWORD, World


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


@override_settings(IMS_EMAIL_ASYNC=False)
class RateLimitReplies(TestCase):
    def setUp(self):
        self.w = World()
        self.w.manager_a.email = "manager_a@shop.test"
        self.w.manager_a.save()
        self.c = APIClient()

    def post(self, path, body=None):
        return self.c.post(f"/api/v1/{path}", body or {}, format="json")

    def test_lockout_says_how_long(self):
        for _ in range(5):
            self.assertEqual(self.post("auth/login", {"username": "cashier_a", "password": "nope"}).status_code, 400)
        r = self.post("auth/login", {"username": "cashier_a", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.data["code"], "locked")
        self.assertTrue(14 * 60 < r.data["retry_after"] <= 15 * 60, r.data)  # default 15-minute window
        self.assertEqual(r["Retry-After"], str(r.data["retry_after"]))
        self.assertIn("15 minutes", r.data["detail"])

    def test_lockout_lifts_as_failures_age_out(self):
        # The failures happened 14 minutes ago: one minute of lockout left. (The audit
        # log is append-only, also in Postgres, so the clock moves, not the rows.)
        earlier = timezone.now() - timedelta(minutes=14)
        with mock.patch("django.utils.timezone.now", return_value=earlier):
            for _ in range(5):
                self.post("auth/login", {"username": "cashier_a", "password": "nope"})
        self.assertEqual(AuditLog.objects.filter(action="auth.login_failed", at__lte=earlier).count(), 5)
        r = self.post("auth/login", {"username": "cashier_a", "password": PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertLessEqual(r.data["retry_after"], 60)
        self.assertIn("seconds", r.data["detail"])

    def test_resend_too_soon_gives_seconds(self):
        self.post("auth/login", {"username": "manager_a", "password": PASSWORD})
        r = self.post("auth/login/resend")
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.data["code"], "otp_wait")
        self.assertTrue(0 < r.data["retry_after"] <= 60)
        self.assertIn("seconds", r.data["detail"])

    def test_hourly_code_limit_gives_minutes(self):
        self.post("auth/login", {"username": "manager_a", "password": PASSWORD})
        OneTimeCode.objects.update(created_at=timezone.now() - timedelta(minutes=50))
        for _ in range(5):
            OneTimeCode.objects.create(user=self.w.manager_a, purpose=OneTimeCode.LOGIN, code_hash="x", sent_to="x",
                                       expires_at=timezone.now())
        OneTimeCode.objects.filter(code_hash="x").update(created_at=timezone.now() - timedelta(minutes=30))
        r = self.post("auth/login/resend")
        self.assertEqual(r.status_code, 429)
        self.assertTrue(9 * 60 < r.data["retry_after"] <= 10 * 60, r.data)  # oldest of the 6 leaves in ~10 min
        self.assertIn("10 minutes", r.data["detail"])


@override_settings(IMS_EMAIL_ASYNC=False)
class SignInCodeReplies(TestCase):
    def setUp(self):
        self.w = World()
        self.w.manager_a.email = "manager_a@shop.test"
        self.w.manager_a.save()
        self.c = APIClient()
        self.c.post("/api/v1/auth/login", {"username": "manager_a", "password": PASSWORD}, format="json")
        self.code = last_code()

    def verify(self, code):
        return self.c.post("/api/v1/auth/login/verify", {"code": code}, format="json")

    def test_wrong_then_spent(self):
        wrong = "%06d" % ((int(self.code) + 1) % 10 ** 6)
        r = self.verify(wrong)
        self.assertEqual(r.data["code"], "otp_invalid")
        for _ in range(4):
            r = self.verify(wrong)
        self.assertEqual(r.data["code"], "otp_spent")
        self.assertIn("Resend code", r.data["detail"])

    def test_expired_sign_in_tells_the_app_to_start_over(self):
        session = self.c.session
        session["pending_2fa"]["at"] -= 3600
        session.save()
        r = self.verify(self.code)
        self.assertEqual((r.status_code, r.data["code"]), (400, "login_expired"))


class JsonErrorPages(TestCase):
    def test_csrf_failure_on_sign_in_is_json(self):
        c = APIClient(enforce_csrf_checks=True)
        r = c.post("/api/v1/auth/login", {"username": "x", "password": "y"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "csrf")

    def test_signed_out_is_plain_words(self):
        r = APIClient().get("/api/v1/products/")
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(r.data["code"], "not_authenticated")

    @override_settings(DEBUG=False)
    def test_unknown_api_path_is_json_404(self):
        r = self.client.get("/api/v1/no-such-thing/")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_found")

    def test_server_error_is_json_for_api_only(self):
        rf = RequestFactory()
        r = errors.server_error(rf.get("/api/v1/products/"))
        self.assertEqual((r.status_code, r["Content-Type"]), (500, "application/json"))
        self.assertEqual(errors.server_error(rf.get("/")).status_code, 500)
