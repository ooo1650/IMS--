"""Staff and pay: the monthly sums, owner-only access, business isolation,
the Excel export and the staff-list import."""
import io
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import UserBranchRole
from accounts.roles import ORG_ADMIN
from admin_extras.models import AuditLog
from ims.testutils import World
from staff import services
from staff.models import StaffMember, StaffPayment
from tenancy.models import Branch, Organization

MONTH = "2083-08"  # Mangsir 2083
IN_MONTH = "2026-11-23"  # = 2083.08.07


class StaffTests(TestCase):
    def setUp(self):
        self.w = World()
        self.adm = self.w.client(self.w.admin, self.w.a)
        self.ram = StaffMember.objects.create(org=self.w.org, branch=self.w.a, name="Ram", monthly_salary_paisa=2_000_000)
        self.sita = StaffMember.objects.create(org=self.w.org, branch=self.w.b, name="Sita", monthly_salary_paisa=1_500_000)

    def pay(self, staff, kind, amount, method="cash", client=None, **extra):
        body = {"staff": staff.id, "kind": kind, "amount_paisa": amount, "paid_on": IN_MONTH, "method": method, **extra}
        return (client or self.adm).post("/api/v1/staff-payments/", body, format="json")

    def summary(self, **q):
        return self.adm.get("/api/v1/staff-summary", {"month": MONTH, **q}).json()

    def row(self, data, staff):
        return next(r for r in data["rows"] if r["id"] == staff.id)

    def test_month_of_bs(self):
        self.assertEqual(services.month_of(date(2026, 11, 23)), MONTH)
        self.assertEqual(services.month_label(MONTH), "Mangsir 2083")
        self.assertIsNone(services.parse_month("2083-13"))
        self.assertEqual(services.recent_months(3, date(2026, 11, 23)), ["2083-08", "2083-07", "2083-06"])

    def test_monthly_sums(self):
        for kind, amount, method in (("advance", 500_000, "cash"), ("repayment", 200_000, "salary"),
                                     ("deduction", 100_000, ""), ("salary", 1_500_000, "bank"), ("bonus", 300_000, "cash")):
            self.assertEqual(self.pay(self.ram, kind, amount, method).status_code, 201, kind)
        r = self.row(self.summary(), self.ram)
        self.assertEqual(r["salary_paid_paisa"], 1_500_000)
        self.assertEqual(r["deductions_paisa"], 300_000)  # deduction + advance cut from salary
        self.assertEqual(r["due_paisa"], 200_000)  # 20,000 - 3,000 - 15,000
        self.assertEqual(r["advance_owed_paisa"], 300_000)
        self.assertEqual(r["bonus_paisa"], 300_000)
        s = self.summary()
        self.assertEqual(s["not_paid"], 2)  # Ram still has 2,000 due; Sita got nothing
        self.assertEqual(s["totals"]["paid_out_paisa"], 1_500_000 + 300_000 + 500_000)
        self.assertEqual(s["month_label"], "Mangsir 2083")

    def test_branch_filter_and_leavers(self):
        self.assertEqual([r["name"] for r in self.summary(branch=self.w.b.id)["rows"]], ["Sita"])
        self.sita.is_active = False
        self.sita.save()
        self.assertEqual([r["name"] for r in self.summary()["rows"]], ["Ram"])
        self.pay(self.sita, "salary", 100_000)  # a final payment after leaving still shows
        row = self.row(self.summary(), self.sita)
        self.assertEqual((row["salary_paid_paisa"], row["due_paisa"]), (100_000, 0))

    def test_payment_keeps_branch_and_month(self):
        r = self.pay(self.ram, "salary", 100)
        self.assertEqual((r.data["month"], r.data["branch"], r.data["paid_on_bs"]), (MONTH, self.w.a.id, "2083.08.07"))
        late = self.pay(self.ram, "salary", 100, month="2083-07").data  # Kartik's salary paid in Mangsir
        self.assertEqual(late["month_label"], "Kartik 2083")
        self.ram.branch = self.w.b
        self.ram.save()
        self.assertEqual(StaffPayment.objects.get(pk=r.data["id"]).branch, self.w.a)
        self.assertTrue(AuditLog.objects.filter(object_type__icontains="staffpayment").exists())

    def test_rules(self):
        self.assertEqual(self.pay(self.ram, "salary", 100, method="").status_code, 400)
        self.assertEqual(self.pay(self.ram, "salary", 100, method="salary").status_code, 400)
        self.assertEqual(self.pay(self.ram, "deduction", 100, method="cash").data["method"], "")
        self.assertEqual(self.pay(self.ram, "salary", 0).status_code, 400)
        self.assertEqual(self.pay(self.ram, "salary", 100, month="2083-13").status_code, 400)
        self.assertEqual(self.adm.get("/api/v1/staff-payments/").status_code, 400)  # needs a staff or a month
        self.assertEqual(self.adm.get("/api/v1/staff-summary", {"month": "Mangsir"}).status_code, 400)

    def test_correct_and_remove(self):
        p = self.pay(self.ram, "salary", 100).data
        self.assertEqual(self.adm.patch(f"/api/v1/staff-payments/{p['id']}/", {"amount_paisa": 250}, format="json").status_code, 200)
        self.assertEqual(self.adm.delete(f"/api/v1/staff-payments/{p['id']}/").status_code, 204)
        self.assertEqual(self.adm.delete(f"/api/v1/staff/{self.ram.id}/").status_code, 405)  # leavers are marked, not deleted

    def test_only_owners_and_admins(self):
        for user, branch in ((self.w.manager_a, self.w.a), (self.w.cashier_a, self.w.a)):
            c = self.w.client(user, branch)
            self.assertEqual(c.get("/api/v1/staff/").status_code, 403)
            self.assertEqual(c.get("/api/v1/staff-summary").status_code, 403)
            self.assertEqual(c.get(f"/api/v1/staff-payments/?staff={self.ram.id}").status_code, 403)
            self.assertEqual(self.pay(self.ram, "salary", 100, client=c).status_code, 403)
            self.assertEqual(c.get("/api/v1/staff-export").status_code, 403)

    def test_other_business_sees_nothing(self):
        org2 = Organization.objects.create(name="Shoes", code="shoes")
        b2 = Branch.objects.create(org=org2, name="S", code="S")
        boss = self.w.user("boss2", [])
        UserBranchRole.objects.create(user=boss, org=org2, branch=None, role=ORG_ADMIN)
        c = self.w.client(boss, b2)
        self.assertEqual(c.get("/api/v1/staff/").json(), [])
        self.assertEqual(c.get("/api/v1/staff-summary").json()["rows"], [])
        self.assertEqual(c.patch(f"/api/v1/staff/{self.ram.id}/", {"name": "x"}, format="json").status_code, 404)
        self.assertEqual(self.pay(self.ram, "salary", 100, client=c).status_code, 400)
        self.assertEqual(c.post("/api/v1/staff/", {"name": "X", "branch": self.w.a.id}, format="json").status_code, 400)
        p = self.pay(self.ram, "salary", 100).data
        self.assertEqual(c.delete(f"/api/v1/staff-payments/{p['id']}/").status_code, 404)
        self.assertEqual(c.get(f"/api/v1/staff-payments/?staff={self.ram.id}").json(), [])

    def test_export(self):
        self.pay(self.ram, "salary", 1_500_000)
        self.pay(self.ram, "deduction", 100_000, method="")
        r = self.adm.get("/api/v1/staff-export", {"month": MONTH, "export": "csv"})
        text = r.content.decode()
        self.assertIn("Ram", text)
        self.assertIn("15000.00", text)
        self.assertEqual(r["Content-Disposition"], 'attachment; filename="staff-payments-2083-08.csv"')
        x = self.adm.get("/api/v1/staff-export", {"month": MONTH, "export": "xlsx"})
        self.assertEqual(x.status_code, 200)
        from openpyxl import load_workbook

        load_workbook(io.BytesIO(x.content))

    def test_import_staff(self):
        def upload(text, dry):
            f = SimpleUploadedFile("staff.csv", text.encode(), content_type="text/csv")
            return self.adm.post("/api/v1/import/staff", {"file": f, "dry_run": "true" if dry else "false"}, format="multipart").json()

        good = "name,branch,position,phone,joined,salary\nHari,A,Helper,98,2024-04-14,12000\nRam,a,Cashier,,,21000.50\n"
        preview = upload(good, True)
        self.assertEqual((preview["ok"], preview["creates"], preview["updates"]), (True, 1, 1))
        self.assertFalse(StaffMember.objects.filter(name="Hari").exists())
        upload(good, False)
        upload(good, False)  # same file again adds nobody twice
        self.assertEqual(StaffMember.objects.filter(name="Hari").count(), 1)
        self.ram.refresh_from_db()
        self.assertEqual((self.ram.monthly_salary_paisa, self.ram.position), (2_100_050, "Cashier"))
        bad = upload("name,branch,salary\nX,ZZZ,10\n,A,10\nY,A,-5\nZ,A,abc\n", True)
        self.assertFalse(bad["ok"])
        self.assertEqual([e["row"] for e in bad["errors"]], [2, 3, 4, 5])
        self.assertEqual(self.adm.get("/api/v1/import/template/staff").status_code, 200)
