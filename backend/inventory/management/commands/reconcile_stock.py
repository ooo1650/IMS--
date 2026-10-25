from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from inventory.models import InventoryItem, StockMovement


class Command(BaseCommand):
    help = "Check the cached quantity_on_hand against the sum of movements; --fix to repair."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true")

    def handle(self, *args, **opts):
        sums = {(r["branch_id"], r["product_id"]): r["q"] for r in
                StockMovement.objects.values("branch_id", "product_id").annotate(q=Sum("qty"))}
        bad = 0
        with transaction.atomic():
            for item in InventoryItem.objects.select_for_update().order_by("branch_id", "product_id"):
                expected = sums.get((item.branch_id, item.product_id), 0)
                if item.quantity_on_hand != expected:
                    bad += 1
                    self.stdout.write(f"branch {item.branch_id} product {item.product_id}: "
                                      f"cached {item.quantity_on_hand}, movements {expected}")
                    if opts["fix"]:
                        item.quantity_on_hand = expected
                        item.save(update_fields=["quantity_on_hand"])
        self.stdout.write(self.style.SUCCESS(f"{bad} mismatch(es){' fixed' if opts['fix'] and bad else ''}"))
