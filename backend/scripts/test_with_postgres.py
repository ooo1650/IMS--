"""Run the test suite against a throwaway embedded PostgreSQL (pip: pgserver),
so DB triggers and row-lock concurrency tests run without a system Postgres.

    python scripts/test_with_postgres.py [test labels...]
"""
import os
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import pgserver

backend = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix="ims-pg-") as data:
    srv = pgserver.get_server(data, cleanup_mode="stop")
    socket_dir = urllib.parse.parse_qs(urllib.parse.urlparse(srv.get_uri()).query)["host"][0]
    env = {**os.environ, "DATABASE_URL": f"postgres://postgres@{urllib.parse.quote(socket_dir, safe='')}/postgres"}
    code = subprocess.call([sys.executable, "manage.py", "test", *sys.argv[1:]], cwd=backend, env=env)
    srv.cleanup()
sys.exit(code)
