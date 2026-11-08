"""In-process job scheduler for the Docker ``worker`` service (replaces deploy/crontab).

Times are local (settings.TIME_ZONE, Asia/Kathmandu). Jobs run one after
another; a slow minute is simply skipped, never queued. On PostgreSQL each
job takes an advisory lock, so a second worker can't run it twice.
Backups and the restore drill run in the ``backup`` service (they need the
pg_dump that matches the server).
"""
import signal
import time
import zlib
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone


@dataclass(frozen=True)
class Job:
    command: str
    every_minutes: int = 0  # >0: every N minutes
    at: str = ""  # "HH:MM": once a day

    def due(self, now: datetime) -> bool:
        if self.every_minutes:
            return (now.hour * 60 + now.minute) % self.every_minutes == 0
        return now.strftime("%H:%M") == self.at


JOBS = [
    Job("expire_open_sales", every_minutes=1),
    Job("cbms_sync", every_minutes=1),
    Job("reconcile_fonepay", at="02:30"),
    Job("reconcile_stock", at="03:00"),
    Job("refresh_auditor_views", at="03:15"),
]


class Command(BaseCommand):
    help = "Run the IMS periodic jobs (expiry, CBMS sync, reconciliations, auditor views)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run the jobs due this minute, then exit")

    def handle(self, *args, once=False, **opts):
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(True))
        signal.signal(signal.SIGINT, lambda *_: stop.append(True))
        self.stdout.write(f"scheduler: {len(JOBS)} jobs, timezone {timezone.get_current_timezone_name()}")
        while not stop:
            now = timezone.localtime().replace(second=0, microsecond=0)
            for job in JOBS:
                if job.due(now) and not stop:
                    self.run(job)
            if once:
                return
            # Sleep to the next minute boundary in short steps so SIGTERM is quick.
            while not stop and timezone.localtime().replace(second=0, microsecond=0) == now:
                time.sleep(1)

    def run(self, job: Job):
        close_old_connections()
        lock = zlib.crc32(job.command.encode())
        # Session locks don't survive a transaction-mode pooler (the unlock may
        # reach another server connection); there, run one scheduler only.
        locking = connection.vendor == "postgresql" and not settings.IMS_DB_TRANSACTION_POOLER
        try:
            if locking:
                with connection.cursor() as cur:
                    cur.execute("SELECT pg_try_advisory_lock(%s)", [lock])
                    if not cur.fetchone()[0]:
                        return
            started = time.monotonic()
            call_command(job.command, stdout=self.stdout, stderr=self.stderr)
            self.stdout.write(f"scheduler: {job.command} ok in {time.monotonic() - started:.1f}s")
        except Exception as e:  # one failing job must not stop the others
            self.stderr.write(f"scheduler: {job.command} failed: {e!r}")
        finally:
            if locking:
                try:
                    with connection.cursor() as cur:
                        cur.execute("SELECT pg_advisory_unlock(%s)", [lock])
                except Exception:
                    close_old_connections()
