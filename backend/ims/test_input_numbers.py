"""Bad numbers into every write endpoint: none may crash (500) or be accepted.

Each probe sends -5, 1.5, "2.5", 10**30, "abc", "", null, true, "1,000",
"1e3", 0, 10**12 and Arabic-Indic digits to one numeric field. A 500 always
fails; a 2xx fails unless that value is valid for the field (``ok_kinds``).
Other scripts' digits ("١٢", "१२") are accepted as numbers on purpose (Nepali
keyboards), and 10**12 is a large but legal amount.
"""
import json
import uuid

from django.test import TestCase, override_settings

from ims.testutils import World
from inventory.models import InventoryItem
from catalog.models import Supplier

BAD = {
    "neg": -5, "dec": 1.5, "dec_str": "2.5", "huge": 10 ** 30, "text": "abc", "empty": "", "null": None,
    "bool": True, "comma": "1,000", "exp": "1e3", "zero": 0, "big_ok": 10 ** 12, "unicode": "١٢",
}

ROWS = []


@override_settings(IMS_EMAIL_ASYNC=False)
class BadNumbers(TestCase):
    def setUp(self):
        self.w = World()
        self.mgr = self.w.client(self.w.manager_a, self.w.a)
        self.adm = self.w.client(self.w.admin, self.w.a)
        self.shift = self.w.open_shift(self.w.reg_a, self.w.cashier_a, 0)
        self.sale, _ = self.w.sell((self.w.p1, 3))
        self.supplier = Supplier.objects.create(org=self.w.org, name="S")

    def send(self, client, method, path, body):
        r = getattr(client, method)(f"/api/v1/{path}", json.dumps(body, allow_nan=True),
                                    content_type="application/json")
        return r.status_code, (r.json() if r.get("Content-Type", "").startswith("application/json") else r.content[:80])

    def probe(self, name, client, method, path, make_body, ok_kinds=()):
        from django.db import transaction

        class Undo(Exception):
            pass

        client.raise_request_exception = False
        for kind, v in BAD.items():
            res = {}
            try:
                with transaction.atomic():
                    res["r"] = self.send(client, method, path, make_body(v))
                    raise Undo
            except Undo:
                code, data = res["r"]
            except Exception as e:  # noqa: BLE001
                code, data = "EXC", repr(e)[:120]
            flag = ""
            if code == "EXC" or (isinstance(code, int) and code >= 500):
                flag = "CRASH"
            elif isinstance(code, int) and code < 300 and kind not in ok_kinds and kind not in ("big_ok", "unicode"):
                flag = "ACCEPTED"
            ROWS.append((name, kind, code, flag, str(data)[:110] if flag else ""))

    def test_no_crash_and_no_bad_value_accepted(self):
        p1 = self.w.p1.id
        line = self.sale.lines.get().id
        item = InventoryItem.objects.get(branch=self.w.a, product=self.w.p1).id
        from inventory.services import start_stocktake
        st = start_stocktake(self.w.a, self.w.manager_a)

        cart = lambda **kw: {"register_id": self.w.reg_a.id, "idempotency_key": str(uuid.uuid4()),  # noqa: E731
                             "tendered_paisa": 10 ** 7, "lines": [{"product_id": p1, "qty": 1}], **kw}
        P = self.probe
        P("product.sale_price", self.adm, "post", "products/", lambda v: {"sku": f"N{uuid.uuid4().hex[:6]}", "name": "x", "sale_price_paisa": v}, ok_kinds=("zero",))
        P("product.cost_price", self.adm, "post", "products/", lambda v: {"sku": f"N{uuid.uuid4().hex[:6]}", "name": "x", "sale_price_paisa": 100, "cost_price_paisa": v}, ok_kinds=("zero",))
        P("product.excise_rate", self.adm, "post", "products/", lambda v: {"sku": f"N{uuid.uuid4().hex[:6]}", "name": "x", "sale_price_paisa": 100, "excise_rate": v}, ok_kinds=("zero", "dec", "dec_str"))
        P("product.blocked_pct", self.adm, "post", "products/", lambda v: {"sku": f"N{uuid.uuid4().hex[:6]}", "name": "x", "sale_price_paisa": 100, "input_credit_blocked_pct": v}, ok_kinds=("zero",))
        P("product.pack_qty", self.adm, "post", "products/", lambda v: {"sku": f"N{uuid.uuid4().hex[:6]}", "name": "x", "sale_price_paisa": 100, "barcodes": [{"code": uuid.uuid4().hex[:10], "pack_qty": v}]})
        P("product.branch_price", self.adm, "post", f"products/{p1}/branch-price/", lambda v: {"sale_price_paisa": v}, ok_kinds=("null", "zero", "empty"))  # empty/null = remove the override
        P("shift.open", self.mgr, "post", "pos/shifts/open/", lambda v: {"register_id": self.w.reg_a.id, "opening_cash_paisa": v}, ok_kinds=("zero",))
        P("shift.close", self.mgr, "post", f"pos/shifts/{self.shift.id}/close/", lambda v: {"counted_cash_paisa": v}, ok_kinds=("zero",))
        P("checkout.qty", self.mgr, "post", "pos/checkout", lambda v: cart(lines=[{"product_id": p1, "qty": v}]))
        P("checkout.tendered", self.mgr, "post", "pos/checkout", lambda v: cart(tendered_paisa=v))
        P("checkout.line_disc_pct", self.mgr, "post", "pos/checkout", lambda v: cart(lines=[{"product_id": p1, "qty": 1, "discount_type": "percent", "discount_value": v if v != 10 ** 12 else 20000, "discount_reason": "r"}]), ok_kinds=("zero",))
        P("checkout.sale_disc_amt", self.mgr, "post", "pos/checkout", lambda v: cart(sale_discount_type="amount", sale_discount_value=v, sale_discount_reason="r"), ok_kinds=("zero",))
        P("checkout.product_id", self.mgr, "post", "pos/checkout", lambda v: cart(lines=[{"product_id": v, "qty": 1}]))
        P("checkout.register_id", self.mgr, "post", "pos/checkout", lambda v: cart(register_id=v))
        P("return.qty", self.mgr, "post", f"sales/{self.sale.id}/returns/", lambda v: {"register_id": self.w.reg_a.id, "reason": "r", "lines": [{"sale_line_id": line, "qty": v}]})
        P("return.line_id", self.mgr, "post", f"sales/{self.sale.id}/returns/", lambda v: {"register_id": self.w.reg_a.id, "reason": "r", "lines": [{"sale_line_id": v, "qty": 1}]})
        P("debit.taxable", self.mgr, "post", f"sales/{self.sale.id}/debit-note/", lambda v: {"taxable_paisa": v, "reason": "r", "tax_rate": "13"})
        P("debit.rate", self.mgr, "post", f"sales/{self.sale.id}/debit-note/", lambda v: {"taxable_paisa": 100, "reason": "r", "tax_rate": v}, ok_kinds=("zero", "dec", "dec_str", "exp"))
        P("inventory.reorder_point", self.mgr, "patch", f"inventory/{item}/", lambda v: {"reorder_point": v}, ok_kinds=("zero",))
        P("inventory.reorder_qty", self.mgr, "patch", f"inventory/{item}/", lambda v: {"reorder_qty": v}, ok_kinds=("zero",))
        P("adjust.qty", self.mgr, "post", "inventory/adjust/", lambda v: {"product": p1, "qty": v, "reason": "r"}, ok_kinds=("neg",))
        P("transfer.qty_sent", self.mgr, "post", "transfers/", lambda v: {"to_branch": self.w.b.id, "lines": [{"product": p1, "qty_sent": v}]})
        P("stocktake.counted", self.mgr, "post", f"stocktakes/{st.id}/lines/", lambda v: {"lines": [{"id": st.lines.first().id if st.lines.exists() else 1, "counted_qty": v, "reason": "r"}]}, ok_kinds=("zero", "null", "empty"))  # empty = not counted yet
        P("po.qty", self.mgr, "post", "purchase-orders/", lambda v: {"supplier": self.supplier.id, "lines": [{"product": p1, "qty_ordered": v, "unit_cost_paisa": 100}]})
        P("po.cost", self.mgr, "post", "purchase-orders/", lambda v: {"supplier": self.supplier.id, "lines": [{"product": p1, "qty_ordered": 1, "unit_cost_paisa": v}]}, ok_kinds=("zero",))
        P("purchase_return.qty", self.mgr, "post", "purchase-returns/", lambda v: {"supplier": self.supplier.id, "reason": "r", "lines": [{"product": p1, "qty": v}]})
        P("tax_rate.vat", self.adm, "post", "compliance/tax-categories/1/rates/", lambda v: {"effective_from": "2026-01-01", "vat_rate": v, "levy_rate": 0}, ok_kinds=("zero", "dec", "dec_str", "exp"))
        P("settings.abbrev_limit", self.adm, "patch", "compliance/settings", lambda v: {"abbreviated_invoice_limit_paisa": v}, ok_kinds=("zero",))
        screen = lambda qty: {"mode": "cart", "store": "S", "total": "1.00",  # noqa: E731
                              "lines": [{"name": "x", "qty": qty, "amount": "1.00"}]}
        P("display.qty", self.mgr, "post", "pos/display/state", lambda v: {"register_id": self.w.reg_a.id, "state": screen(v)})
        P("display.register_id", self.mgr, "post", "pos/display/state", lambda v: {"register_id": v, "state": screen(1)})
        P("customer.create", self.mgr, "post", "customers/", lambda v: {"name": "C", "phone": v})
        from staff.models import StaffMember

        worker = StaffMember.objects.create(org=self.w.org, branch=self.w.a, name="W")
        P("staff.salary", self.adm, "post", "staff/", lambda v: {"name": "N", "branch": self.w.a.id, "monthly_salary_paisa": v}, ok_kinds=("zero",))
        P("staff.branch", self.adm, "post", "staff/", lambda v: {"name": "N", "branch": v})
        P("staff_payment.amount", self.adm, "post", "staff-payments/", lambda v: {"staff": worker.id, "kind": "salary", "method": "cash", "paid_on": "2026-11-23", "amount_paisa": v})
        P("staff_payment.staff", self.adm, "post", "staff-payments/", lambda v: {"staff": v, "kind": "salary", "method": "cash", "paid_on": "2026-11-23", "amount_paisa": 100})

        problems = [f"{name} <- {kind}: {code} {flag} {data}" for name, kind, code, flag, data in ROWS if flag]
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertGreater(len(ROWS), 300)
