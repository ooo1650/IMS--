"""CSV import of products and opening stock (§16).

Both imports are idempotent and support dry-run: the same file twice never
doubles stock (opening stock is keyed on branch + SKU) and products upsert on
SKU. Prices in spreadsheets are rupees (e.g. 120.50) and convert exactly to
paisa via Decimal.
"""
import csv
import io
from decimal import Decimal, InvalidOperation

from django.db import transaction

from catalog.models import Barcode, Category, Product
from inventory.models import StockMovement
from inventory.services import Receipt, receive_stock

PRODUCT_COLUMNS = ["sku", "name", "barcode", "price", "cost", "tax_rate", "tax_inclusive", "category", "unit"]
STOCK_COLUMNS = ["sku", "qty", "cost"]

TEMPLATES = {
    "products": ",".join(PRODUCT_COLUMNS) + "\nRICE-5KG,Basmati Rice 5kg,8901234567890,850.00,700.00,13,yes,Grocery,pcs\n",
    "opening-stock": ",".join(STOCK_COLUMNS) + "\nRICE-5KG,40,700.00\n",
}


def rupees_to_paisa(raw, field):
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"{field}: {raw!r} is not a number")
    if value < 0:
        raise ValueError(f"{field} cannot be negative")
    paisa = value * 100
    if paisa != paisa.to_integral_value():
        raise ValueError(f"{field}: {raw!r} has more than 2 decimal places")
    return int(paisa)


def _rows(file_bytes):
    text = file_bytes.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(f or "").strip().lower() for f in reader.fieldnames or []]
    return reader


def _bool(raw, default=True):
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "yes", "y", "true", "inclusive")


def import_products(file_bytes, dry_run=True):
    reader = _rows(file_bytes)
    missing = {"sku", "name", "price"} - set(reader.fieldnames)
    if missing:
        return {"ok": False, "errors": [{"row": 1, "error": f"Missing columns: {', '.join(sorted(missing))}"}],
                "rows": []}
    rows, errors, seen_skus, seen_barcodes = [], [], set(), {}
    for n, raw in enumerate(reader, start=2):
        try:
            sku = (raw.get("sku") or "").strip()
            name = (raw.get("name") or "").strip()
            if not sku or not name:
                raise ValueError("sku and name are required")
            if sku in seen_skus:
                raise ValueError(f"duplicate sku {sku} in file")
            seen_skus.add(sku)
            barcode = (raw.get("barcode") or "").strip()
            if barcode:
                if barcode in seen_barcodes:
                    raise ValueError(f"barcode {barcode} also on row {seen_barcodes[barcode]}")
                seen_barcodes[barcode] = n
                owner = Barcode.objects.filter(code=barcode).exclude(product__sku=sku).first()
                if owner:
                    raise ValueError(f"barcode {barcode} already belongs to {owner.product.sku}")
            tax = Decimal((raw.get("tax_rate") or "13").strip())
            if not 0 <= tax <= 100:
                raise ValueError("tax_rate must be 0-100")
            rows.append({
                "row": n, "sku": sku, "name": name, "barcode": barcode,
                "sale_price_paisa": rupees_to_paisa(raw["price"], "price"),
                "cost_price_paisa": rupees_to_paisa(raw.get("cost") or 0, "cost"),
                "tax_rate": str(tax), "tax_inclusive": _bool(raw.get("tax_inclusive")),
                "category": (raw.get("category") or "").strip(), "unit": (raw.get("unit") or "pcs").strip(),
                "action": "update" if Product.objects.filter(sku=sku).exists() else "create",
            })
        except (ValueError, InvalidOperation) as e:
            errors.append({"row": n, "error": str(e)})
    result = {"ok": not errors, "dry_run": dry_run, "rows": rows, "errors": errors,
              "creates": sum(r["action"] == "create" for r in rows),
              "updates": sum(r["action"] == "update" for r in rows)}
    if dry_run or errors:
        return result
    with transaction.atomic():
        categories = {}
        for r in rows:
            cat = None
            if r["category"]:
                cat = categories.get(r["category"])
                if cat is None:
                    cat, _ = Category.objects.get_or_create(name=r["category"], parent=None)
                    categories[r["category"]] = cat
            product, _ = Product.objects.update_or_create(sku=r["sku"], defaults={
                "name": r["name"], "sale_price_paisa": r["sale_price_paisa"],
                "cost_price_paisa": r["cost_price_paisa"], "tax_rate": Decimal(r["tax_rate"]),
                "tax_inclusive": r["tax_inclusive"], "category": cat, "unit": r["unit"],
            })
            if r["barcode"]:
                Barcode.objects.get_or_create(code=r["barcode"], defaults={"product": product})
    return result


def import_opening_stock(branch, file_bytes, by, dry_run=True):
    reader = _rows(file_bytes)
    missing = {"sku", "qty"} - set(reader.fieldnames)
    if missing:
        return {"ok": False, "errors": [{"row": 1, "error": f"Missing columns: {', '.join(sorted(missing))}"}],
                "rows": []}
    already = set(StockMovement.objects.filter(branch=branch, ref_type="opening_stock")
                  .values_list("product__sku", flat=True))
    products = {p.sku: p for p in Product.objects.all()}
    rows, errors, seen = [], [], set()
    for n, raw in enumerate(reader, start=2):
        try:
            sku = (raw.get("sku") or "").strip()
            product = products.get(sku)
            if product is None:
                raise ValueError(f"unknown sku {sku!r} — import products first")
            if sku in seen:
                raise ValueError(f"duplicate sku {sku} in file")
            seen.add(sku)
            qty = int(str(raw["qty"]).strip())
            if qty <= 0:
                raise ValueError("qty must be positive")
            cost = rupees_to_paisa(raw["cost"], "cost") if (raw.get("cost") or "").strip() else product.cost_price_paisa
            rows.append({"row": n, "sku": sku, "product_id": product.id, "qty": qty, "unit_cost_paisa": cost,
                         "action": "skip (already imported)" if sku in already else "import"})
        except (ValueError, KeyError) as e:
            errors.append({"row": n, "error": str(e)})
    result = {"ok": not errors, "dry_run": dry_run, "rows": rows, "errors": errors,
              "imports": sum(r["action"] == "import" for r in rows),
              "skips": sum(r["action"] != "import" for r in rows)}
    if dry_run or errors:
        return result
    todo = [r for r in rows if r["action"] == "import"]
    with transaction.atomic():
        for r in todo:
            receive_stock(branch, [Receipt(r["product_id"], r["qty"], r["unit_cost_paisa"])],
                          StockMovement.PURCHASE_IN, by, ref=r["sku"], ref_type="opening_stock",
                          reason="opening stock import")
    return result
