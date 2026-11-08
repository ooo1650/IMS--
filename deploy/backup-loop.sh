#!/bin/sh
# Docker `backup` service: nightly backup.sh, weekly restore-drill.sh (local time, TZ).
# Runs in the postgres image so pg_dump/pg_restore match the server version.
set -eu
BACKUP_AT="${BACKUP_AT:-01:45}"
DRILL_AT="${DRILL_AT:-04:00}"   # Sundays
echo "backup loop: daily at $BACKUP_AT, restore drill Sundays at $DRILL_AT ($(date +%Z))"
last=""
while true; do
  now="$(date +%H:%M)"
  if [ "$now" != "$last" ]; then
    last="$now"
    if [ "$now" = "$BACKUP_AT" ]; then /deploy/backup.sh || echo "backup FAILED"; fi
    if [ "$now" = "$DRILL_AT" ] && [ "$(date +%u)" = "7" ]; then /deploy/restore-drill.sh || echo "restore drill FAILED"; fi
  fi
  sleep 20
done
