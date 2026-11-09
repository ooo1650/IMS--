"""One backend, several separately registered businesses (e.g. a shoe shop and
a clothing shop with one owner). Nothing of one business may be visible to,
or changeable by, the other."""
import uuid
from decimal import Decimal

from django.test import TestCase

from accounts.models import User, UserBranchRole
from accounts.roles import CASHIER, ORG_ADMIN
from catalog.models import Barcode, Product
from compliance import cbms, documents
from compliance.models import MockCbmsRecord, get_settings
from ims.testutils import PASSWORD, World
from inventory.models import StockMovement
from inventory.services import Receipt, receive_stock
from sales import services
from sales.models import Customer
from tenancy.models import Branch, Organization, Register


class TwoBusinesses(TestCase):
    def setUp(self):
        self.w = World()  # business A ("Org"): branches A and B, products P1-P3
        self.a = self.w.org
        self.a.pan = "301111111"
        self.a.save()
        self.b = Organization.objects.create(name="Clothing Co", code="clothing", pan="302222222")
        self.bb = Branch.objects.create(org=self.b, name="Cloth KTM", code="CKTM")
        self.breg = Register.objects.create(branch=self.bb, code="R1", name="Till 1")
        self.admin_b = self.w.user("admin_b", [])
        UserBranchRole.objects.create(user=self.admin_b, org=self.b, role=ORG_ADMIN)
        self.cashier_b = self.w.user("cashier_b2", [])
        UserBranchRole.objects.create(user=self.cashier_b, org=self.b, branch=self.bb, role=CASHIER)
        # Same SKU and barcode as business A's P1: allowed in another business.
        self.shirt = Product.objects.create(org=self.b, sku="P1", name="Shirt", sale_price_paisa=150000,
                                            cost_price_paisa=90000, tax_rate=Decimal("13"), tax_inclusive=True)
        Barcode.objects.create(product=self.shirt, code="111")
        receive_stock(self.bb, [Receipt(self.shirt.id, 5, 90000)], StockMovement.PURCHASE_IN, self.admin_b,
                      ref_type="opening_stock")

    def api(self, user, branch=None):
        return self.w.client(user, branch)

    # --- catalog ---------------------------------------------------------------
    def test_each_business_sees_only_its_catalog(self):
        a_names = {p["name"] for p in self.api(self.w.admin).get("/api/v1/products/").data["results"]}
        b_names = {p["name"] for p in self.api(self.admin_b).get("/api/v1/products/").data["results"]}
        self.assertNotIn("Shirt", a_names)
        self.assertEqual(b_names, {"Shirt"})

    def test_sku_and_barcode_unique_per_business(self):
        c = self.api(self.admin_b)
        r = c.post("/api/v1/products/", {"sku": "P1", "name": "Dup", "sale_price_paisa": 100}, format="json")
        self.assertEqual(r.status_code, 400)  # same business: refused
        r = c.post("/api/v1/products/", {"sku": "P2", "name": "Jeans", "sale_price_paisa": 100,
                                         "barcodes": [{"code": "222"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.data)  # P2 / 222 exist only in business A
        self.assertEqual(Product.objects.get(pk=r.data["id"]).org, self.b)

    def test_cannot_link_another_business_category(self):
        from catalog.models import Category

        a_cat = Category.objects.create(org=self.a, name="Grocery")
        r = self.api(self.admin_b).post("/api/v1/products/", {"sku": "X", "name": "X", "sale_price_paisa": 1,
                                                                  "category": a_cat.id}, format="json")
        self.assertEqual(r.status_code, 400)

    # --- selling and stock -----------------------------------------------------------
    def test_cashier_cannot_sell_another_business_product(self):
        self.w.open_shift(self.breg, self.cashier_b)
        c = self.api(self.cashier_b, self.bb)
        r = c.post("/api/v1/pos/checkout", {"register_id": self.breg.id, "idempotency_key": str(uuid.uuid4()),
                                            "tendered_paisa": 10 ** 8,
                                            "lines": [{"product_id": self.w.p2.id, "qty": 1}]}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("no longer in this business's catalog", str(r.data))

    def test_pos_catalog_and_customers_are_per_business(self):
        Customer.objects.create(org=self.a, name="Ram", phone="1")
        Customer.objects.create(org=self.b, name="Sita", phone="2")
        c = self.api(self.cashier_b, self.bb)
        ids = [p["id"] for p in c.get("/api/v1/pos/catalog/").data["products"]]
        self.assertEqual(ids, [self.shirt.id])
        names = [x["name"] for x in c.get("/api/v1/customers/").data["results"]]
        self.assertEqual(names, ["Sita"])

    def test_no_transfers_between_businesses(self):
        c = self.api(self.w.manager_a, self.w.a)
        r = c.post("/api/v1/transfers/", {"to_branch": self.bb.id, "lines": []}, format="json")
        self.assertEqual(r.status_code, 400)

    # --- tax documents, numbering, CBMS ---------------------------------------------------
    def test_invoice_prints_its_own_business_pan_and_numbers_independently(self):
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.w.open_shift(self.breg, self.cashier_b)
        sale_a, _ = self.w.sell((self.w.p1, 1))
        sale_b, _ = services.checkout_cash(branch=self.bb, register=self.breg, cashier=self.cashier_b,
                                           cart=self.w.cart((self.shirt, 1)), tendered_paisa=10 ** 8,
                                           idempotency_key=str(uuid.uuid4()))
        self.assertEqual(documents.invoice_context(sale_a, None)["seller"]["pan"], "301111111")
        self.assertEqual(documents.invoice_context(sale_b, None)["seller"]["pan"], "302222222")
        self.assertTrue(sale_b.receipt_number.startswith("CKTM-"))
        self.assertEqual(sale_b.receipt_seq, 1)

    def test_cbms_posts_each_business_with_its_own_login(self):
        for org, user in ((self.a, "A_USER"), (self.b, "B_USER")):
            cfg = get_settings(org)
            cfg.cbms_enabled, cfg.cbms_base_url, cfg.cbms_username = True, "mock://", user
            cfg.cbms_password = "pw"
            cfg.save()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.w.open_shift(self.breg, self.cashier_b)
        sale_a, _ = self.w.sell((self.w.p1, 1))
        sale_b, _ = services.checkout_cash(branch=self.bb, register=self.breg, cashier=self.cashier_b,
                                           cart=self.w.cart((self.shirt, 1)), tendered_paisa=10 ** 8,
                                           idempotency_key=str(uuid.uuid4()))
        cbms.sync_pending(force=True)
        rec = {r.document_number: r.payload for r in MockCbmsRecord.objects.all()}
        self.assertEqual((rec[sale_a.receipt_number]["username"], rec[sale_a.receipt_number]["seller_pan"]),
                         ("A_USER", "301111111"))
        self.assertEqual((rec[sale_b.receipt_number]["username"], rec[sale_b.receipt_number]["seller_pan"]),
                         ("B_USER", "302222222"))

    def test_bad_cbms_login_pauses_only_that_business(self):
        for org, pw in ((self.a, "wrong"), (self.b, "pw")):
            cfg = get_settings(org)
            cfg.cbms_enabled, cfg.cbms_base_url, cfg.cbms_username = True, "mock://", "u"
            cfg.cbms_password = pw
            cfg.save()
        self.w.open_shift(self.w.reg_a, self.w.cashier_a)
        self.w.open_shift(self.breg, self.cashier_b)
        self.w.sell((self.w.p1, 1))
        sale_b, _ = services.checkout_cash(branch=self.bb, register=self.breg, cashier=self.cashier_b,
                                           cart=self.w.cart((self.shirt, 1)), tendered_paisa=10 ** 8,
                                           idempotency_key=str(uuid.uuid4()))
        cbms.sync_pending()
        self.assertTrue(get_settings(self.a).cbms_paused_reason)
        self.assertFalse(get_settings(self.b).cbms_paused_reason)
        self.assertTrue(MockCbmsRecord.objects.filter(document_number=sale_b.receipt_number).exists())

    # --- reports and admin ------------------------------------------------------------
    def test_dashboard_and_reports_cover_only_the_business(self):
        d = self.api(self.admin_b).get("/api/v1/reports/dashboard").data
        self.assertEqual([b["code"] for b in d["branches"]], ["CKTM"])
        r = self.api(self.admin_b).get("/api/v1/compliance/reports/sales-book")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(row.get("branch") in ("", "CKTM", None) for row in r.data["rows"]))

    def test_admin_cannot_touch_another_business(self):
        c = self.api(self.w.admin)  # admin of business A
        self.assertEqual(c.patch(f"/api/v1/branches/{self.bb.id}/", {"name": "Hacked"}, format="json").status_code, 404)
        r = c.post("/api/v1/branches/", {"name": "New", "code": "NEW", "org": self.b.id}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Branch.objects.get(code="NEW").org, self.a)  # org is forced to the active business
        names = {u["username"] for u in c.get("/api/v1/users/").data}
        self.assertNotIn("admin_b", names)
        self.assertNotIn("cashier_b2", names)
        r = c.post("/api/v1/users/", {"username": "x", "password": "a-long-pass-123",
                                      "roles": [{"branch": self.bb.id, "role": CASHIER}]}, format="json")
        self.assertEqual(r.status_code, 400)  # can't hire into the other business

    def test_owner_in_two_businesses_switches_and_identity_is_locked(self):
        owner = self.w.user("owner", [])
        UserBranchRole.objects.create(user=owner, org=self.a, role=ORG_ADMIN)
        UserBranchRole.objects.create(user=owner, org=self.b, role=ORG_ADMIN)
        c = self.api(owner)
        me = c.get("/api/v1/auth/me").data
        self.assertEqual({b["name"] for b in me["businesses"]}, {"Org", "Clothing Co"})
        me = c.post("/api/v1/auth/active-org", {"org_id": self.b.id}, format="json").data
        self.assertEqual(me["active_org_name"], "Clothing Co")
        self.assertEqual([b["code"] for b in me["branches"]], ["CKTM"])
        self.assertEqual({p["name"] for p in c.get("/api/v1/products/").data["results"]}, {"Shirt"})
        # Business A's admin may change the owner's roles in A but not their password.
        r = self.api(self.w.admin).patch(f"/api/v1/users/{owner.id}/", {"password": "another-pass-123"},
                                            format="json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(User.objects.get(pk=owner.pk).check_password(PASSWORD))

    def test_switching_to_a_business_you_are_not_in_is_refused(self):
        r = self.api(self.w.admin).post("/api/v1/auth/active-org", {"org_id": self.b.id}, format="json")
        self.assertEqual(r.status_code, 403)
