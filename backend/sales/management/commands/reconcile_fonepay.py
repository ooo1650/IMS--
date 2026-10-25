from datetime import date, timedelta

from django.core.management.base import BaseCommand

from sales.fonepay import get_gateway
from sales.models import FonepayMismatch, Payment


class Command(BaseCommand):
    help = "Reconcile FonePay settlement against recorded payments (§14). Run daily for the previous day."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")

    def handle(self, *args, **opts):
        day = date.fromisoformat(opts["date"]) if opts["date"] else date.today() - timedelta(days=1)
        remote = {r["prn"]: r for r in get_gateway().settlement_report(day)}
        local = {p.fonepay_prn: p for p in Payment.objects.filter(
            method=Payment.FONEPAY, created_at__date=day).select_related("sale")}
        found = 0
        for prn, r in remote.items():
            p = local.get(prn)
            if p is None:
                kind, detail, branch = "missing_local", r, None
            elif p.status != Payment.SUCCESS:
                kind, detail, branch = "status_mismatch", {"remote": r, "local_status": p.status}, p.sale.branch
            elif r["amount_paisa"] != p.amount_paisa:
                kind, detail, branch = "amount_mismatch", {"remote": r, "local_amount": p.amount_paisa}, p.sale.branch
            else:
                continue
            FonepayMismatch.objects.get_or_create(prn=prn, kind=kind, defaults={"detail": detail, "branch": branch})
            found += 1
        for prn, p in local.items():
            if p.status == Payment.SUCCESS and prn not in remote:
                FonepayMismatch.objects.get_or_create(
                    prn=prn, kind="missing_remote",
                    defaults={"detail": {"local_amount": p.amount_paisa, "sale": p.sale_id}, "branch": p.sale.branch})
                found += 1
        self.stdout.write(self.style.SUCCESS(f"{day}: {len(remote)} remote, {len(local)} local, {found} mismatch(es)"))
