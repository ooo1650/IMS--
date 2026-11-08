"""Every existing category, supplier, product and barcode belongs to the
business that existed before multi-business support (the first one)."""
from django.db import migrations


def forwards(apps, schema_editor):
    Organization = apps.get_model("tenancy", "Organization")
    models = [apps.get_model("catalog", m) for m in ("Category", "Supplier", "Product")]
    if not any(m.objects.filter(org__isnull=True).exists() for m in models):
        return
    org = Organization.objects.order_by("id").first() or Organization.objects.create(name="My business", code="my-business")
    for m in models:
        m.objects.filter(org__isnull=True).update(org=org)
    Barcode = apps.get_model("catalog", "Barcode")
    for b in Barcode.objects.filter(org__isnull=True).select_related("product"):
        b.org_id = b.product.org_id
        b.save(update_fields=["org"])


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_barcode_org_category_org_product_org_supplier_org_and_more"),
                    ("tenancy", "0004_backfill_business")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
