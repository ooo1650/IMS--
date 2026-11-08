from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from admin_extras.models import AuditLog
from ims.testutils import World

URL = "/api/v1/auth/test-login"


def client(ip="127.0.0.1"):
    return APIClient(REMOTE_ADDR=ip)


class TestLoginTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_off_by_default_and_invisible(self):
        self.assertEqual(client().get(URL).status_code, 404)
        self.assertEqual(client().post(URL, {"username": "admin"}, format="json").status_code, 404)
        self.assertFalse(client().get("/api/v1/auth/me").data.get("test_login", False))

    @override_settings(IMS_TEST_LOGIN=True)
    def test_signs_in_without_password_or_code(self):
        c = client()
        names = [u["username"] for u in c.get(URL).data["users"]]
        self.assertIn("manager_a", names)
        r = c.post(URL, {"username": "admin"}, format="json")  # an org admin normally needs an emailed code
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["username"], "admin")
        self.assertTrue(r.data["test_login"])
        self.assertEqual(c.get("/api/v1/auth/me").data["username"], "admin")
        self.assertTrue(AuditLog.objects.filter(action="auth.test_login", data__username="admin").exists())

    @override_settings(IMS_TEST_LOGIN=True, IMS_TRUSTED_PROXIES=0)
    def test_public_addresses_are_refused(self):
        self.assertEqual(client("8.8.8.8").get(URL).status_code, 404)
        self.assertEqual(client("8.8.8.8").post(URL, {"username": "admin"}, format="json").status_code, 404)
        self.assertEqual(client("192.168.1.20").get(URL).status_code, 200)

    @override_settings(IMS_TEST_LOGIN=True)
    def test_inactive_or_unknown_users_are_refused(self):
        User.objects.filter(username="clerk_a").update(is_active=False)
        self.assertEqual(client().post(URL, {"username": "clerk_a"}, format="json").status_code, 400)
        self.assertEqual(client().post(URL, {"username": "nobody"}, format="json").status_code, 400)
        self.assertNotIn("clerk_a", [u["username"] for u in client().get(URL).data["users"]])
