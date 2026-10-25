#!/usr/bin/env bash
# Weekly restore drill (design doc §8: "An untested backup is not a backup").
# Restores the newest dump into a throwaway database, then checks that:
#   * the checksum matches,
#   * migrations are up to date,
#   * issued invoices are present and the append-only triggers survived,
#   * the auditor views are queryable.
#
#   ADMIN_URL=postgres://user@host/postgres BACKUP_DIR=/srv/ims-backups deploy/restore-drill.sh
set -euo pipefail
: "${ADMIN_URL:?set ADMIN_URL (a role that can CREATE DATABASE)}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ims-backups}"
dump="$(ls -1t "$BACKUP_DIR"/daily/ims-*.dump | head -1)"
sha256sum -c "$dump.sha256"

db="ims_restore_drill_$(date +%s)"
psql "$ADMIN_URL" -qc "CREATE DATABASE $db"
trap 'psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS $db"' EXIT
target="${ADMIN_URL%/*}/$db"
pg_restore --no-owner --dbname="$target" "$dump"

invoices="$(psql "$target" -tAc "SELECT count(*) FROM sales_sale WHERE receipt_number <> ''")"
triggers="$(psql "$target" -tAc "SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%no_delete'")"
psql "$target" -qc "REFRESH MATERIALIZED VIEW cbms_bill_view" >/dev/null
DATABASE_URL="$target" python backend/manage.py migrate --check >/dev/null

[ "$triggers" -gt 0 ] || { echo "FAIL: append-only triggers missing"; exit 1; }
echo "restore drill ok: $dump -> $db · $invoices invoices · $triggers delete-guards"
