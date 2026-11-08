"""Product photos (catalog/images.py) and the POS catalog version check."""
import shutil
import tempfile
from io import BytesIO
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from catalog.models import Product
from ims.testutils import World


def png(w=1200, h=900, color=(200, 30, 30)):
    buf = BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return SimpleUploadedFile("photo.png", buf.getvalue(), content_type="image/png")


class ProductImageTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.w = World()
        self.admin = self.w.client(self.w.admin, self.w.a)

    def upload(self, client, product, file):
        return client.post(f"/api/v1/products/{product.id}/image/", {"image": file}, format="multipart")

    def test_upload_resizes_to_webp_and_exposes_urls(self):
        r = self.upload(self.admin, self.w.p1, png())
        self.assertEqual(r.status_code, 200, r.data)
        key = Product.objects.get(pk=self.w.p1.pk).image_key
        self.assertEqual(r.data["image_url"], f"/media/products/{key}.webp")
        self.assertEqual(r.data["thumb_url"], f"/media/products/{key}-t.webp")
        with Image.open(Path(self.media) / "products" / f"{key}.webp") as full:
            self.assertEqual((full.format, full.size), ("WEBP", (800, 600)))
        with Image.open(Path(self.media) / "products" / f"{key}-t.webp") as thumb:
            self.assertEqual(thumb.size, (160, 120))

    def test_pos_catalog_carries_thumb(self):
        self.upload(self.admin, self.w.p1, png())
        products = self.w.client(self.w.cashier_a, self.w.a).get("/api/v1/pos/catalog/").data["products"]
        thumbs = {p["id"]: p["thumb"] for p in products}
        self.assertTrue(thumbs[self.w.p1.id].endswith("-t.webp"))
        self.assertIsNone(thumbs[self.w.p2.id])

    def test_rejects_non_images_and_cashiers(self):
        fake = SimpleUploadedFile("x.png", b"<script>alert(1)</script>", content_type="image/png")
        self.assertEqual(self.upload(self.admin, self.w.p1, fake).status_code, 400)
        cashier = self.w.client(self.w.cashier_a, self.w.a)
        self.assertEqual(self.upload(cashier, self.w.p1, png()).status_code, 403)
        self.assertEqual(Product.objects.get(pk=self.w.p1.pk).image_key, "")

    def test_replacing_and_removing_deletes_unused_files(self):
        self.upload(self.admin, self.w.p1, png(color=(1, 2, 3)))
        first = Product.objects.get(pk=self.w.p1.pk).image_key
        self.upload(self.admin, self.w.p2, png(color=(1, 2, 3)))  # same photo, shared files
        with self.captureOnCommitCallbacks(execute=True):
            self.upload(self.admin, self.w.p1, png(color=(9, 9, 9)))
        folder = Path(self.media) / "products"
        self.assertTrue((folder / f"{first}.webp").exists(), "still used by P2")
        with self.captureOnCommitCallbacks(execute=True):
            r = self.admin.post(f"/api/v1/products/{self.w.p2.id}/remove-image/")
        self.assertIsNone(r.data["image_url"])
        self.assertFalse((folder / f"{first}.webp").exists())


class PosCatalogVersionTests(TestCase):
    def setUp(self):
        self.w = World()
        self.c = self.w.client(self.w.cashier_a, self.w.a)

    def test_unchanged_catalog_skips_the_product_list(self):
        first = self.c.get("/api/v1/pos/catalog/").data
        again = self.c.get(f"/api/v1/pos/catalog/?version={first['version']}").data
        self.assertTrue(again["unchanged"])
        self.assertNotIn("products", again)
        Product.objects.filter(pk=self.w.p1.pk).update(sale_price_paisa=12345)
        changed = self.c.get(f"/api/v1/pos/catalog/?version={first['version']}").data
        self.assertNotEqual(changed["version"], first["version"])
        self.assertEqual(len(changed["products"]), 3)
