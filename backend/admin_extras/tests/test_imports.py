from django.test import TestCase

from admin_extras import imports
from catalog.models import Product
from ims.testutils import World
from inventory.models import InventoryItem

PRODUCTS = b"sku,name,barcode,price,cost,tax_rate,tax_inclusive,category\nNEW1,New One,999001,120.50,90,13,yes,Snacks\nP1,Renamed,,100,70,13,no,\n"


class ImportTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_dry_run_previews_without_writing(self):
        r = imports.import_products(PRODUCTS, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual((r["creates"], r["updates"]), (1, 1))
        self.assertFalse(Product.objects.filter(sku="NEW1").exists())

    def test_commit_upserts_on_sku_and_converts_rupees(self):
        imports.import_products(PRODUCTS, dry_run=False)
        imports.import_products(PRODUCTS, dry_run=False)  # idempotent
        p = Product.objects.get(sku="NEW1")
        self.assertEqual(p.sale_price_paisa, 12050)
        self.assertEqual(Product.objects.get(sku="P1").name, "Renamed")
        self.assertEqual(Product.objects.filter(sku="NEW1").count(), 1)

    def test_row_errors_block_commit(self):
        bad = b"sku,name,price\nX1,Ok,10\nX2,Bad,12.345\n,NoSku,5\n"
        r = imports.import_products(bad, dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual([e["row"] for e in r["errors"]], [3, 4])
        self.assertFalse(Product.objects.filter(sku="X1").exists())

    def test_opening_stock_is_not_doubled_on_reimport(self):
        imports.import_products(PRODUCTS, dry_run=False)
        csv = b"sku,qty,cost\nNEW1,25,95.00\n"
        imports.import_opening_stock(self.w.a, csv, self.w.admin, dry_run=False)
        r = imports.import_opening_stock(self.w.a, csv, self.w.admin, dry_run=False)
        self.assertEqual(r["skips"], 1)
        item = InventoryItem.objects.get(branch=self.w.a, product__sku="NEW1")
        self.assertEqual((item.quantity_on_hand, item.weighted_avg_cost_paisa), (25, 9500))
