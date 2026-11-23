# Deployment (Docker)

One VM (in Nepal, for data residency) running Docker Engine 24+ with the Compose plugin. DNS for your domain must point at the VM, with ports 80 and 443 open.

## Services
| Service | Image | Role | Network |
|---|---|---|---|
| `db` | `postgres:16.6` | Database, WAL archiving on. No published port. | `data` (internal, no internet) |
| `migrate` | `ims-backend` | One-shot on every `up`: `migrate` + `ensure_db_roles`. Runs as the DB **owner**. | `data` |
| `backend` | `ims-backend` | gunicorn. Connects as **`ims_app`** (row access only). Read-only filesystem, non-root, no capabilities. | `data`, `edge` |
| `worker` | `ims-backend` | `run_scheduler`: sale expiry and CBMS sync every minute; FonePay reconciliation 02:30, stock reconciliation 03:00, auditor views 03:15 (Asia/Kathmandu). | `data`, `edge` |
| `web` | `ims-web` | nginx (`deploy/nginx/`): HTTPS, serves the SPA + admin static files and photos, proxies `/api`, security headers, rate limits (sign-in 30/min, POS 20/s, other API 50/s per IP), admin IP allowlist. Certificate: Let's Encrypt from `certbot`, else self-signed (`localhost`, LAN names, IPs). | `edge` (ports 80/443) |
| `certbot` | `certbot/certbot` | `deploy/certbot-loop.sh`: gets a Let's Encrypt certificate for a public `IMS_DOMAIN` (webroot challenge on port 80) and renews it; `web` installs new certificates within 5 minutes. Idle for non-public names. | `edge` |
| `backup` | `postgres:16.6` | `backup.sh` nightly at 01:45 (dump every night, base backup + WAL pruning on Sundays), `restore-drill.sh` Sundays 04:00. | `data` |

Volumes: `pgdata`, `walarchive`, `backups`, `letsencrypt` (TLS certificates and the ACME account; keep it), `acme` (challenge files), `media` (product photos: written by `backend`, served read-only by `web` at `/media/` with `immutable` caching, mirrored nightly into `backups/media`).

## First install
```bash
git clone … ims && cd ims
cp .env.example .env
# Fill in .env: IMS_DOMAIN (bare hostname: no http://, no port), LETSENCRYPT_EMAIL, DJANGO_SECRET_KEY, POSTGRES_PASSWORD, IMS_APP_DB_PASSWORD, CBMS_ENCRYPTION_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # for each secret
chmod 600 .env
docker compose up -d --build
docker compose ps                     # migrate: exited (0); others: running/healthy
docker compose run --rm migrate manage create_platform_admin --email you@example.com
# forgot an operator's password (and no reset email arrives): same command with --reset
# then open https://<IMS_DOMAIN>:8443 (the operator console): set up email (System settings),
# create each business with its first branch and admin; they get an emailed setup code
```
Then sign in at `https://<IMS_DOMAIN>`. Set up the organization, branches, registers, users, tax settings and CBMS credentials as in `docs/onboarding-checklist.md`.

## Upgrade
```bash
git pull
docker compose up -d --build          # migrate runs first; backend/worker wait for it
docker compose logs migrate
```
Take a manual backup first if the release has migrations: `docker compose exec backup /deploy/backup.sh`.

## Day to day
| Task | Command |
|---|---|
| Logs | `docker compose logs -f backend worker web` |
| Django command | `docker compose run --rm migrate manage <command>` (owner) or `docker compose exec backend python manage.py <command>` (app role) |
| Manual backup | `docker compose exec backup /deploy/backup.sh` |
| Restore drill now | `docker compose exec backup /deploy/restore-drill.sh` |
| Copy backups off the VM (one-off) | `docker run --rm -v ims_backups:/b -v "$PWD":/out alpine tar czf /out/ims-backups.tgz -C /b .` (keep the copy inside Nepal; for nightly copies set `BACKUP_OFFSITE_DIR`, see Backups) |
| Health | `curl -fsS https://<IMS_DOMAIN>/api/v1/health` |

## Backups
What `deploy/backup.sh` keeps in the `backups` volume:

| What | When | Kept | Used for |
|---|---|---|---|
| `daily/ims-YYYY-MM-DD.dump[.gpg]` (pg_dump) | every night | 90 days | restoring a whole day; the weekly drill |
| `monthly/…` (copy of the 1st) | 1st of the month | ~7 years | the 6-year record-keeping rule |
| `base/<date>/base.tar.gz[.gpg]` (pg_basebackup) | Sundays (and the first run) | newest 2 | point-in-time recovery, with the `walarchive` volume |

* **Encryption:** set `BACKUP_PASSPHRASE` in `.env` and every new file is gpg-encrypted (AES-256). Keep the passphrase off this server (password manager, sealed envelope). The restore drill decrypts the newest dump each week, so a wrong or lost passphrase shows up within a week.
* **Off-site copy:** mount a second disk or network share into the `backup` service and set `BACKUP_OFFSITE_DIR` to that path; new dumps and base backups are copied there each night. Example `docker-compose.override.yml`:
  ```yaml
  services:
    backup:
      volumes: ["/mnt/nas/ims-backups:/offsite"]
  ```
  with `BACKUP_OFFSITE_DIR=/offsite` in `.env`. Keep the copy inside Nepal.
* **WAL archive:** the `db` service gzips each WAL file into `walarchive` (at least every 10 minutes); the Sunday base backup prunes WAL older than the oldest base backup kept, so the volume stays bounded (about two weeks of changes). Check it is working: `docker compose exec db psql -U ims -d ims -c "select archived_count, failed_count from pg_stat_archiver"` (`failed_count` should stay 0).
* Take a base backup now (e.g. right after setting this up): `docker compose exec -e BASE_BACKUP=force backup /deploy/backup.sh`.
* **Installs from before 2026-11-09:** WAL archiving never worked (the archive volume was owned by root, so every `archive_command` failed and `pg_wal` kept growing). After `docker compose up -d` with this version, check `pg_stat_archiver` shows new archived files, then take a base backup as above.

## Restore from a dump
```bash
docker compose stop backend worker
# Encrypted dumps: decrypt first (gpg asks for BACKUP_PASSPHRASE)
docker compose exec backup sh -c 'gpg -d -o /tmp/ims.dump /srv/ims-backups/daily/ims-YYYY-MM-DD.dump.gpg'
docker compose exec backup sh -c 'pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" /tmp/ims.dump'
docker compose run --rm migrate          # re-applies role grants
docker compose start backend worker
```

## Point-in-time recovery
Brings the database back to a moment (e.g. just before a bad import), from the newest base backup taken *before* that moment plus the WAL archive. WAL is archived at least every 10 minutes (`archive_timeout`), so `latest` can miss up to the last 10 minutes. Tested end to end on 2026-11-09 in a local staging run (encrypted base backup, to a target time and to `latest`).
```bash
docker compose stop backend worker web db
# 1. Prepare a recovered copy in a NEW volume (the damaged one is left as it is).
docker volume create ims_pgdata_recovered
docker run --rm -v ims_backups:/b:ro -v ims_pgdata_recovered:/d -v "$PWD/deploy":/deploy:ro \
  -e BACKUP_PASSPHRASE --entrypoint /deploy/pitr-prepare.sh postgres:16.6 \
  /b/base/<date> "2026-11-09 14:05:00+05:45" /d/data        # or: ... /b/base/<date> latest /d/data
# 2. Start it on its own and check the data.
docker run -d --name ims-recovered -e PGDATA=/var/lib/postgresql/data/data \
  -v ims_pgdata_recovered:/var/lib/postgresql/data -v ims_walarchive:/wal_archive:ro postgres:16.6
docker logs -f ims-recovered          # wait for "archive recovery complete"
docker exec -it ims-recovered psql -U ims -d ims
```
`ls` the backups volume to pick `<date>` (`docker run --rm -v ims_backups:/b alpine ls /b/base`); the target must be after that base backup and inside the archived WAL, or Postgres stops with "recovery ended before configured recovery target was reached". When the data is right, stop `ims-recovered` and make it the live database: either dump it and restore that dump into `db` (see "Restore from a dump"), or point the `db` service's `pgdata` volume at `ims_pgdata_recovered` with `PGDATA=/var/lib/postgresql/data/data`. Then `docker compose up -d` and take a fresh base backup (`BASE_BACKUP=force`).

## Operator console
A separate project (`console/`, built into the web image as `/srv/console`; or hosted on another server, see `console/README.md`) for the operator: businesses, branches, registers, admin accounts, shop rules, tax and FonePay settings, email, jobs, audit. Served on **port 8443** of `IMS_DOMAIN` (same certificate), or on its own subdomain with `IMS_CONSOLE_DOMAIN` (DNS must point there; certbot adds it to the certificate). Only networks in `IMS_CONSOLE_ALLOW` (default: private) can open it, and `/api/v1/platform/` answers 404 on the IMS address. Operators always sign in with an emailed code.

The console is its own project (`console/`, own `docker-compose.yml`), so it can also run elsewhere:
- **Same server, as a separate app** (to rehearse): `docker compose --profile console up -d --build` serves it on `:8081` through the IMS console endpoint; set `IMS_CONSOLE_ORIGIN=http://localhost:8081` to sign in.
- **Another server:** `cd console && cp .env.example .env && docker compose --profile https up -d --build`; on the IMS server set `IMS_CONSOLE_ORIGIN`, `IMS_CONSOLE_PROXY`, `IMS_CONSOLE_ALLOW` (see `console/README.md`).

## Settings worth knowing
- `IMS_ADMIN_ALLOW`: who may open `/django-admin` (CIDRs; default private networks). nginx must see real client IPs. Docker's default iptables port publishing preserves them; with `userland-proxy` everyone appears as a private address.
- FonePay is configured in the app (Admin → Branches & registers → FonePay): mode, API address and per-branch merchant credentials live in the database. Keep `IMS_ALLOW_FONEPAY_MOCK=false` on real servers; `IMS_FONEPAY_HOSTS` limits where the API address may point.
- Tills need **https** for USB/serial printers and the camera (Chrome/Edge). A local print service on a till must listen on `http://localhost`; LAN `http://` printer addresses are blocked as mixed content.

## Tablet customer display
It needs the table from migration `sales/0008` (hosted: see *Updating the database* below). To check, run `curl -H 'X-Display-Token: test' https://<the app's address>/api/v1/display/state`: `401 display_unpaired` means ready, `500 db_outdated` means the migration is missing. How it works: `docs/ARCHITECTURE.md` §4.8.

Tablets open `https://<the app's address>/display`; plain `http://` redirects there, and no port has to be opened.
- The address must be one the tablet can reach: with `IMS_DOMAIN=localhost` it can't (port 80 redirects to `https://localhost`). On a LAN-only install set `IMS_DOMAIN` to the server's LAN IP or name and accept the self-signed certificate on the tablet once.
- Each tablet makes roughly 700–2,400 requests an hour while showing. On Vercel's Hobby plan count on about one always-on tablet before the monthly request allowance matters; on Render's free plan a showing tablet keeps the instance awake, using its monthly hours.

## Without Docker
`backend/.env.example`, `deploy/crontab`, `deploy/nginx/` (render `templates/ims.conf.template` with your values and provide the certificate yourself), and run `manage.py ensure_db_roles` as the DB owner after each `migrate`, with the app connecting as the role it creates.

## Hosted split: Vercel (frontends) + Render (backend) + Supabase (database)

No nginx here, so the backend and `vercel.json` take over its jobs.

**Why the frontends proxy `/api`:** sign-in uses a session cookie and CSRF, so
the browser must see the app and its API on one origin. `frontend/vercel.json`
and `console/vercel.json` rewrite `/api/*` (and `/media/*` for product photos)
to the Render service and send the same security headers as nginx. Replace
`https://YOUR-BACKEND.onrender.com` in both files with the Render URL.

**Supabase**
* Use the **Session pooler** connection (Connect → Session pooler, port 5432, IPv4;
  the direct host is IPv6-only and Render can't reach it). The transaction pooler
  (port 6543) also works: the backend detects the port and turns off prepared
  statements, server-side cursors and the scheduler's session locks (run only one
  scheduler then). Run migrations over 5432 (`IMS_MIGRATE_DATABASE_URL`).
* Pooler usernames carry the project ref: `postgres.<ref>` (owner) and `ims_app.<ref>` (app role).
* Supabase's Data API exposes the `public` schema. `ensure_db_roles` removes the
  `anon`/`authenticated` grants, but a deploy only runs it when
  `IMS_MIGRATE_DATABASE_URL` is set (otherwise the update script below removes them).
  Also turn the Data API off
  (Project Settings → Data API) since IMS never uses it.

**Render** (Web Service, Docker, root directory `backend`, health check path `/api/v1/health`):

| Variable | Value |
|---|---|
| `DJANGO_SECRET_KEY` | long random string |
| `DJANGO_ALLOWED_HOSTS` | `<service>.onrender.com,<app>.vercel.app,<console>.vercel.app` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://<app>.vercel.app,https://<console>.vercel.app` |
| `DATABASE_URL` | `postgresql://ims_app.<ref>:<IMS_APP_DB_PASSWORD>@<pooler-host>:5432/postgres?sslmode=require` |
| `IMS_MIGRATE_DATABASE_URL` | same, with `postgres.<ref>` and the Supabase database password: with it set, migrations + `ensure_db_roles` run at start; without it neither does |
| `IMS_APP_DB_PASSWORD` | 16+ random characters (sets the `ims_app` password) |
| `CBMS_ENCRYPTION_KEY` | Fernet key; keep it forever |
| `IMS_PLATFORM_ORIGINS` | `https://<console>.vercel.app`: only the console may call `/api/v1/platform/` |
| `IMS_PLATFORM_ALLOW` | optional: operators' IPs/CIDRs |
| `IMS_TRUSTED_PROXIES` | `2` (Vercel, then Render). Check the IPs in the console's audit log are real visitor IPs |
| `IMS_SERVE_MEDIA` | `true` (Django serves product photos) |
| `IMS_RUN_SCHEDULER` | `true` (scheduled jobs run inside the web service) |

**Updating the database.** Render applies migrations at start **only if `IMS_MIGRATE_DATABASE_URL` is set** (that also needs `IMS_APP_DB_PASSWORD`, and resets `ims_app` to that password). Without it a deploy brings new code but no new tables: the API answers `500 db_outdated` and the log shows a WARNING at start. Update from your own computer, with the repository at the deployed commit:

```bash
cd backend && .venv/bin/python scripts/update_hosted_db.py 'postgresql://postgres.<ref>@<pooler-host>:5432/postgres'
```
It asks for the database password (hidden), shows the plan, waits for `yes`, migrates, gives `ims_app` access without changing its password, and removes the Data API roles' access.

**Operator account on the hosted database** (first operator, or a forgotten password when no reset email arrives):
```bash
cd backend && .venv/bin/python scripts/hosted_operator.py 'postgresql://postgres.<ref>@<pooler-host>:5432/postgres' --email you@example.com [--reset]
```
Both the database password and the operator's new password are asked for, hidden. Signing in still needs the emailed code, so email (SMTP in the console's System settings) must work.

Limits of this setup:
* Render's free plan sleeps after 15 idle minutes (first request ~1 min; jobs don't run while asleep) and its disk is wiped on every deploy, so **product photos are lost** unless a Render persistent disk is mounted at `/srv/media` (paid).
* The proxy can be challenged: Render's edge (Cloudflare) sees the proxy's shared addresses, not the shop's. Seen 2026-11-22:
  `/api` calls through the shop app got an HTML `429` with `cf-mitigated: challenge` while Render answered directly;
  it cleared by itself after the checks began, and can come back. The app shows "Too many requests". It is not the sign-in lockout (that is JSON with a countdown) and no setting in IMS
  lifts it. Check with `curl -i https://<app>.vercel.app/api/v1/health`; if it lasts, send the `cf-ray` and time to Render support.
* No `pg_dump` backup service: use Supabase's backups.
* `IMS_PLATFORM_ORIGINS`/`IMS_PLATFORM_ALLOW` keep browsers on the IMS address away from the console API, but a script calling Render directly can fake those headers. The real protection is the operator's password plus emailed code.
* `/django-admin` isn't proxied and has no static files here; use the console.

**Fresh database from one SQL file** (e.g. pasted into Supabase's SQL editor):
`cd backend && .venv/bin/python scripts/export_schema.py --email you@example.com`
writes `backend/db/ims-initial.sql` (git-ignored, contains a password hash): the
full schema, triggers and auditor views, the seeded tax categories and default
settings, and one operator account. No demo data. Load it into an empty
database, then let `ensure_db_roles` run (Render: `IMS_MIGRATE_DATABASE_URL`).
