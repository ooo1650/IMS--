"""Recreate the SQLite auditor views dropped by tenancy 0003 (multi-business
upgrade) now that every table has been rebuilt. PostgreSQL keeps its
materialized views throughout, so this does nothing there."""
import importlib

from django.db import migrations


def recreate(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    base = importlib.import_module("compliance.migrations.0003_append_only_and_auditor_views")
    schema_editor.execute("DROP VIEW IF EXISTS cbms_bill_view")
    schema_editor.execute("DROP VIEW IF EXISTS cbms_return_view")
    schema_editor.execute(f"CREATE VIEW cbms_bill_view AS {base.BILL_VIEW_SELECT}")
    schema_editor.execute(f"CREATE VIEW cbms_return_view AS {base.RETURN_VIEW_SELECT}")


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0003_append_only_and_auditor_views"),
        ("tenancy", "0004_backfill_business"),
        ("catalog", "0006_alter_barcode_org_alter_category_org_and_more"),
        ("accounts", "0005_alter_userbranchrole_org"),
        ("sales", "0007_alter_customer_org"),
    ]
    operations = [migrations.RunPython(recreate, migrations.RunPython.noop)]
