"""Demo data for local development: 3 branches, users for each role, a small
catalog with opening stock, and a few days of sales for the dashboard."""
import random
import uuid
from datetime import timedelta
from decimal import Decimal

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


def paisa(npr):
    return int(Decimal(npr) * 100)


class Command(BaseCommand):
    help = "Seed demo branches, users, catalog, stock and sales. Refuses to run twice."

    def handle(self, *args, **opts):
        if Organization.objects.exists():
            raise CommandError("Data already exists; run on an empty database (flush first).")
        with transaction.atomic():
            self.seed()
        self.stdout.write(self.style.SUCCESS(f"Seeded. Every demo user's password is: {PASSWORD}"))
        for u in User.objects.order_by("username"):
            roles = ", ".join(f"{r.role}@{r.branch.code if r.branch else 'ORG'}" for r in u.branch_roles.all())
            self.stdout.write(f"  {u.username:<14} {roles}")

    def seed(self):
        org = Organization.objects.create(
            name="Himalaya Mart Pvt. Ltd.", name_np="हिमालय मार्ट प्रा. लि.", pan="999999999",
            address="New Road, Kathmandu", phone="01-4200000")
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

        def user(username, first, roles, superuser=False):
            u = User.objects.create(username=username, first_name=first, is_staff=superuser, is_superuser=superuser)
            u.set_password(PASSWORD)
            u.save()
            for branch, role in roles:
                UserBranchRole.objects.create(user=u, branch=branch, role=role)
            return u

        admin = user("admin", "Org Admin", [(None, ORG_ADMIN)], superuser=True)
        user("manager", "Sita Manager", [(ktm, BRANCH_MANAGER), (pkr, CASHIER)])
        cashier = user("cashier", "Ram Cashier", [(ktm, CASHIER)])
        user("cashier_pkr", "Hari Cashier", [(pkr, CASHIER)])
        user("clerk", "Gita Clerk", [(ktm, INVENTORY_CLERK)])
        user("viewer", "Auditor", [(None, VIEWER)])
        user("manager_brt", "Bikash Manager", [(brt, BRANCH_MANAGER)])

        suppliers = [Supplier.objects.create(name=n, phone=p, pan=pan) for n, p, pan in
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
            cats.setdefault(cat, Category.objects.get_or_create(name=cat)[0])
            p = Product.objects.create(
                sku=sku, name=name, category=cats[cat], supplier=random.choice(suppliers),
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
