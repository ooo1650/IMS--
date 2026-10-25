from django.core.management.base import BaseCommand

from compliance import cbms


class Command(BaseCommand):
    help = "Post pending bills/credit notes to IRD CBMS with retry/backoff. Run every minute from cron."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore backoff and paused state")

    def handle(self, *args, **opts):
        done = cbms.sync_pending(force=opts["force"])
        for p in done:
            self.stdout.write(f"{p.doc_type} {p.document_number}: {p.status} ({p.last_code})")
        s = cbms.status_summary()
        if s["paused_reason"]:
            self.stdout.write(self.style.ERROR(f"PAUSED: {s['paused_reason']}"))
        self.stdout.write(self.style.SUCCESS(
            f"{len(done)} posted · pending {s['pending']} · synced {s['synced']} · failed {s['failed']}"))
