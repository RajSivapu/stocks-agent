"""Disposable PostgreSQL coverage for protected recovery-journal compaction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

from cryptography.fernet import Fernet
import psycopg
import pytest


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40


def _module():
    try:
        from scripts import compact_recovery_journals
    except ModuleNotFoundError:
        pytest.fail("protected recovery-journal compaction entrypoint is missing")
    return compact_recovery_journals


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _terminal_payload(
    *, run_id: str, run_attempt: str = "1", status: str = "rolled_back"
) -> dict[str, object]:
    components: dict[str, object] = {}
    if status == "verified":
        prior = {
            "exists": False,
            "identity": None,
            "version": None,
            "configuration": {},
            "files": {},
            "values": {},
        }
        components = {
            name: {
                "changed": False,
                "prior": prior,
                "prior_sha256": hashlib.sha256(_canonical(prior)).hexdigest(),
            }
            for name in (
                "runtime-role",
                "dashboard-secrets",
                "market-briefing-gateway",
                "owner-dashboard-api",
                "telegram-portfolio",
            )
        }
    return {
        "format": 1,
        "status": status,
        "captured_at": "2026-09-15T17:54:46+00:00",
        "components": components,
        "release_context": {
            "project_ref": PROJECT_REF,
            "candidate_sha": MAIN_SHA,
            "release_run_id": run_id,
            "release_run_attempt": run_attempt,
        },
    }


@pytest.fixture()
def database():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()):
        pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="journal-compaction-pg-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        subprocess.run(
            [
                binaries["initdb"],
                "-D",
                str(root / "db"),
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--no-locale",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                binaries["pg_ctl"],
                "-D",
                str(root / "db"),
                "-l",
                str(root / "log"),
                "-o",
                f"-k {root} -h '' -p {port}",
                "-w",
                "start",
            ],
            check=True,
            capture_output=True,
        )
        try:
            dsn = f"host={root} port={port} dbname=postgres"
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute(
                    "CREATE TABLE public.stock_agent_release_mutation_lease("
                    "singleton boolean PRIMARY KEY, owner text NOT NULL, "
                    "kind text NOT NULL, state text NOT NULL, "
                    "expires_at timestamptz NOT NULL, heartbeat_at timestamptz NOT NULL)"
                )
                expired = datetime.now(timezone.utc) - timedelta(minutes=5)
                connection.execute(
                    "INSERT INTO public.stock_agent_release_mutation_lease "
                    "VALUES(true,'recovery-349-1','recovery','resolved',%s,%s)",
                    (expired, expired),
                )
                connection.execute(
                    "CREATE TABLE public.stock_agent_component_recovery_journals("
                    "sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                    "project_ref text NOT NULL, candidate_sha text NOT NULL, "
                    "run_id text NOT NULL, run_attempt text NOT NULL, "
                    "ciphertext bytea NOT NULL, captured_at timestamptz NOT NULL "
                    "DEFAULT clock_timestamp())"
                )
            yield dsn
        finally:
            subprocess.run(
                [
                    binaries["pg_ctl"],
                    "-D",
                    str(root / "db"),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ],
                check=True,
                capture_output=True,
            )


def _insert(
    connection: psycopg.Connection,
    cipher: Fernet,
    *,
    run_id: str,
    payload: dict[str, object],
) -> int:
    return connection.execute(
        "INSERT INTO public.stock_agent_component_recovery_journals("
        "project_ref,candidate_sha,run_id,run_attempt,ciphertext) "
        "VALUES(%s,%s,%s,'1',%s) RETURNING sequence",
        (PROJECT_REF, MAIN_SHA, run_id, cipher.encrypt(_canonical(payload))),
    ).fetchone()[0]


def test_compaction_keeps_only_latest_authenticated_terminal_checkpoint_per_run(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(
            connection,
            cipher,
            run_id="350",
            payload={**_terminal_payload(run_id="350"), "status": "preparing"},
        )
        kept_rolled_back = _insert(
            connection,
            cipher,
            run_id="350",
            payload=_terminal_payload(run_id="350"),
        )
        _insert(
            connection,
            cipher,
            run_id="351",
            payload={**_terminal_payload(run_id="351", status="verified"), "status": "recovery_required"},
        )
        kept_verified = _insert(
            connection,
            cipher,
            run_id="351",
            payload=_terminal_payload(run_id="351", status="verified"),
        )

    receipt = module.compact_recovery_journals(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
    )

    with psycopg.connect(database, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT sequence,run_id FROM public.stock_agent_component_recovery_journals "
            "ORDER BY sequence"
        ).fetchall()
        unique_indexes = connection.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
            "AND tablename='stock_agent_component_recovery_journals' "
            "AND indexname='stock_agent_component_recovery_journals_run_identity'"
        ).fetchall()

    assert rows == [(kept_rolled_back, "350"), (kept_verified, "351")]
    assert len(unique_indexes) == 1
    assert receipt["format"] == "stocks-recovery-journal-compaction-v1"
    assert receipt["main_sha"] == MAIN_SHA
    assert receipt["before"] == {"groups": 2, "rows": 4}
    assert receipt["after"] == {"groups": 2, "rows": 2}
    assert receipt["deleted_rows"] == 2
    assert receipt["terminal_statuses"] == {"rolled_back": 1, "verified": 1}
    assert receipt["unique_identity"] is True
    assert receipt["vacuum_full"] is True
    assert len(receipt["retained_identity_sha256"]) == 64


def test_compaction_rejects_an_unresolved_release_lease_without_deleting(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        connection.execute(
            "UPDATE public.stock_agent_release_mutation_lease "
            "SET state='recovery_required',expires_at=statement_timestamp()+interval '5 minutes'"
        )

    with pytest.raises(RuntimeError, match="lease remains unresolved"):
        module.compact_recovery_journals(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)


def test_compaction_rejects_a_nonterminal_latest_journal_atomically(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(
            connection,
            cipher,
            run_id="350",
            payload={**_terminal_payload(run_id="350"), "status": "recovery_required"},
        )

    with pytest.raises(RuntimeError, match="not terminal"):
        module.compact_recovery_journals(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='stock_agent_component_recovery_journals_run_identity'"
        ).fetchone() == (0,)


def test_compaction_rejects_a_retained_row_with_mismatched_authenticated_identity(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(
            connection,
            cipher,
            run_id="350",
            payload=_terminal_payload(run_id="999"),
        )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        module.compact_recovery_journals(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (1,)


def test_compaction_is_idempotent_after_the_unique_terminal_set_is_retained(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))

    first = module.compact_recovery_journals(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
    )
    second = module.compact_recovery_journals(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
    )

    assert first["deleted_rows"] == 1
    assert second["deleted_rows"] == 0
    assert second["before"] == second["after"] == {"groups": 1, "rows": 1}
    assert second["retained_identity_sha256"] == first["retained_identity_sha256"]


def test_compaction_rejects_inventory_outside_the_approved_destructive_scope(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))

    with pytest.raises(RuntimeError, match="approved inventory changed"):
        module.compact_recovery_journals(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            expected_rows=467,
            expected_groups=45,
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)
