#!/usr/bin/env bash
# Weekly restore drill (design doc §8: "An untested backup is not a backup").
# Restores the newest dump into a throwaway database, then checks that:
#   * the checksum matches (and an encrypted dump opens with BACKUP_PASSPHRASE),
#   * migrations are up to date,
#   * issued invoices are present and the append-only triggers survived,
#   * the auditor views are queryable.
#
#   ADMIN_URL=postgres://user@host/postgres BACKUP_DIR=/srv/ims-backups deploy/restore-drill.sh
set -euo pipefail
: "${ADMIN_URL:?set ADMIN_URL (a role that can CREATE DATABASE)}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ims-backups}"
dump="$(ls -1t "$BACKUP_DIR"/daily/ims-*.dump "$BACKUP_DIR"/daily/ims-*.dump.gpg 2>/dev/null | head -1 || true)"
[ -n "$dump" ] || { echo "FAIL: no dump in $BACKUP_DIR/daily"; exit 1; }
(cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256")

db="ims_restore_drill_$(date +%s)"
work="$(mktemp -d)"
psql "$ADMIN_URL" -qc "CREATE DATABASE $db"
trap 'psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS $db"; rm -rf "$work"' EXIT
target="${ADMIN_URL%/*}/$db"
if [ "${dump%.gpg}" != "$dump" ]; then
  # Encrypted (BACKUP_PASSPHRASE): proves the passphrase still opens it.
  : "${BACKUP_PASSPHRASE:?the newest dump is encrypted: set BACKUP_PASSPHRASE}"
  gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 --decrypt --output "$work/ims.dump" "$dump" \
    3<<<"$BACKUP_PASSPHRASE"
  pg_restore --no-owner --dbname="$target" "$work/ims.dump"
else
  pg_restore --no-owner --dbname="$target" "$dump"
fi

invoices="$(psql "$target" -tAc "SELECT count(*) FROM sales_sale WHERE receipt_number <> ''")"
triggers="$(psql "$target" -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%no_delete'")"
psql "$target" -qc "REFRESH MATERIALIZED VIEW cbms_bill_view" >/dev/null
# Schema matches the code (skipped in the Docker backup container, which has no Python).
if command -v python >/dev/null && [ -f backend/manage.py ]; then
  DATABASE_URL="$target" python backend/manage.py migrate --check >/dev/null
fi

[ "$triggers" -gt 0 ] || { echo "FAIL: append-only triggers missing"; exit 1; }
echo "restore drill ok: $dump -> $db · $invoices invoices · $triggers delete-guards"
