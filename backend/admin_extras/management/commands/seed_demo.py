"""Demo data for local development. Two separately registered businesses with
one owner, to show that each has its own catalog, invoices and reports:

* Himalaya Mart (groceries): 3 branches, users for each role, a catalog with
  opening stock and a few days of sales for the dashboard;
* Himalaya Footwear (shoes): 1 branch, its own small catalog.

``admin`` is the owner (org admin of both); ``operator`` is the platform admin
for the operator console."""
import random
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import User, UserBranchRole
from accounts.roles import BRANCH_MANAGER, CASHIER, INVENTORY_CLERK, ORG_ADMIN, VIEWER
from catalog.models import Barcode, Category, Product, Supplier
from inventory.models import InventoryItem, StockMovement
from inventory.services import Receipt, receive_stock
from sales import services
from compliance.models import TaxCategory, get_settings
from tenancy.models import Branch, Organization, Register

PASSWORD = "demo-pass-123"
HS = {"Grocery": "1006", "Beverages": "2202", "Snacks": "1905", "Personal care": "3401", "Household": "3402",
      "Fresh": "0401"}

CATALOG = [
    # sku, name, category, price NPR, cost NPR, tax, inclusive, barcode
    ("RICE-5KG", "Basmati Rice 5kg", "Grocery", "850.00", "700.00", "13", True, "8901000000011"),
    ("DAL-1KG", "Masoor Dal 1kg", "Grocery", "195.00", "160.00", "13", True, "8901000000028"),
    ("OIL-1L", "Sunflower Oil 1L", "Grocery", "310.00", "265.00", "13", True, "8901000000035"),
    ("SUGAR-1KG", "Sugar 1kg", "Grocery", "120.00", "98.00", "13", True, "8901000000042"),
    ("SALT-1KG", "Iodized Salt 1kg", "Grocery", "30.00", "22.00", "0", True, "8901000000059"),
    ("TEA-500", "Ilam Tea 500g", "Beverages", "450.00", "360.00", "13", True, "8901000000066"),
    ("COFFEE-200", "Instant Coffee 200g", "Beverages", "725.00", "590.00", "13", True, "8901000000073"),
    ("JUICE-1L", "Mango Juice 1L", "Beverages", "180.00", "140.00", "13", True, "8901000000080"),
    ("WATER-1L", "Mineral Water 1L", "Beverages", "25.00", "15.00", "13", True, "8901000000097"),
    ("NOODLE-70", "Instant Noodles 70g", "Snacks", "25.00", "19.00", "13", True, "8901000000103"),
    ("BISCUIT", "Digestive Biscuits", "Snacks", "90.00", "70.00", "13", True, "8901000000110"),
    ("CHIPS", "Potato Chips 50g", "Snacks", "50.00", "36.00", "13", True, "8901000000127"),
    ("SOAP", "Bath Soap 100g", "Personal care", "65.00", "48.00", "13", False, "8901000000134"),
    ("SHAMPOO", "Shampoo 180ml", "Personal care", "295.00", "230.00", "13", False, "8901000000141"),
    ("PASTE", "Toothpaste 150g", "Personal care", "155.00", "118.00", "13", False, "8901000000158"),
    ("DETERG-1KG", "Detergent Powder 1kg", "Household", "210.00", "165.00", "13", False, "8901000000165"),
    ("BULB-9W", "LED Bulb 9W", "Household", "175.00", "120.00", "13", False, "8901000000172"),
    ("EGGS-12", "Eggs (tray of 12)", "Fresh", "210.00", "180.00", "0", True, "8901000000189"),
    ("MILK-1L", "Fresh Milk 1L", "Fresh", "110.00", "95.00", "0", True, "8901000000196"),
    ("BREAD", "Sliced Bread", "Fresh", "85.00", "65.00", "13", True, "8901000000202"),
]


FOOTWEAR = [
    # sku, name, price NPR, cost NPR, barcode (SKU "SOCKS" exists in neither grocery list: no clash)
    ("SNEAKER-42", "Running Sneaker size 42", "4500.00", "3100.00", "8902000000019"),
    ("SANDAL-40", "Leather Sandal size 40", "2200.00", "1450.00", "8902000000026"),
    ("BOOT-41", "Trekking Boot size 41", "7800.00", "5600.00", "8902000000033"),
    ("SOCKS", "Cotton Socks (3 pairs)", "450.00", "280.00", "8902000000040"),
]


def paisa(npr):
    return int(Decimal(npr) * 100)


class Command(BaseCommand):
    help = "Seed demo branches, users, catalog, stock and sales. Refuses to run twice."

    def add_arguments(self, parser):
        parser.add_argument("--admin-email", default="",
                            help="Real address for the admin account, so its sign-in codes arrive by email "
                                 "(default: admin@demo.ims.test, which only prints to the log)")

    def handle(self, *args, **opts):
        if Organization.objects.exists():
            raise CommandError("Data already exists; run on an empty database (flush first).")
        admin_email = opts["admin_email"].strip().lower()
        if admin_email:
            try:
                validate_email(admin_email)
            except ValidationError:
                raise CommandError(f"Not a valid email address: {admin_email}")
        with transaction.atomic():
            self.seed(admin_email)
        self.stdout.write(self.style.SUCCESS(f"Seeded. Every demo user's password is: {PASSWORD}"))
        for u in User.objects.order_by("username"):
            roles = ", ".join(f"{r.role}@{r.branch.code if r.branch else r.org.code}" for r in u.branch_roles.all())
            roles = roles or ("platform admin" if u.is_platform_admin else "")
            self.stdout.write(f"  {u.username:<14} {roles}")

    def seed(self, admin_email=""):
        org = Organization.objects.create(
            name="Himalaya Mart Pvt. Ltd.", name_np="हिमालय मार्ट प्रा. लि.", pan="999999999",
            address="New Road, Kathmandu", phone="01-4200000", code="himalaya-mart", trade="Groceries")
        cfg = get_settings(org)
        # Demo: per-branch series and the in-process CBMS double (IRD test login).
        cfg.cbms_enabled, cfg.cbms_base_url, cfg.cbms_username = True, "mock://", "Test_CBMS"
        cfg.cbms_password = "test@321"
        cfg.save()
        branches = [
            Branch.objects.create(org=org, name="Kathmandu — New Road", code="KTM", address="New Road, Kathmandu"),
            Branch.objects.create(org=org, name="Pokhara — Lakeside", code="PKR", address="Lakeside, Pokhara"),
            Branch.objects.create(org=org, name="Biratnagar — Main Rd", code="BRT", address="Main Road, Biratnagar"),
        ]
        registers = {b.code: [Register.objects.create(branch=b, code=f"R{i}", name=f"Till {i}") for i in (1, 2)]
                     for b in branches}
        ktm, pkr, brt = branches

        def user(username, first, roles, superuser=False, email=""):
            # .test is a reserved domain; in dev the sign-in codes print to the runserver console.
            u = User.objects.create(username=username, first_name=first, email=email or f"{username}@demo.ims.test",
                                    is_staff=superuser, is_superuser=superuser)
            u.set_password(PASSWORD)
            u.save()
            for branch, role in roles:
                UserBranchRole.objects.create(user=u, org=org, branch=branch, role=role)
            return u

        admin = user("admin", "Org Admin", [(None, ORG_ADMIN)], superuser=True, email=admin_email)
        user("manager", "Sita Manager", [(ktm, BRANCH_MANAGER), (pkr, CASHIER)])
        cashier = user("cashier", "Ram Cashier", [(ktm, CASHIER)])
        user("cashier_pkr", "Hari Cashier", [(pkr, CASHIER)])
        user("clerk", "Gita Clerk", [(ktm, INVENTORY_CLERK)])
        user("viewer", "Auditor", [(None, VIEWER)])
        user("manager_brt", "Bikash Manager", [(brt, BRANCH_MANAGER)])

        suppliers = [Supplier.objects.create(org=org, name=n, phone=p, pan=pan) for n, p, pan in
                     [("Everest Traders", "01-4220000", "301234567"),
                      ("Sagarmatha Distributors", "061-520000", "302345678")]]
        # flush also removes the migration-seeded tax categories; recreate them.
        import importlib

        from django.apps import apps as django_apps

        importlib.import_module("compliance.migrations.0002_seed_tax_categories").seed(django_apps, None)
        tax_cats = {c.code: c for c in TaxCategory.objects.all()}
        cats = {}
        products = []
        for sku, name, cat, price, cost, tax, incl, code in CATALOG:
            cats.setdefault(cat, Category.objects.get_or_create(org=org, name=cat)[0])
            p = Product.objects.create(
                org=org, sku=sku, name=name, category=cats[cat], supplier=random.choice(suppliers),
                sale_price_paisa=paisa(price), cost_price_paisa=paisa(cost), tax_rate=Decimal(tax),
                tax_inclusive=incl, tax_category=tax_cats["VAT13" if tax == "13" else "EXEMPT"],
                hs_code=HS.get(cat, ""),
            )
            Barcode.objects.create(product=p, code=code)
            products.append(p)
        # A case barcode that rings up 12 units.
        Barcode.objects.create(product=products[9], code="8901000009999", pack_qty=12, type="code128")

        rng = random.Random(42)
        for b in branches:
            receive_stock(b, [Receipt(p.id, rng.randint(20, 120), p.cost_price_paisa) for p in products],
                          StockMovement.PURCHASE_IN, admin, ref_type="opening_stock", reason="demo opening stock")
            items = InventoryItem.objects.filter(branch=b)
            for it in items:
                it.reorder_point, it.reorder_qty = 15, 50
            InventoryItem.objects.bulk_update(items, ["reorder_point", "reorder_qty"])
        InventoryItem.objects.filter(product__sku="BREAD").update(allow_negative=True)

        # A few days of sales so the dashboard has a trend.
        for b in branches:
            reg = registers[b.code][0]
            shift = services.open_shift(b, reg, admin, 500000)
            for days_ago in range(6, -1, -1):
                # Invoice numbers must be chronological: issue each day's sales in time order.
                day_start = timezone.now() - timedelta(days=days_ago, hours=10)
                for minutes in sorted(rng.sample(range(0, 600), rng.randint(4, 10))):
                    picks = rng.sample(products, rng.randint(1, 4))
                    cart = services.Cart(lines=[services.CartLine(p.id, rng.randint(1, 3)) for p in picks])
                    when = min(day_start + timedelta(minutes=minutes), timezone.now())
                    try:
                        services.checkout_cash(
                            branch=b, register=reg, cashier=cashier if b == ktm else admin, cart=cart,
                            tendered_paisa=10 ** 7, idempotency_key=str(uuid.uuid4()), now=when,
                        )
                    except services.StockUnavailable:
                        continue
            services.close_shift(shift, admin, services.expected_cash(shift))

        self.seed_footwear(admin, tax_cats)
        # Platform admin for the operator console (always needs an emailed code).
        op = User.objects.create(username="operator", first_name="Operator", email="operator@demo.ims.test",
                                 is_platform_admin=True)
        op.set_password(PASSWORD)
        op.save()

    def seed_footwear(self, owner, tax_cats):
        """A second, separately registered business run by the same owner."""
        org = Organization.objects.create(
            name="Himalaya Footwear Pvt. Ltd.", name_np="हिमालय फुटवेयर प्रा. लि.", pan="999999998",
            address="Durbar Marg, Kathmandu", phone="01-4211111", code="himalaya-footwear", trade="Footwear")
        branch = Branch.objects.create(org=org, name="Durbar Marg", code="SHO", address="Durbar Marg, Kathmandu")
        Register.objects.create(branch=branch, code="R1", name="Till 1")
        UserBranchRole.objects.create(user=owner, org=org, role=ORG_ADMIN)
        manager = User.objects.create(username="manager_shoes", first_name="Anita Manager",
                                      email="manager_shoes@demo.ims.test")
        manager.set_password(PASSWORD)
        manager.save()
        UserBranchRole.objects.create(user=manager, org=org, branch=branch, role=BRANCH_MANAGER)
        supplier = Supplier.objects.create(org=org, name="Bhaktapur Shoe Works", pan="303456789")
        cat = Category.objects.create(org=org, name="Shoes")
        products = []
        for sku, name, price, cost, code in FOOTWEAR:
            p = Product.objects.create(org=org, sku=sku, name=name, category=cat, supplier=supplier,
                                       sale_price_paisa=paisa(price), cost_price_paisa=paisa(cost),
                                       tax_rate=Decimal("13"), tax_inclusive=True, tax_category=tax_cats["VAT13"],
                                       hs_code="6403")
            Barcode.objects.create(product=p, code=code)
            products.append(p)
        receive_stock(branch, [Receipt(p.id, 25, p.cost_price_paisa) for p in products], StockMovement.PURCHASE_IN,
                      owner, ref_type="opening_stock", reason="demo opening stock")
