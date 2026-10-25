from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Refresh the CBMS-field materialized views for IRD auditors (PostgreSQL). Run nightly."

    def handle(self, *args, **opts):
        if connection.vendor != "postgresql":
            self.stdout.write("Not PostgreSQL: the views are plain views, nothing to refresh.")
            return
        with connection.cursor() as c:
            c.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY cbms_bill_view;")
            c.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY cbms_return_view;")
        self.stdout.write(self.style.SUCCESS("Refreshed cbms_bill_view and cbms_return_view"))
