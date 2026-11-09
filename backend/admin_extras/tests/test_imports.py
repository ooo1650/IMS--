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
        r = imports.import_products(self.w.org, PRODUCTS, dry_run=True)
        self.assertTrue(r["ok"])
        self.assertEqual((r["creates"], r["updates"]), (1, 1))
        self.assertFalse(Product.objects.filter(sku="NEW1").exists())

    def test_commit_upserts_on_sku_and_converts_rupees(self):
        imports.import_products(self.w.org, PRODUCTS, dry_run=False)
        imports.import_products(self.w.org, PRODUCTS, dry_run=False)  # idempotent
        p = Product.objects.get(sku="NEW1")
        self.assertEqual(p.sale_price_paisa, 12050)
        self.assertEqual(Product.objects.get(sku="P1").name, "Renamed")
        self.assertEqual(Product.objects.filter(sku="NEW1").count(), 1)

    def test_row_errors_block_commit(self):
        bad = b"sku,name,price\nX1,Ok,10\nX2,Bad,12.345\n,NoSku,5\n"
        r = imports.import_products(self.w.org, bad, dry_run=False)
        self.assertFalse(r["ok"])
        self.assertEqual([e["row"] for e in r["errors"]], [3, 4])
        self.assertFalse(Product.objects.filter(sku="X1").exists())

    def test_opening_stock_is_not_doubled_on_reimport(self):
        imports.import_products(self.w.org, PRODUCTS, dry_run=False)
        csv = b"sku,qty,cost\nNEW1,25,95.00\n"
        imports.import_opening_stock(self.w.a, csv, self.w.admin, dry_run=False)
        r = imports.import_opening_stock(self.w.a, csv, self.w.admin, dry_run=False)
        self.assertEqual(r["skips"], 1)
        item = InventoryItem.objects.get(branch=self.w.a, product__sku="NEW1")
        self.assertEqual((item.quantity_on_hand, item.weighted_avg_cost_paisa), (25, 9500))


def xlsx(rows):
    """An in-memory .xlsx with the given rows (first row = header)."""
    import io

    from openpyxl import Workbook

    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class ExcelImportTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_products_from_xlsx_with_numeric_cells(self):
        # Excel stores numbers as floats: 120.5, a barcode as 8901234567890.0, a rate as 13.
        data = xlsx([["SKU", "Name", "Barcode", "Price", "Cost", "Tax_rate", "Tax_inclusive", "Category"],
                     ["NEW1", "New One", 8901234567890, 120.5, 90, 13, True, "Snacks"],
                     [None, None, None, None, None, None, None, None]])  # formatted-but-empty row
        r = imports.import_products(self.w.org, data, dry_run=False)
        self.assertTrue(r["ok"], r["errors"])
        p = Product.objects.get(org=self.w.org, sku="NEW1")
        self.assertEqual((p.sale_price_paisa, p.cost_price_paisa, p.tax_inclusive), (12050, 9000, True))
        self.assertEqual(p.barcodes.get().code, "8901234567890")

    def test_opening_stock_from_xlsx(self):
        imports.import_products(self.w.org, PRODUCTS, dry_run=False)
        r = imports.import_opening_stock(self.w.a, xlsx([["sku", "qty", "cost"], ["NEW1", 25, 95]]), self.w.admin,
                                         dry_run=False)
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(InventoryItem.objects.get(branch=self.w.a, product__sku="NEW1").quantity_on_hand, 25)

    def test_bad_files_are_row_errors_not_crashes(self):
        broken_zip = b"PK\x03\x04not really a workbook"
        self.assertFalse(imports.import_products(self.w.org, broken_zip)["ok"])
        utf16 = "sku,name,price\nA,B,1\n".encode("utf-16")
        self.assertFalse(imports.import_products(self.w.org, utf16)["ok"])
