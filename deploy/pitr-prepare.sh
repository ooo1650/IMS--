#!/usr/bin/env bash
# Prepare a data directory for point-in-time recovery (docs/DEPLOYMENT.md).
# Runs in the postgres image with the backups volume, the WAL archive and an
# EMPTY target directory mounted:
#
#   pitr-prepare.sh <base-backup-dir> "<target time, e.g. 2026-11-09 14:05:00+05:45>" <empty-dir>
#   pitr-prepare.sh <base-backup-dir> latest <empty-dir>     # everything in the archive
#
# WAL is archived at least every 10 minutes (archive_timeout), so "latest"
# can miss up to the last 10 minutes before the failure.
# Unpacks (and with BACKUP_PASSPHRASE decrypts) the base backup, then sets
# restore_command / recovery_target_time so Postgres replays the WAL archive
# (mounted at /wal_archive) up to that moment and opens the database.
set -euo pipefail
base="${1:?base backup directory, e.g. /b/base/2026-11-08T0145}"
target="${2:?recovery target time}"
out="${3:?empty output directory}"
[ -z "$(ls -A "$out" 2>/dev/null)" ] || { echo "$out is not empty"; exit 1; }
mkdir -p "$out"

if [ -f "$base/base.tar.gz.gpg" ]; then
  : "${BACKUP_PASSPHRASE:?this base backup is encrypted: set BACKUP_PASSPHRASE}"
  gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 --decrypt "$base/base.tar.gz.gpg" \
    3<<<"$BACKUP_PASSPHRASE" | tar -xz -C "$out"
else
  tar -xzf "$base/base.tar.gz" -C "$out"
fi
touch "$out/recovery.signal"
echo "restore_command = 'gunzip -c /wal_archive/%f.gz > %p'" >> "$out/postgresql.auto.conf"
if [ "$target" != "latest" ]; then
  # The target must be inside the archived WAL, or Postgres stops with
  # "recovery ended before configured recovery target was reached".
  printf "recovery_target_time = '%s'\nrecovery_target_action = 'promote'\n" "$target" >> "$out/postgresql.auto.conf"
fi
chown -R postgres:postgres "$out"
chmod 700 "$out"
echo "ready: $out (base $(basename "$base"), target $target)"
