from django.core.management.base import BaseCommand

from sales.services import expire_open_sales


class Command(BaseCommand):
    help = "Final-poll and void abandoned FonePay sales past expires_at (§10.5). Run every minute from cron."

    def handle(self, *args, **opts):
        done = expire_open_sales()
        for sale in done:
            self.stdout.write(f"sale {sale.id}: {sale.status}")
        self.stdout.write(self.style.SUCCESS(f"{len(done)} open sale(s) processed"))
