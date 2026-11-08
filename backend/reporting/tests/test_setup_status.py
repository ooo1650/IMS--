"""The getting-started checklist reads the active business's progress."""
from django.test import TestCase
from rest_framework.test import APIClient

from ims.testutils import World


class SetupStatusTests(TestCase):
    def test_reports_progress_for_the_active_business(self):
        w = World()
        r = w.client(w.admin, w.a).get("/api/v1/setup-status")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(set(r.data), {"products", "stock", "suppliers", "staff", "sale"})
        self.assertTrue(r.data["products"])

    def test_needs_sign_in(self):
        self.assertEqual(APIClient().get("/api/v1/setup-status").status_code, 403)
