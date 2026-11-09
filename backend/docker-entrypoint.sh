#!/bin/sh
# One image, four roles (docker-compose.yml picks one per service):
#   web      gunicorn behind nginx (app DB role: row access only)
#   migrate  migrations + least-privilege role setup (DB owner credentials)
#   worker   periodic jobs: expiry, CBMS sync, reconciliations, auditor views
#   manage   any manage.py command, e.g. `docker compose run --rm migrate manage createsuperuser`
set -eu

case "${1:-web}" in
  web)
    # Single-service hosts (e.g. Render, docs/DEPLOYMENT.md) have no separate
    # migrate/worker containers:
    #   IMS_MIGRATE_DATABASE_URL  the DB owner's URL: migrate + app role first
    #   IMS_RUN_SCHEDULER=true    run the scheduled jobs in this container too
    if [ -n "${IMS_MIGRATE_DATABASE_URL:-}" ]; then
      DATABASE_URL="$IMS_MIGRATE_DATABASE_URL" python manage.py migrate --no-input
      DATABASE_URL="$IMS_MIGRATE_DATABASE_URL" python manage.py ensure_db_roles
    fi
    if [ "${IMS_RUN_SCHEDULER:-false}" = "true" ]; then
      python manage.py run_scheduler &
    fi
    # Threads: a FonePay status poll waits on the bank (up to 10 s); with plain
    # sync workers three tills waiting on QR payments would block every worker.
    # Only the proxy in front can reach this port, so trusting its forwarded
    # headers is safe; Django reads X-Forwarded-Proto itself. PORT: set by
    # hosts like Render; 8000 in Docker Compose.
    exec gunicorn ims.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${GUNICORN_WORKERS:-3}" \
      --worker-class gthread \
      --threads "${GUNICORN_THREADS:-8}" \
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
