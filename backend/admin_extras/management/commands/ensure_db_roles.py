"""Least-privilege database role for the running app (PostgreSQL only).

Run as the database owner after ``migrate`` (the Docker ``migrate`` service does).
Creates/updates the app login and grants it row access only:

* SELECT/INSERT/UPDATE/DELETE on all tables, now and in future migrations;
* no TRUNCATE, no ownership: it can't drop tables, alter them, or
  ``DISABLE TRIGGER``, so the append-only triggers hold even if the app's
  credentials leak;
* owns the two auditor materialized views so the nightly REFRESH works.

Idempotent; safe on every deploy.
"""
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

MATVIEWS = ("cbms_bill_view", "cbms_return_view")


class Command(BaseCommand):
    help = "Create/refresh the least-privilege app database role (run as the DB owner)."

    def add_arguments(self, parser):
        parser.add_argument("--user", default=os.environ.get("IMS_APP_DB_USER", "ims_app"))

    def handle(self, *args, user, **opts):
        if connection.vendor != "postgresql":
            self.stdout.write("Not PostgreSQL; skipping role setup.")
            return
        password = os.environ.get("IMS_APP_DB_PASSWORD", "")
        if len(password) < 16:
            raise CommandError("Set IMS_APP_DB_PASSWORD (16+ characters) for the app database role.")
        from psycopg import sql

        with connection.cursor() as cur:
            cur.execute("SELECT current_user, current_database()")
            owner, db = cur.fetchone()
            if owner == user:
                raise CommandError("Run this as the database owner, not as the app role.")
            role, own, dbname = sql.Identifier(user), sql.Identifier(owner), sql.Identifier(db)
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [user])
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role).as_string(connection.connection))
            statements = [
                # Utility statements can't take bind parameters: quote client-side.
                sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}")
                .format(role, sql.Literal(password)),
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(dbname, role),
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"),
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role),
                sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role),
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(role),
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}")
                .format(own, role),
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}")
                .format(own, role),
            ]
            for st in statements:
                cur.execute(st.as_string(connection.connection))
            self._close_hosted_api(cur, owner)
            cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public' AND matviewname = ANY(%s)",
                        [list(MATVIEWS)])
            for (name,) in cur.fetchall():
                cur.execute(sql.SQL("ALTER MATERIALIZED VIEW {} OWNER TO {}")
                            .format(sql.Identifier(name), role).as_string(connection.connection))
        self.stdout.write(self.style.SUCCESS(f"App role {user!r} ready (row access only; owner stays {owner!r})."))

    def _close_hosted_api(self, cur, owner):
        """Supabase: its Data API (PostgREST) serves the public schema to the
        ``anon``/``authenticated`` roles, and by default they get every new
        table. IMS doesn't use that API, so take those grants away; otherwise
        anyone with the project's public key could read or change the data."""
        from psycopg import sql

        cur.execute("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
        api_roles = [r for (r,) in cur.fetchall()]
        conn = connection.connection
        for name in api_roles:
            r = sql.Identifier(name)
            for st in (
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(r),
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(r),
                sql.SQL("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {}").format(r),
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON TABLES FROM {}").format(sql.Identifier(owner), r),
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {}").format(sql.Identifier(owner), r),
                sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM {}").format(sql.Identifier(owner), r),
            ):
                cur.execute(st.as_string(conn))
        if api_roles:
            self.stdout.write(f"Removed hosted data-API access for: {', '.join(api_roles)}.")
