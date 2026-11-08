"""Existing businesses get a short code and their shop rules, copied from the
IMS_* environment variables they used before (defaults if unset)."""
import os

from django.db import migrations
from django.utils.text import slugify


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def forwards(apps, schema_editor):
    Organization = apps.get_model("tenancy", "Organization")
    BusinessRules = apps.get_model("tenancy", "BusinessRules")
    taken = set(Organization.objects.exclude(code=None).values_list("code", flat=True))
    for org in Organization.objects.order_by("id"):
        if not org.code:
            base = slugify(org.name)[:32] or "business"
            code, n = base, 2
            while code in taken:
                code, n = f"{base}-{n}", n + 1
            taken.add(code)
            org.code = code
            org.save(update_fields=["code"])
        BusinessRules.objects.get_or_create(org=org, defaults={
            "cash_rounding_paisa": env_int("IMS_CASH_ROUNDING_PAISA", 1),
            "cashier_max_discount_bp": env_int("IMS_CASHIER_MAX_DISCOUNT_BP", 500),
            "return_approval_threshold_paisa": env_int("IMS_RETURN_APPROVAL_THRESHOLD", 500000),
            "stocktake_variance_threshold": env_int("IMS_STOCKTAKE_VARIANCE_THRESHOLD", 5),
        })


class Migration(migrations.Migration):
    dependencies = [("tenancy", "0003_businessrules_organization_code_and_more")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
