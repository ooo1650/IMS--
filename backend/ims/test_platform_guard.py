"""Without nginx in front (Vercel + Render), the backend keeps the operator
console API to the console's address and the operators' networks."""
from django.test import TestCase

from accounts.models import User
from ims.testutils import PASSWORD, World

URL = "/api/v1/platform/overview"
CONSOLE = "https://console.example.com"


class PlatformGuardTests(TestCase):
    def setUp(self):
        World()
        op = User.objects.create(username="op", email="op@example.com", is_platform_admin=True)
        op.set_password(PASSWORD)
        op.save()
        self.op = op

    def get(self, **headers):
        from rest_framework.test import APIClient

        c = APIClient()
        c.force_authenticate(self.op)
        return c.get(URL, headers=headers)

    def test_off_by_default(self):
        self.assertEqual(self.get().status_code, 200)

    def test_origin_must_be_the_console(self):
        with self.settings(IMS_PLATFORM_ORIGINS=[CONSOLE]):
            self.assertEqual(self.get().status_code, 404)
            self.assertEqual(self.get(Referer="https://ims.example.com/").status_code, 404)
            self.assertEqual(self.get(Origin="https://ims.example.com").status_code, 404)
            self.assertEqual(self.get(Referer=CONSOLE + "/businesses").status_code, 200)
            self.assertEqual(self.get(Origin=CONSOLE).status_code, 200)

    def test_operator_networks(self):
        with self.settings(IMS_PLATFORM_ALLOW=["203.0.113.0/24"], IMS_TRUSTED_PROXIES=1):
            self.assertEqual(self.get(X_Forwarded_For="198.51.100.7").status_code, 404)
            self.assertEqual(self.get(X_Forwarded_For="203.0.113.9").status_code, 200)

    def test_rest_of_the_api_untouched(self):
        with self.settings(IMS_PLATFORM_ORIGINS=[CONSOLE], IMS_PLATFORM_ALLOW=["203.0.113.0/24"]):
            self.assertEqual(self.client.get("/api/v1/health").status_code, 200)

