#!/usr/bin/env bash
# Nightly logical backup with 6-year retention (payment.md Step 2; design doc §8).
# Keeps: every daily dump for 90 days, and the 1st-of-month dump for 7 years
# (6 years required + margin). WAL archiving (docker-compose) gives point-in-time
# recovery between dumps. Copy BACKUP_DIR off the server too — still inside Nepal.
#
#   DATABASE_URL=postgres://... BACKUP_DIR=/srv/ims-backups deploy/backup.sh
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ims-backups}"
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly"

stamp="$(date +%Y-%m-%d)"
file="$BACKUP_DIR/daily/ims-$stamp.dump"
pg_dump --format=custom --no-owner --file="$file.partial" "$DATABASE_URL"
mv "$file.partial" "$file"
sha256sum "$file" > "$file.sha256"

if [ "$(date +%d)" = "01" ]; then
  cp "$file" "$file.sha256" "$BACKUP_DIR/monthly/"
fi

# Product photos: file names are content hashes, so copying only new files
# (no-clobber) keeps a complete mirror. Not tax records; no retention needed.
if [ -n "${MEDIA_DIR:-}" ] && [ -d "$MEDIA_DIR" ]; then
  mkdir -p "$BACKUP_DIR/media"
  cp -Rn "$MEDIA_DIR/." "$BACKUP_DIR/media/"
fi

# Retention: daily 90 days, monthly ~7 years. Never prune anything newer.
find "$BACKUP_DIR/daily" -name 'ims-*.dump*' -mtime +90 -delete
find "$BACKUP_DIR/monthly" -name 'ims-*.dump*' -mtime +2557 -delete
echo "backup ok: $file ($(du -h "$file" | cut -f1))"
