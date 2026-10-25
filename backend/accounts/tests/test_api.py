"""API tests: happy path, permission-denied and tenant-denied paths (§24)."""
import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import UserBranchRole
from accounts.roles import CASHIER
from ims.testutils import PASSWORD, World
from inventory.models import StockTransfer, StockTransferLine
from sales.models import Sale


class AuthTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_login_sets_session_and_returns_roles(self):
        c = APIClient(enforce_csrf_checks=True)
        c.get("/api/v1/auth/csrf")
        token = c.cookies["csrftoken"].value
        r = c.post("/api/v1/auth/login", {"username": "cashier_a", "password": PASSWORD}, format="json",
                   HTTP_X_CSRFTOKEN=token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["branches"][0]["role"], "Cashier")
        self.assertIn("pos.sell", r.data["branches"][0]["permissions"])
        self.assertEqual(r.data["active_branch_id"], self.w.a.id)  # single branch auto-selected

    def test_roles_map_matches_roles_py(self):
        c = APIClient()
        c.login(username="cashier_a", password=PASSWORD)
        r = c.get("/api/v1/auth/roles")
        self.assertEqual(r.status_code, 200)
        by_role = {x["role"]: x for x in r.data}
        self.assertEqual(by_role["Cashier"]["scope"], "branch")
        self.assertEqual(by_role["OrgAdmin"]["scope"], "org")
        self.assertIn("pos.sell", by_role["Cashier"]["permissions"])
        self.assertNotIn("users.manage", by_role["Cashier"]["permissions"])

    def test_roles_requires_login(self):
        self.assertIn(APIClient().get("/api/v1/auth/roles").status_code, (401, 403))

    def test_bad_password(self):
        r = APIClient().post("/api/v1/auth/login", {"username": "cashier_a", "password": "x"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_csrf_enforced_on_session_posts(self):
        c = APIClient(enforce_csrf_checks=True)
        c.login(username="cashier_a", password=PASSWORD)
        r = c.post("/api/v1/auth/logout")
        self.assertEqual(r.status_code, 403)

    def test_deactivation_revokes_existing_session(self):
        c = self.w.client(self.w.cashier_a, self.w.a)
        self.assertEqual(c.get("/api/v1/auth/me").status_code, 200)
        self.w.cashier_a.is_active = False
        self.w.cashier_a.save()
        c.force_authenticate(None)
        self.assertEqual(c.get("/api/v1/auth/me").status_code, 403)


class TenancyTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.sale_a, _ = self.w.sell((self.w.p1, 1))

    def test_cannot_select_branch_without_role(self):
        c = self.w.client(self.w.cashier_a, self.w.b)  # header points at B
        self.assertIsNone(c.get("/api/v1/auth/me").data["active_branch_id"])
        self.assertEqual(c.get("/api/v1/inventory/").status_code, 403)

    def test_sales_do_not_leak_across_branches(self):
        c = self.w.client(self.w.cashier_b, self.w.b)
        self.assertEqual(c.get(f"/api/v1/sales/{self.sale_a.id}/").status_code, 404)
        self.assertEqual(c.get("/api/v1/sales/").data["results"], [])

    def test_cannot_checkout_on_another_branchs_register(self):
        c = self.w.client(self.w.cashier_b, self.w.b)
        r = c.post("/api/v1/pos/checkout", {
            "register_id": self.w.reg_a.id, "idempotency_key": str(uuid.uuid4()), "tendered_paisa": 10 ** 6,
            "lines": [{"product_id": self.w.p1.id, "qty": 1}],
        }, format="json")
        self.assertEqual(r.status_code, 403)

    def test_role_is_per_branch(self):
        UserBranchRole.objects.create(user=self.w.manager_a, branch=self.w.b, role=CASHIER)
        self.assertTrue(self.w.manager_a.has_branch_perm("inventory.adjust", self.w.a))
        self.assertFalse(self.w.manager_a.has_branch_perm("inventory.adjust", self.w.b))
        self.assertTrue(self.w.manager_a.has_branch_perm("pos.sell", self.w.b))

    def test_org_role_applies_everywhere(self):
        for b in (self.w.a, self.w.b):
            self.assertTrue(self.w.admin.has_branch_perm("inventory.adjust", b))
        c = self.w.client(self.w.admin, self.w.b)
        self.assertEqual(c.get("/api/v1/inventory/").status_code, 200)

    def test_transfer_visible_to_both_branches_only(self):
        t = StockTransfer.objects.create(from_branch=self.w.a, to_branch=self.w.b, by=self.w.manager_a)
        StockTransferLine.objects.create(transfer=t, product=self.w.p1, qty_sent=1)
        third = self.w.org.branches.create(name="C", code="C")
        clerk_c = self.w.user("clerk_c", [(third, "InventoryClerk")])
        self.assertEqual(self.w.client(self.w.manager_a, self.w.a).get(f"/api/v1/transfers/{t.id}/").status_code, 200)
        self.assertEqual(self.w.client(self.w.admin, self.w.b).get(f"/api/v1/transfers/{t.id}/").status_code, 200)
        self.assertEqual(self.w.client(clerk_c, third).get(f"/api/v1/transfers/{t.id}/").status_code, 404)


class RbacTests(TestCase):
    def setUp(self):
        self.w = World()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)

    def test_cashier_cannot_adjust_stock_or_view_dashboard(self):
        c = self.w.client(self.w.cashier_a, self.w.a)
        r = c.post("/api/v1/inventory/adjust/", {"product": self.w.p1.id, "qty": 5, "reason": "x"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(c.get("/api/v1/reports/dashboard").status_code, 403)
        self.assertEqual(c.get("/api/v1/users/").status_code, 403)

    def test_cashier_does_not_see_cost(self):
        c = self.w.client(self.w.cashier_a, self.w.a)
        r = c.get(f"/api/v1/products/{self.w.p1.id}/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("cost_price_paisa", r.data)
        offline = c.get("/api/v1/pos/catalog/").data["products"]
        self.assertTrue(all("cost" not in k for p in offline for k in p))

    def test_product_carries_branch_stock_level(self):
        item = self.w.item(self.w.a, self.w.p1)
        item.reorder_point = 12
        item.save()
        r = self.w.client(self.w.clerk_a, self.w.a).get(f"/api/v1/products/{self.w.p1.id}/")
        self.assertEqual((r.data["on_hand"], r.data["reorder_point"]), (10, 12))

    def test_clerk_can_adjust_but_not_sell(self):
        c = self.w.client(self.w.clerk_a, self.w.a)
        r = c.post("/api/v1/inventory/adjust/", {"product": self.w.p1.id, "qty": 5, "reason": "found"}, format="json")
        self.assertEqual(r.status_code, 200)
        r = c.post("/api/v1/pos/checkout", {"register_id": self.w.reg_a.id, "idempotency_key": "k",
                                            "tendered_paisa": 1, "lines": [{"product_id": self.w.p1.id, "qty": 1}]},
                   format="json")
        self.assertEqual(r.status_code, 403)

    def test_cashier_cannot_void(self):
        sale, _ = self.w.sell((self.w.p1, 1))
        r = self.w.client(self.w.cashier_a, self.w.a).post(f"/api/v1/sales/{sale.id}/void/", {"reason": "x"})
        self.assertEqual(r.status_code, 403)
        r = self.w.client(self.w.manager_a, self.w.a).post(f"/api/v1/sales/{sale.id}/void/", {"reason": "x"})
        self.assertEqual(r.status_code, 200)

    def test_org_admin_dashboard(self):
        self.w.sell((self.w.p1, 1))
        r = self.w.client(self.w.admin).get("/api/v1/reports/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["totals"]["today"], Sale.objects.get().total_paisa)


class PosApiTests(TestCase):
    def setUp(self):
        self.w = World()
        self.c = self.w.client(self.w.cashier_a, self.w.a)

    def test_full_cash_flow_over_http(self):
        r = self.c.post("/api/v1/pos/shifts/open/", {"register_id": self.w.reg_a.id, "opening_cash_paisa": 50000},
                        format="json")
        self.assertEqual(r.status_code, 201, r.data)
        shift_id = r.data["id"]
        body = {"register_id": self.w.reg_a.id, "idempotency_key": str(uuid.uuid4()), "tendered_paisa": 20000,
                "lines": [{"product_id": self.w.p1.id, "qty": 1}]}
        r = self.c.post("/api/v1/pos/checkout", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["payments"][0]["change_paisa"], 20000 - 11300)
        r2 = self.c.post("/api/v1/pos/checkout", body, format="json")  # retry
        self.assertEqual((r2.status_code, r2.data["id"]), (200, r.data["id"]))
        receipt = self.c.get(f"/api/v1/sales/{r.data['id']}/receipt/").data
        self.assertTrue(receipt["open_drawer"])
        r = self.c.post(f"/api/v1/pos/shifts/{shift_id}/close/", {"counted_cash_paisa": 61300}, format="json")
        self.assertEqual(r.data["variance_paisa"], 0)

    def test_stock_error_reports_short_lines(self):
        self.c.post("/api/v1/pos/shifts/open/", {"register_id": self.w.reg_a.id, "opening_cash_paisa": 0})
        r = self.c.post("/api/v1/pos/checkout", {
            "register_id": self.w.reg_a.id, "idempotency_key": "x1", "tendered_paisa": 10 ** 8,
            "lines": [{"product_id": self.w.p1.id, "qty": 50}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["short_lines"][0]["sku"], "P1")

    def test_fonepay_over_http_with_mock(self):
        self.c.post("/api/v1/pos/shifts/open/", {"register_id": self.w.reg_a.id, "opening_cash_paisa": 0})
        r = self.c.post("/api/v1/pos/checkout/initiate", {
            "register_id": self.w.reg_a.id, "idempotency_key": "f1",
            "lines": [{"product_id": self.w.p2.id, "qty": 1}]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(r.data["qr_payload"])
        sale_id, prn = r.data["id"], r.data["prn"]
        self.assertEqual(self.c.get(f"/api/v1/pos/sale/{sale_id}/payment-status").data["status"], "open")
        self.c.post(f"/api/v1/pos/fonepay/mock-pay/{prn}", {})
        st = self.c.get(f"/api/v1/pos/sale/{sale_id}/payment-status").data
        self.assertEqual(st["status"], "paid")
        self.assertTrue(st["receipt_number"])
