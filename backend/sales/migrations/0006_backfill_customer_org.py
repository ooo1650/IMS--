from django.db import migrations


def forwards(apps, schema_editor):
    Customer = apps.get_model("sales", "Customer")
    Organization = apps.get_model("tenancy", "Organization")
    for c in Customer.objects.filter(org__isnull=True).select_related("branch"):
        c.org_id = c.branch.org_id if c.branch_id else Organization.objects.order_by("id").values_list("id", flat=True).first()
        c.save(update_fields=["org"])


class Migration(migrations.Migration):
    dependencies = [("sales", "0005_customer_org"), ("tenancy", "0004_backfill_business")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
