from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[2]
FOUNDATION_MIGRATION = ROOT / "sql/migrations/20260905_multitenancy_foundation.sql"


def _postgres_bin(name: str) -> str:
    configured = os.environ.get("POSTGRES_BIN")
    candidates = [
        Path(configured) / name if configured else None,
        Path("/opt/homebrew/opt/postgresql@17/bin") / name,
        Path("/usr/local/opt/postgresql@17/bin") / name,
    ]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.exists():
            return str(candidate)
    pytest.skip("PostgreSQL 17 test binaries are unavailable")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@pytest.fixture(scope="session")
def foundation_database(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pg-foundation")
    data = directory / "data"
    log = directory / "postgres.log"
    port = _free_port()
    initdb = _postgres_bin("initdb")
    pg_ctl = _postgres_bin("pg_ctl")
    subprocess.run(
        [
            initdb,
            "--no-sync",
            "--auth-local=trust",
            "--auth-host=trust",
            "--username=postgres",
            str(data),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            pg_ctl,
            "-D",
            str(data),
            "-l",
            str(log),
            "-o",
            f"-F -h 127.0.0.1 -p {port}",
            "-w",
            "start",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    connection = None
    try:
        connection = psycopg.connect(
            dbname="postgres",
            user="postgres",
            host="127.0.0.1",
            port=port,
            autocommit=True,
        )
        connection.execute("CREATE ROLE anon NOLOGIN")
        connection.execute("CREATE ROLE authenticated NOLOGIN")
        connection.execute("CREATE ROLE service_role NOLOGIN")
        connection.execute("CREATE SCHEMA auth")
        connection.execute(
            "CREATE TABLE auth.users (id uuid PRIMARY KEY, email text UNIQUE)"
        )
        connection.execute((ROOT / "sql/schema.sql").read_text())
        if FOUNDATION_MIGRATION.exists():
            connection.execute(FOUNDATION_MIGRATION.read_text())
        yield connection
    finally:
        if connection is not None:
            connection.close()
        subprocess.run(
            [pg_ctl, "-D", str(data), "-m", "fast", "-w", "stop"],
            check=False,
            capture_output=True,
            text=True,
        )
