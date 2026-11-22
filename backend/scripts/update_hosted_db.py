"""Bring a hosted database (Supabase) up to this code, from your own computer.

For installs where the host doesn't migrate at start (no IMS_MIGRATE_DATABASE_URL):

    cd backend
    .venv/bin/python scripts/update_hosted_db.py 'postgresql://postgres.<ref>@<pooler-host>:5432/postgres'

The URL carries no password: it is asked for, hidden, and never written or
passed on a command line. The script shows what it will do and changes nothing
until you type "yes". Then it

* applies the pending migrations (and refuses ones that rewrite data or run
  raw SQL: those need a person to look first);
* lets the app's role, if there is one, use the owner's tables. Its password is
  left alone, unlike ensure_db_roles, so the live DATABASE_URL keeps working;
* removes the Supabase Data API roles' access to those tables, present and
  future (IMS doesn't use that API, and its key is public);
* checks the result.
"""
import getpass
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import psycopg
from psycopg import conninfo, sql

BACKEND = Path(__file__).resolve().parent.parent
APP_ROLE = os.environ.get("IMS_APP_DB_USER", "ims_app")
API_ROLES = ("anon", "authenticated")


def stop(message):
    print(f"\nSTOPPED: {message}")
    sys.exit(1)


def target(url):
    try:
        info = conninfo.conninfo_to_dict(url)
    except psycopg.Error:
        stop("that isn't a postgresql:// address.")
    if info.get("password"):
        stop("leave the password out of the address: it is asked for, so it stays out of your shell history.")
    if not info.get("host") or not info.get("user") or not info.get("dbname"):
        stop("the address needs a user, a host and a database name.")
    if info["user"].split(".")[0] == APP_ROLE:
        stop("use the owner (postgres.<ref>): the app's role can't change tables.")
    if str(info.get("port", "")) == "6543":
        stop("use the Session pooler, port 5432: 6543 can't run migrations safely.")
    if not info["host"].startswith("/") and info["host"] != "localhost":
        info.setdefault("sslmode", "require")
    return info


def django_url(info):
    port = f":{info['port']}" if info.get("port") else ""
    query = f"?{urlencode({'sslmode': info['sslmode']})}" if info.get("sslmode") else ""
    return (f"postgresql://{quote(info['user'], safe='')}:{quote(info['password'], safe='')}"
            f"@{quote(info['host'], safe='.')}{port}/{info['dbname']}{query}")


def manage(env, *args):
    return subprocess.run([sys.executable, "manage.py", *args], cwd=BACKEND, env=env, capture_output=True, text=True)


def owned(cur):
    """(name, kind) of the current role's tables, views and sequences in public."""
    cur.execute("""SELECT c.relname, c.relkind::text FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relkind::text IN ('r', 'p', 'v', 'm', 'S')
                     AND pg_get_userbyid(c.relowner) = current_user ORDER BY 1""")
    return cur.fetchall()


def existing_roles(cur, names):
    cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", [list(names)])
    return [name for (name,) in cur.fetchall()]


def api_readable(cur, api):
    if not api:
        return []
    cur.execute("""SELECT DISTINCT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace,
                          unnest(%s::text[]) r
                   WHERE n.nspname = 'public' AND c.relkind::text IN ('r', 'p', 'v', 'm')
                     AND NOT c.relrowsecurity
                     AND has_table_privilege(r, c.oid, 'SELECT') ORDER BY 1""", [api])
    return [name for (name,) in cur.fetchall()]


def close_app_owned(cur, api):
    """ensure_db_roles gives the app's role the auditor views, and only an
    object's owner can revoke on it: act as that role, inside the transaction,
    and hand back any membership borrowed to do so."""
    cur.execute("""SELECT c.relname FROM pg_class c
                   WHERE c.relnamespace = 'public'::regnamespace AND c.relkind::text IN ('r', 'p', 'v', 'm')
                     AND pg_get_userbyid(c.relowner) = %s""", [APP_ROLE])
    names = [name for (name,) in cur.fetchall()]
    if not names:
        return
    # From Postgres 16 a member needs the SET option as well.
    need = "SET" if cur.connection.info.server_version >= 160000 else "MEMBER"
    cur.execute("SELECT pg_has_role(current_user, %s, %s)", [APP_ROLE, need])
    borrowed = not cur.fetchone()[0]
    app = sql.Identifier(APP_ROLE)
    if borrowed:
        cur.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(app))
    cur.execute(sql.SQL("SET LOCAL ROLE {}").format(app))
    for name in names:
        for role in api:
            cur.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(sql.Identifier("public", name), sql.Identifier(role)))
    cur.execute("RESET ROLE")
    if borrowed:
        cur.execute(sql.SQL("REVOKE {} FROM CURRENT_USER").format(app))


def set_access(cur, app, api):
    cur.execute("SELECT current_user")
    owner = sql.Identifier(cur.fetchone()[0])
    for name, kind in owned(cur):
        thing = sql.SQL("SEQUENCE {}" if kind == "S" else "TABLE {}").format(sql.Identifier("public", name))
        for role in api:
            cur.execute(sql.SQL("REVOKE ALL ON {} FROM {}").format(thing, sql.Identifier(role)))
        if app:
            rights = sql.SQL("USAGE, SELECT" if kind == "S" else "SELECT, INSERT, UPDATE, DELETE")
            cur.execute(sql.SQL("GRANT {} ON {} TO {}").format(rights, thing, sql.Identifier(APP_ROLE)))
    if app and api:
        try:
            with cur.connection.transaction():  # a savepoint: this failing must not undo the grants above
                close_app_owned(cur, api)
        except psycopg.errors.InsufficientPrivilege:
            print(f"(Couldn't act as {APP_ROLE} to close its own views: the check below lists any still open.)")
    for role in api:
        for kind in ("TABLES", "SEQUENCES"):
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON {} FROM {}")
                        .format(owner, sql.SQL(kind), sql.Identifier(role)))
    if app:
        for kind, rights in (("TABLES", "SELECT, INSERT, UPDATE, DELETE"), ("SEQUENCES", "USAGE, SELECT")):
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT {} ON {} TO {}")
                        .format(owner, sql.SQL(rights), sql.SQL(kind), sql.Identifier(APP_ROLE)))


def main():
    if len(sys.argv) != 2:
        stop("give the database address, without the password. See the top of this file.")
    info = target(sys.argv[1])
    print(f"Database: {info['user']} at {info['host']}")
    try:
        password = getpass.getpass("Database password (paste with Ctrl+Shift+V, nothing will show): ").strip()
    except (KeyboardInterrupt, EOFError):
        stop("cancelled. Nothing was changed.")
    if not password:
        stop("no password received. Nothing was changed.")
    info["password"] = password
    print(f"Received {len(password)} characters. Connecting...")
    try:
        db = psycopg.connect(**info, autocommit=True, connect_timeout=15)
    except psycopg.OperationalError as e:
        reason = str(e).strip().splitlines()[-1]
        stop(f"couldn't connect ({reason}). Nothing was changed. The password is the Supabase database password "
             "(Project Settings > Database), not your Supabase login. Don't retry many times: Supabase blocks repeats.")

    env = {**os.environ, "DATABASE_URL": django_url(info)}
    with db, db.cursor() as cur:
        cur.execute("SELECT to_regclass('public.django_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            stop("this database has no IMS tables. Is it the right project? Nothing was changed.")
        cur.execute("SELECT count(*) FROM django_migrations WHERE app = 'sales'")
        if not cur.fetchone()[0]:
            stop("this database doesn't record IMS's migrations. Nothing was changed: ask before going further.")

        plan = manage(env, "migrate", "--plan")
        if plan.returncode:
            stop("couldn't work out the update. Nothing was changed.\n" + plan.stderr[-600:])
        print("\n" + plan.stdout.strip())
        if "Raw Python" in plan.stdout or "Raw SQL" in plan.stdout:
            stop("this update rewrites data or runs raw SQL, so it needs a look first. Nothing was changed.")

        app = bool(existing_roles(cur, [APP_ROLE]))
        api = existing_roles(cur, API_ROLES)
        exposed = api_readable(cur, api)
        print(f"\nApp role {APP_ROLE}: {'exists, will be given access' if app else 'none (the app connects as the owner)'}")
        if exposed:
            print(f"WARNING: {len(exposed)} table(s) can be read with Supabase's public Data API key. This will close that.")
        try:
            answer = input('\nType "yes" to apply: ')
        except (KeyboardInterrupt, EOFError):
            answer = ""
        if answer.strip().lower() != "yes":
            stop("cancelled. Nothing was changed.")

        done = manage(env, "migrate", "--no-input")
        if done.returncode:
            stop("the update failed.\n" + done.stderr[-800:])
        print(done.stdout.strip())
        with db.transaction():
            set_access(cur, app, api)

        problems = []
        if manage(env, "migrate", "--check").returncode:
            problems.append("some migrations are still pending")
        cur.execute("""SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public' AND c.relkind = 'r'""")
        for name, row_security in cur.fetchall():
            if not app:
                break
            cur.execute("SELECT has_table_privilege(%s, %s::regclass, 'INSERT')",
                        [APP_ROLE, sql.Identifier("public", name).as_string(db)])
            if not cur.fetchone()[0]:
                problems.append(f"{APP_ROLE} can't write to {name}")
            elif row_security:
                problems.append(f"{name} has row-level security on, which hides its rows from {APP_ROLE}")
        left = api_readable(cur, api)
        if left:
            problems.append(f"still readable through the Data API: {', '.join(left)}. The tables are updated and the "
                            "app works; to close those, turn the Data API off in Supabase (Project Settings > Data API)")
        if problems:
            stop("updated, but: " + "; ".join(problems[:8]))
    print("\nDONE: the database is up to date." + (f" Closed Data API access to {len(exposed)} table(s)." if exposed else ""))


if __name__ == "__main__":
    main()
