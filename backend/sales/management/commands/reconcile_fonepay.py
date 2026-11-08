from datetime import date, timedelta

from django.core.management.base import BaseCommand

from sales.fonepay import HttpFonepayGateway, MockFonepayGateway, server_settings
from sales.models import FonepayMerchant, FonepayMismatch, Payment
from tenancy.models import Organization


class Command(BaseCommand):
    help = "Reconcile FonePay settlement against recorded payments (§14). Run daily for the previous day."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")

    def handle(self, *args, **opts):
        day = date.fromisoformat(opts["date"]) if opts["date"] else date.today() - timedelta(days=1)
        # Each business has its own FonePay mode; each branch its own merchant.
        for org in Organization.objects.filter(is_active=True):
            backend, base_url = server_settings(org)
            if backend == "disabled":
                self.stdout.write(f"{org.name}: FonePay is off; nothing to reconcile.")
            elif backend == "mock":
                self.reconcile(day, None, MockFonepayGateway(), org)  # one shared sandbox
            else:
                for m in FonepayMerchant.objects.filter(enabled=True, branch__org=org).select_related("branch"):
                    if not m.is_complete():
                        self.stdout.write(self.style.WARNING(f"{m.branch.code}: merchant incomplete, skipped"))
                        continue
                    self.reconcile(day, m.branch, HttpFonepayGateway(m, base_url), org)

    def reconcile(self, day, branch, gateway, org):
        remote = {r["prn"]: r for r in gateway.settlement_report(day)}
        if branch is None:
            # The mock sandbox is shared by every business: drop payments that belong to another one.
            others = set(Payment.objects.filter(fonepay_prn__in=list(remote)).exclude(sale__branch__org=org)
                         .values_list("fonepay_prn", flat=True))
            remote = {k: v for k, v in remote.items() if k not in others}
        payments = Payment.objects.filter(method=Payment.FONEPAY, created_at__date=day).select_related("sale")
        payments = payments.filter(sale__branch=branch) if branch is not None else payments.filter(sale__branch__org=org)
        local = {p.fonepay_prn: p for p in payments}
        found = 0
        for prn, r in remote.items():
            p = local.get(prn)
            if p is None:
                kind, detail, where = "missing_local", r, branch
            elif p.status != Payment.SUCCESS:
                kind, detail, where = "status_mismatch", {"remote": r, "local_status": p.status}, p.sale.branch
            elif r["amount_paisa"] != p.amount_paisa:
                kind, detail, where = "amount_mismatch", {"remote": r, "local_amount": p.amount_paisa}, p.sale.branch
            else:
                continue
            FonepayMismatch.objects.get_or_create(prn=prn, kind=kind, defaults={"detail": detail, "branch": where})
            found += 1
        for prn, p in local.items():
            if p.status == Payment.SUCCESS and prn not in remote:
                FonepayMismatch.objects.get_or_create(
                    prn=prn, kind="missing_remote",
                    defaults={"detail": {"local_amount": p.amount_paisa, "sale": p.sale_id}, "branch": p.sale.branch})
                found += 1
        label = branch.code if branch else "all branches"
        self.stdout.write(self.style.SUCCESS(
            f"{day} {label}: {len(remote)} remote, {len(local)} local, {found} mismatch(es)"))
