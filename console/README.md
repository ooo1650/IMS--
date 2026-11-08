# IMS Console

The operator console for IMS: create businesses (each separately registered,
with its own PAN, catalog and invoices), their branches, registers and admin
accounts; set shop rules, tax/CBMS and FonePay per business; edit system
settings (email, sign-in rules); run maintenance jobs; read the audit log.

It is a **separate project** (own `package.json`, own build) so it can be
hosted on its own server. It holds no data: everything goes to the IMS backend's
`/api/v1/platform/...` API, which only operator accounts (`is_platform_admin`)
can use.

## Develop

```bash
npm install
npm run dev                         # http://localhost:5174, API -> http://127.0.0.1:8000 (runserver)
IMS_API=https://ims.example.com:8443 npm run dev   # or against a running IMS
```

Sign in as an operator (demo data: `operator` / `demo-pass-123`). Operators
always get an emailed sign-in code; on a test box `IMS_TEST_LOGIN=true` on the
server shows one-click test sign-in instead.

```bash
npm run build                       # static files in dist/
npm run sync-shared                 # refresh src/shared/ from ../frontend/src (same repo only)
```

`src/shared/` holds copies of the IMS app's design tokens, UI kit, icons and
API/money helpers, so this folder works on its own. Run `sync-shared` after
changing those files in the IMS app.

## Host it

The browser must see the console and its `/api` on **one origin** (sign-in uses
a session cookie and CSRF), so wherever the console runs, its web server serves
the static files **and forwards `/api/` to the IMS server's console endpoint**
(port 8443 on the IMS server, where `/api/v1/platform/` is allowed; the normal
IMS address answers 404 for it).

### A. On the IMS server (default)

Nothing to do: `deploy/web.Dockerfile` builds this folder and nginx serves it on
`https://<IMS_DOMAIN>:8443` (or `IMS_CONSOLE_DOMAIN`), limited to
`IMS_CONSOLE_ALLOW` networks.

### B. Separately, on this machine (to try it before moving it)

From the repository root:

```bash
docker compose --profile console up -d --build    # http://localhost:8081
```

It talks to the IMS console endpoint (`web:8443`) exactly like a remote console
would. Add `IMS_CONSOLE_ORIGIN=http://localhost:8081` to the root `.env` (and
`docker compose up -d`) to be able to sign in through it.

### C. On another server

Copy this folder to the server, then:

```bash
cp .env.example .env            # set IMS_API_URL (and CONSOLE_DOMAIN for HTTPS)
docker compose up -d --build                    # HTTP on CONSOLE_PORT (8080)
docker compose --profile https up -d --build    # + automatic HTTPS (Caddy) for CONSOLE_DOMAIN
```

The sign-in cookies are `Secure`, so use the `https` profile (or the host's
own HTTPS proxy in front of `CONSOLE_PORT`). Without Compose:
`docker build -t ims-console . && docker run -d -p 8080:8080 -e IMS_API_URL=https://ims.example.com:8443 ims-console`.

Then on the **IMS server's** `.env`, and `docker compose up -d`:

| Setting | Value |
|---|---|
| `IMS_CONSOLE_ORIGIN` | the console's public address, e.g. `https://console.example.com` (trusted for sign-in forms) |
| `IMS_CONSOLE_PROXY` | the console server's address(es) as seen by the IMS server, e.g. `203.0.113.10` |
| `IMS_CONSOLE_ALLOW` | the operators' own networks (their real addresses, not the proxy's) |

Port 8443 on the IMS server must be reachable from the console server. Because
of `IMS_CONSOLE_PROXY`, the IMS server reads the operator's real address from
`X-Forwarded-For`, so the allowlist, sign-in lockouts and the audit log work as
if the operator connected directly.

Any static host works too (S3 + CloudFront, a plain nginx...) as long as it
forwards `/api/` like `nginx/default.conf.template` does.
