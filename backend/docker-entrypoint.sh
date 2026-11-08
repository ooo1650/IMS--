#!/bin/sh
# One image, four roles (docker-compose.yml picks one per service):
#   web      gunicorn behind nginx (app DB role: row access only)
#   migrate  migrations + least-privilege role setup (DB owner credentials)
#   worker   periodic jobs: expiry, CBMS sync, reconciliations, auditor views
#   manage   any manage.py command, e.g. `docker compose run --rm migrate manage createsuperuser`
set -eu

case "${1:-web}" in
  web)
    # Only nginx can reach this port (internal network), so trusting its
    # forwarded headers is safe; Django reads X-Forwarded-Proto itself.
    exec gunicorn ims.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout 30 \
      --worker-tmp-dir /dev/shm \
      --forwarded-allow-ips '*' \
      --no-control-socket \
      --access-logfile - \
      --error-logfile -
    ;;
  migrate)
    python manage.py migrate --no-input
    python manage.py ensure_db_roles
    ;;
  worker)
    exec python manage.py run_scheduler
    ;;
  manage)
    shift
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
