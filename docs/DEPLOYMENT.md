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
| `backup` | `postgres:16.6` | `backup.sh` nightly at 01:45, `restore-drill.sh` Sundays 04:00. | `data` |

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
docker compose run --rm migrate manage create_platform_admin --username op --email you@example.com
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
| Copy backups off the VM | `docker run --rm -v ims_backups:/b -v "$PWD":/out alpine tar czf /out/ims-backups.tgz -C /b .` (keep the copy inside Nepal) |
| Health | `curl -fsS https://<IMS_DOMAIN>/api/v1/health` |

## Restore from a dump
```bash
docker compose stop backend worker
docker compose exec backup sh -c 'pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" /srv/ims-backups/daily/ims-YYYY-MM-DD.dump'
docker compose run --rm migrate          # re-applies role grants
docker compose start backend worker
```

## Operator console
A separate project (`console/`, built into the web image as `/srv/console`; or hosted on another server, see `console/README.md`) for the operator: businesses, branches, registers, admin accounts, shop rules, tax and FonePay settings, email, jobs, audit. Served on **port 8443** of `IMS_DOMAIN` (same certificate), or on its own subdomain with `IMS_CONSOLE_DOMAIN` (DNS must point there; certbot adds it to the certificate). Only networks in `IMS_CONSOLE_ALLOW` (default: private) can open it, and `/api/v1/platform/` answers 404 on the IMS address. Operators always sign in with an emailed code.

The console is its own project (`console/`, own `docker-compose.yml`), so it can also run elsewhere:
- **Same server, as a separate app** (to rehearse): `docker compose --profile console up -d --build` serves it on `:8081` through the IMS console endpoint; set `IMS_CONSOLE_ORIGIN=http://localhost:8081` to sign in.
- **Another server:** `cd console && cp .env.example .env && docker compose --profile https up -d --build`; on the IMS server set `IMS_CONSOLE_ORIGIN`, `IMS_CONSOLE_PROXY`, `IMS_CONSOLE_ALLOW` (see `console/README.md`).

## Settings worth knowing
- `IMS_ADMIN_ALLOW`: who may open `/django-admin` (CIDRs; default private networks). nginx must see real client IPs. Docker's default iptables port publishing preserves them; with `userland-proxy` everyone appears as a private address.
- FonePay is configured in the app (Admin → Branches & registers → FonePay): mode, API address and per-branch merchant credentials live in the database. Keep `IMS_ALLOW_FONEPAY_MOCK=false` on real servers; `IMS_FONEPAY_HOSTS` limits where the API address may point.
- Tills need **https** for USB/serial printers and the camera (Chrome/Edge). A local print service on a till must listen on `http://localhost`; LAN `http://` printer addresses are blocked as mixed content.

## Without Docker
`backend/.env.example`, `deploy/crontab`, `deploy/nginx/` (render `templates/ims.conf.template` with your values and provide the certificate yourself), and run `manage.py ensure_db_roles` as the DB owner after each `migrate`, with the app connecting as the role it creates.
