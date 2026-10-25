from datetime import date
from decimal import Decimal
from fractions import Fraction

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from admin_extras.models import log_action
from catalog.models import tax_profile
from compliance import bs
from inventory.models import InventoryItem, StockMovement
from inventory.services import Receipt, lock_items, receive_stock
from sales.models import DocumentSequence
from sales.numbering import allocate, fiscal_year_for, local_today
from sales.pricing import rate_fraction, round_half_up

from .models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, PurchaseReturn, PurchaseReturnLine


def mark_ordered(po, by, request=None):
    if po.status != PurchaseOrder.DRAFT:
        raise ValidationError("Only draft POs can be marked ordered")
    if not po.lines.exists():
        raise ValidationError("PO has no lines")
    po.status, po.ordered_at = PurchaseOrder.ORDERED, timezone.now()
    po.save(update_fields=["status", "ordered_at"])
    log_action(by, "po.ordered", po, po.branch, request)


def cancel_po(po, by, request=None):
    if po.status not in (PurchaseOrder.DRAFT, PurchaseOrder.ORDERED):
        raise ValidationError("Only draft or ordered POs with nothing received can be cancelled")
    po.status = PurchaseOrder.CANCELLED
    po.save(update_fields=["status"])
    log_action(by, "po.cancelled", po, po.branch, request)


def purchase_vat(rows):
    """rows: [(taxable_paisa, vat_rate, blocked_pct)] -> (per-row vat, total vat,
    claimable vat). VAT rounded once per rate, like sales."""
    from sales.pricing import allocate as allocate_shares

    unrounded = [Fraction(t) * rate_fraction(r) / 100 for t, r, _ in rows]
    per_row = [0] * len(rows)
    groups = {}
    for i, (_, r, _) in enumerate(rows):
        groups.setdefault(Decimal(str(r)), []).append(i)
    for idxs in groups.values():
        total = round_half_up(sum((unrounded[i] for i in idxs), Fraction(0)))
        for i, share in zip(idxs, allocate_shares(total, [unrounded[i] for i in idxs])):
            per_row[i] = share
    claimable = sum(round_half_up(Fraction(v * (100 - b), 100)) for v, (_, _, b) in zip(per_row, rows))
    return per_row, sum(per_row), claimable


def receive_goods(po, by, lines, supplier_invoice_ref="", supplier_invoice_date=None, is_import=False,
                  request=None):
    """``lines``: [(po_line_id, qty, unit_cost_paisa or None)]. Partial receipts
    allowed. Writes purchase_in at the actual cost (recomputes weighted average)
    with InventoryItem rows locked in product_id order (§19), and records the
    supplier invoice's VAT for the purchase book (Schedule-8)."""
    with transaction.atomic():
        po = PurchaseOrder.objects.select_for_update(of=("self",)).select_related("branch").get(pk=po.pk)
        if po.status not in (PurchaseOrder.ORDERED, PurchaseOrder.PARTIAL):
            raise ValidationError(f"Cannot receive against a {po.status} PO")
        po_lines = {l.id: l for l in PurchaseOrderLine.objects.select_for_update(of=("self",))
                    .select_related("product__tax_category").filter(po=po)}
        planned = []
        for line_id, qty, cost in lines:
            line = po_lines.get(int(line_id))
            if line is None:
                raise ValidationError(f"Line {line_id} is not on this PO")
            if qty <= 0:
                continue
            if line.qty_received + qty > line.qty_ordered:
                raise ValidationError(
                    f"Line {line_id}: receiving {qty} would exceed ordered {line.qty_ordered} "
                    f"(already {line.qty_received})"
                )
            planned.append((line, qty, line.unit_cost_paisa if cost is None else int(cost)))
        if not planned:
            raise ValidationError("Nothing to receive")
        if isinstance(supplier_invoice_date, str) and supplier_invoice_date:
            supplier_invoice_date = date.fromisoformat(supplier_invoice_date)
        inv_date = supplier_invoice_date or local_today(po.branch)
        profiles = [tax_profile(l.product, inv_date) for l, _, _ in planned]
        rows = [(q * c, p[0], l.product.input_credit_blocked_pct) for (l, q, c), p in zip(planned, profiles)]
        per_vat, vat, claimable = purchase_vat(rows)
        taxable = sum(t for (t, r, _) in rows if Decimal(str(r)) > 0)
        exempt = sum(t for (t, r, _) in rows if Decimal(str(r)) == 0)
        gr = GoodsReceipt.objects.create(
            po=po, by=by, supplier_invoice_ref=supplier_invoice_ref, supplier_invoice_date=inv_date,
            supplier_invoice_bs=bs.bs_str(inv_date), received_bs=bs.bs_str(local_today(po.branch)),
            is_import=is_import, taxable_paisa=taxable, exempt_paisa=exempt, vat_paisa=vat,
            claimable_vat_paisa=claimable,
        )
        GoodsReceiptLine.objects.bulk_create([
            GoodsReceiptLine(receipt=gr, po_line=l, qty=q, unit_cost_paisa=c, vat_rate=p[0], tax_kind=p[1],
                             vat_paisa=v, input_credit_blocked_pct=l.product.input_credit_blocked_pct)
            for (l, q, c), p, v in zip(planned, profiles, per_vat)
        ])
        receive_stock(po.branch, [Receipt(l.product_id, q, c) for l, q, c in planned],
                      StockMovement.PURCHASE_IN, by, gr.id, "goods_receipt")
        for l, q, _ in planned:
            l.qty_received += q
        PurchaseOrderLine.objects.bulk_update([l for l, _, _ in planned], ["qty_received"])
        complete = all(l.qty_received >= l.qty_ordered for l in po_lines.values())
        po.status = PurchaseOrder.RECEIVED if complete else PurchaseOrder.PARTIAL
        if complete:
            po.received_at = timezone.now()
        po.save(update_fields=["status", "received_at"])
        log_action(by, "po.received", po, po.branch, request, goods_receipt=gr.id, complete=complete,
                   supplier_invoice=supplier_invoice_ref, vat=vat)
        return gr


def create_purchase_return(*, branch, supplier, by, lines, reason, goods_receipt=None, supplier_note_ref="",
                           request=None):
    """``lines``: [(product_id, qty, unit_cost_paisa or None)]. Stock leaves at
    the given cost (default: current average); input VAT is reversed."""
    if not reason or not reason.strip():
        raise ValidationError("A reason is required")
    lines = [(int(p), int(q), c) for p, q, c in lines if int(q) > 0]
    if not lines:
        raise ValidationError("Nothing to return")
    with transaction.atomic():
        items = lock_items(branch, [p for p, _, _ in lines])
        from catalog.models import Product

        products = Product.objects.select_related("tax_category").in_bulk([p for p, _, _ in lines])
        today = local_today(branch)
        planned = []
        for pid, qty, cost in sorted(lines):
            item = items[pid]
            if item.quantity_on_hand < qty and not item.allow_negative:
                raise ValidationError(f"Only {item.quantity_on_hand} of {products[pid].sku} in stock")
            unit = item.weighted_avg_cost_paisa if cost in (None, "") else int(cost)
            planned.append((pid, qty, unit, tax_profile(products[pid], today)[0]))
        rows = [(q * c, r, products[p].input_credit_blocked_pct) for p, q, c, r in planned]
        per_vat, vat, claimable = purchase_vat(rows)
        fy = fiscal_year_for(today)
        seq, _, number = allocate(DocumentSequence.PURCHASE_RETURN, branch, None, fy)
        pr = PurchaseReturn.objects.create(
            branch=branch, supplier=supplier, goods_receipt=goods_receipt, by=by, fiscal_year=fy, seq=seq,
            number=number, issued_bs=bs.bs_str(today), supplier_note_ref=supplier_note_ref, reason=reason.strip(),
            taxable_paisa=sum(t for t, _, _ in rows), vat_paisa=vat, claimable_vat_paisa=claimable,
        )
        PurchaseReturnLine.objects.bulk_create([
            PurchaseReturnLine(purchase_return=pr, product_id=p, qty=q, unit_cost_paisa=c, vat_rate=r, vat_paisa=v)
            for (p, q, c, r), v in zip(planned, per_vat)
        ])
        movements = []
        for p, q, c, _ in planned:
            items[p].quantity_on_hand -= q
            movements.append(StockMovement(branch=branch, product_id=p, type=StockMovement.PURCHASE_RETURN_OUT,
                                           qty=-q, unit_cost_paisa=c, by=by, ref=str(pr.id),
                                           ref_type="purchase_return", reason=reason))
        InventoryItem.objects.bulk_update(items.values(), ["quantity_on_hand"])
        StockMovement.objects.bulk_create(movements)
        log_action(by, "purchase.returned", pr, branch, request, number=number, vat=vat)
        return pr
