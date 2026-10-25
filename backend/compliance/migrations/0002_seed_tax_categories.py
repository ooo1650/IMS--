"""Seed the default tax categories (payment.md Step 3) and move every product
onto one. Rates are data, not code: change them in Tax & IRD settings with a
new effective-from date when the Finance Act changes."""
from datetime import date
from decimal import Decimal

from django.db import migrations

LONG_AGO = date(2000, 1, 1)
FY_2083 = date(2026, 7, 17)  # Shrawan 1, 2083

DEFAULTS = [
    # code, name, kind, levy, [(effective_from, vat, levy, note)]
    ("VAT13", "Standard VAT 13%", "standard", "", [(LONG_AGO, "13", "0", "VAT Act 2052")]),
    ("VAT5", "Reduced VAT 5%", "reduced", "",
     [(FY_2083, "5", "0", "Finance Act 2083 — confirm scope and date with IRD")]),
    ("ZERO", "Zero-rated (exports)", "zero", "", [(LONG_AGO, "0", "0", "")]),
    ("EXEMPT", "VAT exempt (Schedule 1)", "exempt", "", [(LONG_AGO, "0", "0", "")]),
    ("HST5", "Health service (HST 5%, VAT exempt)", "exempt", "hst", [(LONG_AGO, "0", "5", "Health Service Tax Act")]),
]


def seed(apps, schema_editor):
    TaxCategory = apps.get_model("compliance", "TaxCategory")
    TaxCategoryRate = apps.get_model("compliance", "TaxCategoryRate")
    Product = apps.get_model("catalog", "Product")
    Organization = apps.get_model("tenancy", "Organization")
    ComplianceSettings = apps.get_model("compliance", "ComplianceSettings")
    cats = {}
    for code, name, kind, levy, rates in DEFAULTS:
        cat, _ = TaxCategory.objects.get_or_create(code=code, defaults=dict(name=name, kind=kind, levy_type=levy))
        for eff, vat, levy_rate, note in rates:
            TaxCategoryRate.objects.get_or_create(category=cat, effective_from=eff, defaults=dict(
                vat_rate=Decimal(vat), levy_rate=Decimal(levy_rate), note=note))
        cats[code] = cat
    for p in Product.objects.filter(tax_category__isnull=True):
        rate = p.tax_rate
        if rate == Decimal("13"):
            cat = cats["VAT13"]
        elif rate == 0:
            cat = cats["EXEMPT"]
        elif rate == Decimal("5"):
            cat = cats["VAT5"]
        else:
            code = f"VAT{str(rate.normalize()).replace('.', '_')}"
            cat, created = TaxCategory.objects.get_or_create(code=code, defaults=dict(
                name=f"VAT {rate.normalize()}%", kind="standard"))
            if created:
                TaxCategoryRate.objects.create(category=cat, effective_from=LONG_AGO, vat_rate=rate)
        Product.objects.filter(pk=p.pk).update(tax_category=cat)
    for org in Organization.objects.all():
        ComplianceSettings.objects.get_or_create(org=org)


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0001_initial"),
        ("catalog", "0002_product_excise_rate_product_hs_code_and_more"),
        ("tenancy", "0002_branch_phone_organization_address_and_more"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
