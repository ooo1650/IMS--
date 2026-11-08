from django.core.management.base import BaseCommand

from compliance import cbms


class Command(BaseCommand):
    help = "Post pending bills/credit notes to IRD CBMS with retry/backoff. Run every minute from cron."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="ignore backoff and paused state")

    def handle(self, *args, **opts):
        from tenancy.models import Organization

        # Each business posts with its own PAN and CBMS login.
        for org in Organization.objects.all():
            done = cbms.sync_pending(force=opts["force"], org=org)
            for p in done:
                self.stdout.write(f"{p.doc_type} {p.document_number}: {p.status} ({p.last_code})")
            s = cbms.status_summary(org)
            if s["paused_reason"]:
                self.stdout.write(self.style.ERROR(f"{org.name}: PAUSED: {s['paused_reason']}"))
            self.stdout.write(self.style.SUCCESS(
                f"{org.name}: {len(done)} posted · pending {s['pending']} · synced {s['synced']} · failed {s['failed']}"))
