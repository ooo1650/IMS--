"""Branch roles take their branch's business; existing business-wide roles
belong to the business that existed before (the first one)."""
from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "UserBranchRole")
    Organization = apps.get_model("tenancy", "Organization")
    first = Organization.objects.order_by("id").values_list("id", flat=True).first()
    for r in Role.objects.filter(org__isnull=True).select_related("branch"):
        r.org_id = r.branch.org_id if r.branch_id else first
        r.save(update_fields=["org"])
    Role.objects.filter(org__isnull=True).delete()  # only possible with no business at all


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_remove_userbranchrole_uniq_user_org_role_and_more"),
                    ("tenancy", "0004_backfill_business")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
