import uuid
from decimal import Decimal

from django.db import transaction
from rest_framework.test import APIClient

from accounts.models import User, UserBranchRole
from accounts.roles import BRANCH_MANAGER, CASHIER, INVENTORY_CLERK, ORG_ADMIN
from catalog.models import Barcode, Product
from inventory.models import InventoryItem, StockMovement
from inventory.services import Receipt, receive_stock
from sales import services
from tenancy.models import Branch, Organization, Register

PASSWORD = "test-pass-123"


class World:
    """Two branches, a register each, users per role, three products in stock."""

    def __init__(self):
        self.org = Organization.objects.create(name="Org", code="org")
        self.a = Branch.objects.create(org=self.org, name="A", code="A")
        self.b = Branch.objects.create(org=self.org, name="B", code="B")
        self.reg_a = Register.objects.create(branch=self.a, code="R1", name="Till 1")
        self.reg_b = Register.objects.create(branch=self.b, code="R1", name="Till 1")
        self.admin = self.user("admin", [(None, ORG_ADMIN)])
        self.manager_a = self.user("manager_a", [(self.a, BRANCH_MANAGER)])
        self.cashier_a = self.user("cashier_a", [(self.a, CASHIER)])
        self.cashier_b = self.user("cashier_b", [(self.b, CASHIER)])
        self.clerk_a = self.user("clerk_a", [(self.a, INVENTORY_CLERK)])
        self.p1 = self.product("P1", 10000, Decimal("13"), False, cost=7000, barcode="111")
        self.p2 = self.product("P2", 5650, Decimal("13"), True, cost=4000, barcode="222")
        self.p3 = self.product("P3", 2000, Decimal("0"), True, cost=1500, barcode="333")
        with transaction.atomic():
            for branch in (self.a, self.b):
                receive_stock(branch, [Receipt(p.id, 10, p.cost_price_paisa) for p in (self.p1, self.p2, self.p3)],
                              StockMovement.PURCHASE_IN, self.admin, ref_type="opening_stock")

    def user(self, username, roles):
        u = User.objects.create(username=username)
        u.set_password(PASSWORD)
        u.save()
        for branch, role in roles:
            UserBranchRole.objects.create(user=u, org=self.org, branch=branch, role=role)
        return u

    def product(self, sku, price, rate, inclusive, cost=0, barcode=None):
        p = Product.objects.create(org=self.org, sku=sku, name=f"Product {sku}", sale_price_paisa=price, cost_price_paisa=cost,
                                   tax_rate=rate, tax_inclusive=inclusive)
        if barcode:
            Barcode.objects.create(product=p, code=barcode)
        return p

    def on_hand(self, branch, product):
        return InventoryItem.objects.get(branch=branch, product=product).quantity_on_hand

    def item(self, branch, product):
        return InventoryItem.objects.get(branch=branch, product=product)

    def client(self, user, branch=None):
        c = APIClient()
        c.force_authenticate(user)
        if branch is not None:
            c.credentials(HTTP_X_BRANCH_ID=str(branch.id))
        # TenantMiddleware reads the Django user, so log in for real too.
        c.login(username=user.username, password=PASSWORD)
        return c

    def open_shift(self, register, user, cash=0):
        return services.open_shift(register.branch, register, user, cash)

    def cart(self, *lines, **kw):
        return services.Cart(lines=[services.CartLine(p.id, q) for p, q in lines], **kw)

    def sell(self, *lines, branch=None, register=None, cashier=None, tendered=10 ** 8, key=None, **kw):
        return services.checkout_cash(
            branch=branch or self.a, register=register or self.reg_a, cashier=cashier or self.cashier_a,
            cart=self.cart(*lines, **kw), tendered_paisa=tendered, idempotency_key=key or str(uuid.uuid4()),
        )
