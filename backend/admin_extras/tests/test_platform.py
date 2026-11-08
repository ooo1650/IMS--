"""Operator console API: platform admins create businesses, branches and admin
accounts, edit settings that used to be environment variables, and run jobs."""
from io import StringIO

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import OneTimeCode, User, UserBranchRole
from admin_extras.models import AuditLog
from admin_extras.system import system_settings
from ims.testutils import PASSWORD, World
from tenancy.models import Branch, Organization, Register, rules_for

P = "/api/v1/platform"


@override_settings(IMS_EMAIL_ASYNC=False)
class PlatformApiTests(TestCase):
    def setUp(self):
        self.w = World()
        self.op = User.objects.create(username="op", email="op@example.com", is_platform_admin=True)
        self.op.set_password(PASSWORD)
        self.op.save()
        self.c = self.w.client(self.op)

    def test_only_platform_admins(self):
        for user in (self.w.admin, self.w.manager_a, self.w.cashier_a):
            c = self.w.client(user, self.w.a)
            for url in ("/overview", "/businesses", "/users", "/system", "/jobs", "/audit"):
                self.assertEqual(c.get(P + url).status_code, 403, (user.username, url))
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().get(P + "/businesses").status_code, 403)

    def test_platform_admin_always_needs_a_code_and_holds_no_business(self):
        self.assertTrue(self.op.otp_required())
        me = self.c.get("/api/v1/auth/me").data
        self.assertTrue(me["is_platform_admin"])
        self.assertEqual(me["businesses"], [])

    def test_create_business_with_first_branch_and_admin(self):
        r = self.c.post(P + "/businesses", {
            "name": "Everest Gear Pvt. Ltd.", "pan": "305555555", "trade": "Outdoor gear",
            "first_branch_name": "Thamel", "first_branch_code": "gear",
            "admin_username": "gear_admin", "admin_email": "gear@example.com", "admin_name": "Pemba Sherpa",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        org = Organization.objects.get(pk=r.data["id"])
        self.assertEqual(org.code, "everest-gear")
        branch = Branch.objects.get(org=org)
        self.assertEqual(branch.code, "GEAR")
        self.assertTrue(Register.objects.filter(branch=branch, code="R1").exists())
        admin = User.objects.get(username="gear_admin")
        self.assertFalse(admin.has_usable_password())  # they set it from the emailed code
        self.assertEqual(admin.org_role(org), "OrgAdmin")
        self.assertTrue(OneTimeCode.objects.filter(user=admin, purpose=OneTimeCode.RESET).exists())
        self.assertEqual(mail.outbox[-1].to, ["gear@example.com"])
        self.assertTrue(AuditLog.objects.filter(action="platform.business_created").exists())
        # The new admin sees only the new business.
        self.assertEqual(r.data["branches"], 1)

    def test_business_validation(self):
        self.w.org.pan = "301111111"
        self.w.org.save()
        r = self.c.post(P + "/businesses", {"name": "Dup", "pan": "301111111"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post(P + "/businesses", {"name": "X", "first_branch_name": "Y", "first_branch_code": "A"}, format="json")
        self.assertEqual(r.status_code, 400)  # branch code A is taken (codes start invoice numbers)

    def test_rules_and_tax_settings_per_business(self):
        pk = self.w.org.pk
        r = self.c.patch(f"{P}/businesses/{pk}/rules", {"cash_rounding_paisa": 100, "cashier_max_discount_bp": 1000},
                         format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(rules_for(self.w.org).cash_rounding_paisa, 100)
        self.assertEqual(self.c.patch(f"{P}/businesses/{pk}/rules", {"cash_rounding_paisa": 7}, format="json").status_code, 400)
        r = self.c.patch(f"{P}/businesses/{pk}/tax-settings", {"cbms_username": "u", "cbms_password": "secret"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["cbms_password_set"])
        self.assertNotIn("cbms_password", r.data)

    def test_branches_and_registers(self):
        pk = self.w.org.pk
        r = self.c.post(f"{P}/businesses/{pk}/branches", {"name": "Lalitpur", "code": "lal"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        b = Branch.objects.get(code="LAL")
        self.assertEqual(b.org, self.w.org)
        r = self.c.post(f"{P}/branches/{b.pk}/registers", {"code": "r2", "name": "Till 2"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(b.registers.count(), 2)
        rows = self.c.get(f"{P}/businesses/{pk}/branches").data
        self.assertIn("LAL", [x["code"] for x in rows])

    def test_users_across_businesses(self):
        other = Organization.objects.create(name="Shoes", code="shoes")
        ob = Branch.objects.create(org=other, name="S1", code="S1")
        r = self.c.post(P + "/users", {
            "username": "owner", "email": "owner@example.com", "password": "a-long-pass-123",
            "roles": [{"org": self.w.org.pk, "role": "OrgAdmin"}, {"org": other.pk, "branch": ob.pk, "role": "BranchManager"}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        owner = User.objects.get(username="owner")
        self.assertEqual(owner.org_ids(), {self.w.org.pk, other.pk})
        r = self.c.post(P + "/users", {"username": "bad", "password": "a-long-pass-123",
                                       "roles": [{"org": self.w.org.pk, "branch": ob.pk, "role": "Cashier"}]}, format="json")
        self.assertEqual(r.status_code, 400)  # branch not in that business
        r = self.c.patch(f"{P}/users/{self.op.pk}", {"roles": [{"org": self.w.org.pk, "role": "OrgAdmin"}]}, format="json")
        self.assertEqual(r.status_code, 400)  # platform admins hold no business roles
        r = self.c.patch(f"{P}/users/{owner.pk}", {"is_active": False}, format="json")
        self.assertFalse(User.objects.get(pk=owner.pk).is_active)
        self.assertEqual(self.c.patch(f"{P}/users/{self.op.pk}", {"is_active": False}, format="json").status_code, 400)

    def test_system_settings_replace_env(self):
        r = self.c.patch(P + "/system", {"smtp_host": "smtp.example.com", "smtp_password": "app-pass",
                                         "otp_required_roles": ["SuperAdmin"], "group_name": "Himalaya Group"},
                         format="json")
        self.assertEqual(r.status_code, 200, r.data)
        s = system_settings()
        self.assertEqual(s.smtp_password, "app-pass")
        self.assertNotIn("app-pass", s.smtp_password_enc)
        self.assertNotIn("smtp_password", r.data)
        # Roles needing a sign-in code now come from here: managers no longer need one.
        self.assertFalse(User.objects.get(pk=self.w.manager_a.pk).otp_required())
        self.assertEqual(self.c.patch(P + "/system", {"otp_required_roles": ["Nope"]}, format="json").status_code, 400)

    def test_jobs_run_and_are_audited(self):
        names = [j["name"] for j in self.c.get(P + "/jobs").data]
        self.assertIn("reconcile_stock", names)
        r = self.c.post(P + "/jobs/reconcile_stock")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["ok"])
        self.assertTrue(AuditLog.objects.filter(action="platform.job_run", data__job="reconcile_stock").exists())
        self.assertEqual(self.c.post(P + "/jobs/rm_rf").status_code, 404)

    @override_settings(IMS_ALLOW_DEMO_SEED=False)
    def test_demo_seed_needs_server_permission(self):
        self.assertEqual(self.c.post(P + "/demo-seed").status_code, 403)

    def test_create_platform_admin_command(self):
        out = StringIO()
        call_command("create_platform_admin", "--username", "op2", "--email", "op2@example.com",
                     "--password", "a-long-pass-123", stdout=out)
        self.assertTrue(User.objects.get(username="op2").is_platform_admin)
        self.assertFalse(UserBranchRole.objects.filter(user__username="op2").exists())

    def test_test_email_unreachable_server_fails_fast_with_hint(self):
        self.c.patch(P + "/system", {"smtp_host": "127.0.0.1", "smtp_port": 1}, format="json")
        r = self.c.post(P + "/system/test-email", {"to": "x@example.com"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("2525", r.data["detail"])
