"""Write a fresh-install SQL file: the full schema (tables, constraints,
append-only triggers, auditor views), the reference rows the migrations seed
(tax categories, default settings) and ONE operator (platform admin). No demo
data. Runnable in any SQL client, including Supabase's SQL editor (plain
INSERTs, no COPY or psql commands).

    python scripts/export_schema.py --email you@example.com [--username operator] [--out db/ims-initial.sql]

The password is asked for (or read from IMS_OPERATOR_PASSWORD); the file holds
only its hash. Built from the migrations on a throwaway embedded PostgreSQL
(pip: pgserver), so it always matches the code. After loading it into an EMPTY
database, run ``manage.py ensure_db_roles`` as the owner (the Docker migrate
service and IMS_MIGRATE_DATABASE_URL on Render do this) for the app's
least-privilege role; ``migrate`` then finds nothing to apply.
"""
import argparse
import getpass
import os
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import date
from pathlib import Path

import pgserver

backend = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
ap.add_argument("--email", required=True, help="the operator's email (sign-in codes go here)")
ap.add_argument("--username", default="operator")
ap.add_argument("--out", default=str(backend / "db" / "ims-initial.sql"))
args = ap.parse_args()

password = os.environ.get("IMS_OPERATOR_PASSWORD") or getpass.getpass("Operator password: ")
if not os.environ.get("IMS_OPERATOR_PASSWORD") and password != getpass.getpass("Again: "):
    sys.exit("The passwords don't match")

with tempfile.TemporaryDirectory(prefix="ims-pg-") as data:
    srv = pgserver.get_server(data, cleanup_mode="stop")
    socket_dir = urllib.parse.parse_qs(urllib.parse.urlparse(srv.get_uri()).query)["host"][0]
    env = {**os.environ, "DJANGO_DEBUG": "true", "IMS_OPERATOR_PASSWORD": password,
           "DATABASE_URL": f"postgres://postgres@{urllib.parse.quote(socket_dir, safe='')}/postgres"}
    for k in [k for k in env if k.startswith(("EMAIL_", "IMS_CASH", "IMS_CASHIER", "IMS_RETURN", "IMS_2FA"))]:
        del env[k]  # the migration that imports old .env values must see none
    manage = [sys.executable, "manage.py"]
    subprocess.run([*manage, "migrate", "--no-input", "-v", "0"], cwd=backend, env=env, check=True)
    subprocess.run([*manage, "shell", "-c", f"""
import os
from accounts.models import User
from django.contrib.auth.password_validation import validate_password
u = User(username={args.username!r}, email={args.email.strip().lower()!r}, first_name="Operator",
         is_platform_admin=True, email_verified=True)
validate_password(os.environ["IMS_OPERATOR_PASSWORD"], u)
u.set_password(os.environ["IMS_OPERATOR_PASSWORD"])
u.save()
"""], cwd=backend, env=env, check=True)
    pg_dump = Path(pgserver.__file__).parent / "pginstall" / "bin" / "pg_dump"
    dump = subprocess.run([str(pg_dump), "--no-owner", "--no-privileges", "--no-comments", "--column-inserts",
                           "-h", socket_dir, "-U", "postgres", "postgres"],
                          check=True, capture_output=True, text=True).stdout
    srv.cleanup()

# psql-only meta-commands (newer pg_dump) don't run in SQL editors.
lines = [ln for ln in dump.splitlines() if not ln.startswith("\\")]
out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(f"""-- IMS fresh install, generated {date.today()} by backend/scripts/export_schema.py
-- Schema + seeded reference rows + one operator ({args.username}, {args.email}). No demo data.
-- Load into an EMPTY database as its owner, then run `manage.py ensure_db_roles`.
-- Contains a password hash: keep this file private.
BEGIN;
""" + "\n".join(lines) + "\nCOMMIT;\n")
os.chmod(out, 0o600)
print(f"Wrote {out}")
