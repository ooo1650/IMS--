#!/usr/bin/env bash
# Nightly backups (payment.md Step 2; design doc §8). Two kinds:
#
#  * Logical dump (pg_dump), every night: the long-term record. Every daily
#    dump is kept 90 days and the 1st-of-month dump ~7 years (6 required + margin).
#  * Physical base backup (pg_basebackup), weekly: together with the WAL
#    archive it allows point-in-time recovery (to any moment since the oldest
#    kept base backup; docs/DEPLOYMENT.md "Point-in-time recovery"). The newest
#    BASE_KEEP base backups are kept and older WAL is pruned, so the archive
#    can't grow without limit. A WAL archive without a base backup is useless.
#
# With BACKUP_PASSPHRASE set, every file is encrypted (gpg, AES-256) before it
# is stored; restore-drill.sh decrypts. With BACKUP_OFFSITE_DIR set (a mounted
# disk or share, still inside Nepal) new files are copied there too.
#
#   DATABASE_URL=postgres://... BACKUP_DIR=/srv/ims-backups deploy/backup.sh
#   BASE_BACKUP=force deploy/backup.sh     # take a base backup now, whatever the day
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-/srv/ims-backups}"
WAL_ARCHIVE="${WAL_ARCHIVE:-}"
BASE_KEEP="${BASE_KEEP:-2}"
BASE_DAY="${BASE_DAY:-7}"   # ISO weekday for the weekly base backup (7 = Sunday)
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/monthly" "$BACKUP_DIR/base"
umask 077

# seal FILE: optionally encrypt, then write FILE[.gpg].sha256. Prints the stored name.
seal() {
  local f="$1"
  if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
    gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 --output "$f.gpg" "$f" 3<<<"$BACKUP_PASSPHRASE"
    rm -f "$f"
    f="$f.gpg"
  fi
  (cd "$(dirname "$f")" && sha256sum "$(basename "$f")" > "$(basename "$f").sha256")
  echo "$f"
}

offsite() {  # offsite SUBDIR FILE...
  [ -n "${BACKUP_OFFSITE_DIR:-}" ] || return 0
  if [ ! -d "$BACKUP_OFFSITE_DIR" ]; then echo "WARNING: BACKUP_OFFSITE_DIR $BACKUP_OFFSITE_DIR is not mounted"; return 1; fi
  local sub="$1"; shift
  mkdir -p "$BACKUP_OFFSITE_DIR/$sub"
  cp -n "$@" "$BACKUP_OFFSITE_DIR/$sub/"
}

# --- Logical dump ---------------------------------------------------------------
stamp="$(date +%Y-%m-%d)"
file="$BACKUP_DIR/daily/ims-$stamp.dump"
pg_dump --format=custom --no-owner --file="$file.partial" "$DATABASE_URL"
mv "$file.partial" "$file"
stored="$(seal "$file")"
offsite daily "$stored" "$stored.sha256"

if [ "$(date +%d)" = "01" ]; then
  cp "$stored" "$stored.sha256" "$BACKUP_DIR/monthly/"
  offsite monthly "$stored" "$stored.sha256"
fi

# --- Weekly base backup + WAL pruning --------------------------------------------
latest_base="$(ls -1d "$BACKUP_DIR"/base/*/ 2>/dev/null | sort | tail -1 || true)"
if [ -n "$WAL_ARCHIVE" ] && { [ "${BASE_BACKUP:-}" = "force" ] || [ "$(date +%u)" = "$BASE_DAY" ] || [ -z "$latest_base" ]; }; then
  base="$BACKUP_DIR/base/$(date +%Y-%m-%dT%H%M)"
  rm -rf "$base.partial"
  # -X fetch: the WAL needed to make this copy consistent goes inside it, so a
  # base backup restores on its own; the archive only adds the time after it.
  pg_basebackup --dbname="$DATABASE_URL" --pgdata="$base.partial" --format=tar --gzip \
                --wal-method=fetch --checkpoint=fast --no-password
  # The first WAL file this backup needs: everything older can be pruned once
  # this is the oldest base backup kept.
  tar -xzOf "$base.partial/base.tar.gz" backup_label \
    | sed -n 's/^START WAL LOCATION: .*(file \([0-9A-F]\{24\}\)).*/\1/p' > "$base.partial/START_WAL"
  [ -s "$base.partial/START_WAL" ] || { echo "base backup: no START WAL in backup_label"; exit 1; }
  for f in "$base.partial"/*.tar.gz; do seal "$f" >/dev/null; done
  mv "$base.partial" "$base"
  offsite "base/$(basename "$base")" "$base"/*
  echo "base backup ok: $base"

  # Keep the newest BASE_KEEP; prune WAL older than the oldest one kept.
  ls -1d "$BACKUP_DIR"/base/*/ | sort | head -n -"$BASE_KEEP" | xargs -r rm -rf
  oldest="$(ls -1d "$BACKUP_DIR"/base/*/ | sort | head -1)"
  pg_archivecleanup -x .gz "$WAL_ARCHIVE" "$(cat "$oldest/START_WAL")"
  echo "WAL archive pruned before $(cat "$oldest/START_WAL") ($(ls -1 "$WAL_ARCHIVE" | wc -l) files left)"
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
echo "backup ok: $stored ($(du -h "$stored" | cut -f1))"
