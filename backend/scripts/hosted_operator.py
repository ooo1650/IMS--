"""Create a console operator, or reset a forgotten operator password, on the
hosted database (Supabase), from your own computer:

    cd backend
    .venv/bin/python scripts/hosted_operator.py 'postgresql://postgres.<ref>@<pooler-host>:5432/postgres' --email you@example.com
    .venv/bin/python scripts/hosted_operator.py '<same address>' --email you@example.com --reset

Neither password goes on the command line: the database password and the
operator's new password are both asked for, hidden. Use the address of the
database the server really uses (Render → DATABASE_URL: same project id and host).
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_hosted_db import BACKEND, django_url, stop, target  # noqa: E402


def main():
    if len(sys.argv) < 3 or "--email" not in sys.argv:
        stop("give the database address (no password) and --email. See the top of this file.")
    info = target(sys.argv[1])
    extra = sys.argv[2:]
    if any(a == "--password" or a.startswith("--password=") for a in extra):
        stop("leave --password out: it is asked for, so it stays out of your shell history.")
    print(f"Database: {info['user']} at {info['host']}")
    try:
        password = getpass.getpass("Database password (paste with Ctrl+Shift+V, nothing will show): ").strip()
    except (KeyboardInterrupt, EOFError):
        stop("cancelled. Nothing was changed.")
    if not password:
        stop("no password received. Nothing was changed.")
    info["password"] = password
    try:
        with psycopg.connect(**info, connect_timeout=15) as db:
            if not db.execute("SELECT to_regclass('public.accounts_user') IS NOT NULL").fetchone()[0]:
                stop("this database has no IMS tables. Is it the right project? Nothing was changed.")
    except psycopg.OperationalError as e:
        stop(f"couldn't connect ({str(e).strip().splitlines()[-1]}). Nothing was changed. The password is the "
             "Supabase database password (Project Settings > Database), not your Supabase login.")
    env = {**os.environ, "DATABASE_URL": django_url(info)}
    # Not captured: the command asks for the operator's new password on this terminal.
    done = subprocess.run([sys.executable, "manage.py", "create_platform_admin", *extra], cwd=BACKEND, env=env)
    sys.exit(done.returncode)


if __name__ == "__main__":
    main()
