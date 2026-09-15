"""Disposable PostgreSQL coverage for protected recovery-journal compaction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
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
                f"-k {root} -h '' -p {port} -c log_statement=all",
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
    captured_at: str | None = None,
) -> int:
    if captured_at is None:
        return connection.execute(
            "INSERT INTO public.stock_agent_component_recovery_journals("
            "project_ref,candidate_sha,run_id,run_attempt,ciphertext) "
            "VALUES(%s,%s,%s,'1',%s) RETURNING sequence",
            (PROJECT_REF, MAIN_SHA, run_id, cipher.encrypt(_canonical(payload))),
        ).fetchone()[0]
    return connection.execute(
        "INSERT INTO public.stock_agent_component_recovery_journals("
        "project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at) "
        "VALUES(%s,%s,%s,'1',%s,%s) RETURNING sequence",
        (
            PROJECT_REF,
            MAIN_SHA,
            run_id,
            cipher.encrypt(_canonical(payload)),
            captured_at,
        ),
    ).fetchone()[0]


def test_prepare_rejects_an_unresolved_release_lease_without_exporting(database, tmp_path):
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
        module.prepare_recovery_journal_backup(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            backup_path=tmp_path / "backup.json",
            manifest_path=tmp_path / "manifest.json",
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)


def test_prepare_rejects_a_nonterminal_latest_journal_atomically(database, tmp_path):
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
        module.prepare_recovery_journal_backup(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            backup_path=tmp_path / "backup.json",
            manifest_path=tmp_path / "manifest.json",
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname='public' "
            "AND indexname='stock_agent_component_recovery_journals_run_identity'"
        ).fetchone() == (0,)


def test_prepare_rejects_a_retained_row_with_mismatched_authenticated_identity(
    database, tmp_path
):
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
        module.prepare_recovery_journal_backup(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            backup_path=tmp_path / "backup.json",
            manifest_path=tmp_path / "manifest.json",
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (1,)


def test_prepare_rejects_inventory_outside_the_approved_scope(database, tmp_path):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))

    with pytest.raises(RuntimeError, match="approved inventory changed"):
        module.prepare_recovery_journal_backup(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            backup_path=tmp_path / "backup.json",
            manifest_path=tmp_path / "manifest.json",
            expected_rows=467,
            expected_groups=45,
        )

    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (2,)


def test_prepare_backup_preserves_exact_latest_authenticated_rows(database, tmp_path):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(
            connection,
            cipher,
            run_id="350",
            payload={**_terminal_payload(run_id="350"), "status": "preparing"},
            captured_at="2026-09-15T16:00:00+00:00",
        )
        kept_rolled_back = _insert(
            connection,
            cipher,
            run_id="350",
            payload=_terminal_payload(run_id="350"),
            captured_at="2026-09-15T17:00:00.123456+00:00",
        )
        _insert(
            connection,
            cipher,
            run_id="351",
            payload={
                **_terminal_payload(run_id="351", status="verified"),
                "status": "recovery_required",
            },
            captured_at="2026-09-15T17:30:00+00:00",
        )
        kept_verified = _insert(
            connection,
            cipher,
            run_id="351",
            payload=_terminal_payload(run_id="351", status="verified"),
            captured_at="2026-09-15T18:00:00.654321+00:00",
        )
        expected = connection.execute(
            "SELECT sequence,project_ref,candidate_sha,run_id,run_attempt,"
            "ciphertext,captured_at FROM public.stock_agent_component_recovery_journals "
            "WHERE sequence=ANY(%s) ORDER BY sequence",
            ([kept_rolled_back, kept_verified],),
        ).fetchall()

    backup_path = tmp_path / "recovery-journal-backup.json"
    manifest_path = tmp_path / "recovery-journal-backup-manifest.json"
    manifest = module.prepare_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        expected_rows=4,
        expected_groups=2,
    )

    bundle_bytes = backup_path.read_bytes()
    bundle = json.loads(bundle_bytes)
    records = bundle["records"]
    assert bundle["format"] == "stocks-recovery-journal-backup-v1"
    assert bundle["project_ref"] == PROJECT_REF
    assert bundle["main_sha"] == MAIN_SHA
    assert [record["sequence"] for record in records] == [
        kept_rolled_back,
        kept_verified,
    ]
    assert [record["run_id"] for record in records] == ["350", "351"]
    assert [record["captured_at"] for record in records] == [
        "2026-09-15T17:00:00.123456+00:00",
        "2026-09-15T18:00:00.654321+00:00",
    ]
    assert [record["ciphertext"].encode("ascii") for record in records] == [
        row[5] for row in expected
    ]
    assert manifest["format"] == "stocks-recovery-journal-backup-manifest-v1"
    assert manifest["source"]["rows"] == 4
    assert manifest["source"]["groups"] == 2
    assert manifest["retained"]["rows"] == 2
    assert manifest["retained"]["terminal_statuses"] == {
        "rolled_back": 1,
        "verified": 1,
    }
    assert manifest["bundle"]["bytes"] == len(bundle_bytes)
    assert manifest["bundle"]["sha256"] == hashlib.sha256(bundle_bytes).hexdigest()
    assert json.loads(manifest_path.read_bytes()) == manifest
    assert module.verify_recovery_journal_backup(
        backup_path=backup_path,
        manifest_path=manifest_path,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        expected_rows=4,
        expected_groups=2,
    ) == manifest
    with psycopg.connect(database, autocommit=True) as connection:
        assert connection.execute(
            "SELECT count(*) FROM public.stock_agent_component_recovery_journals"
        ).fetchone() == (4,)


def test_verify_backup_rejects_ciphertext_tampering_before_database_access(database, tmp_path):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))

    backup_path = tmp_path / "recovery-journal-backup.json"
    manifest_path = tmp_path / "recovery-journal-backup-manifest.json"
    module.prepare_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        expected_rows=1,
        expected_groups=1,
    )
    bundle = json.loads(backup_path.read_bytes())
    ciphertext = bundle["records"][0]["ciphertext"]
    bundle["records"][0]["ciphertext"] = (
        ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
    )
    backup_path.write_bytes(_canonical(bundle) + b"\n")

    with pytest.raises(RuntimeError, match="backup digest mismatch"):
        module.verify_recovery_journal_backup(
            backup_path=backup_path,
            manifest_path=manifest_path,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            expected_rows=1,
            expected_groups=1,
        )


def _artifact_binding() -> dict[str, object]:
    return {
        "id": 42,
        "name": "recovery-journal-backup-123-1",
        "digest": "sha256:" + "b" * 64,
        "workflow_run_id": 123,
    }


def _prepare_two_identity_backup(database: str, tmp_path: Path):
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
            captured_at="2026-09-15T17:00:00.123456+00:00",
        )
        _insert(
            connection,
            cipher,
            run_id="351",
            payload={
                **_terminal_payload(run_id="351", status="verified"),
                "status": "recovery_required",
            },
        )
        kept_verified = _insert(
            connection,
            cipher,
            run_id="351",
            payload=_terminal_payload(run_id="351", status="verified"),
            captured_at="2026-09-15T18:00:00.654321+00:00",
        )
    backup_path = tmp_path / "recovery-journal-backup.json"
    manifest_path = tmp_path / "recovery-journal-backup-manifest.json"
    module.prepare_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        expected_rows=4,
        expected_groups=2,
    )
    return module, key, backup_path, manifest_path, [kept_rolled_back, kept_verified]


def _apply_backup(module, database, key, backup_path, manifest_path):
    return module.apply_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        backup_artifact=_artifact_binding(),
        expected_rows=4,
        expected_groups=2,
    )


def _journal_rows(database: str):
    with psycopg.connect(database, autocommit=True) as connection:
        return connection.execute(
            "SELECT sequence,project_ref,candidate_sha,run_id,run_attempt,"
            "ciphertext,captured_at AT TIME ZONE 'UTC' "
            "FROM public.stock_agent_component_recovery_journals ORDER BY sequence"
        ).fetchall()


def test_apply_uses_truncate_restore_without_delete_or_vacuum_full(database, tmp_path):
    module, key, backup_path, manifest_path, kept = _prepare_two_identity_backup(
        database, tmp_path
    )
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "CREATE FUNCTION public.reject_journal_delete() RETURNS trigger "
            "LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'journal delete forbidden'; END$$"
        )
        connection.execute(
            "CREATE TRIGGER reject_journal_delete BEFORE DELETE ON "
            "public.stock_agent_component_recovery_journals FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.reject_journal_delete()"
        )

    receipt = _apply_backup(module, database, key, backup_path, manifest_path)

    assert [row[0] for row in _journal_rows(database)] == kept
    assert receipt["format"] == "stocks-recovery-journal-compaction-v2"
    assert receipt["method"] == "truncate_restore"
    assert receipt["truncated"] is True
    assert receipt["resumed"] is False
    assert receipt["removed_redundant_rows"] == 2
    assert receipt["after"] == {"groups": 2, "rows": 2}
    assert receipt["unique_identity"] is True
    assert receipt["vacuum_full"] is False
    assert receipt["backup_artifact"] == _artifact_binding()
    log_path = Path(database.split()[0].split("=", 1)[1]) / "log"
    statements = log_path.read_text()
    assert (
        "statement: TRUNCATE TABLE public.stock_agent_component_recovery_journals "
        "CONTINUE IDENTITY" in statements
    )
    assert (
        "statement: DELETE FROM public.stock_agent_component_recovery_journals"
        not in statements
    )
    assert "statement: VACUUM (FULL" not in statements


def test_apply_does_not_load_obsolete_ciphertext_when_latest_is_authenticated(
    database, tmp_path
):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO public.stock_agent_component_recovery_journals("
            "project_ref,candidate_sha,run_id,run_attempt,ciphertext) "
            "VALUES(%s,%s,'350','1',%s)",
            (PROJECT_REF, MAIN_SHA, b"\xff" * 1024),
        )
        kept = _insert(
            connection,
            cipher,
            run_id="350",
            payload=_terminal_payload(run_id="350"),
        )
    backup_path = tmp_path / "backup.json"
    manifest_path = tmp_path / "manifest.json"
    module.prepare_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        expected_rows=2,
        expected_groups=1,
    )

    receipt = module.apply_recovery_journal_backup(
        admin_url=database,
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        recovery_key=key,
        backup_path=backup_path,
        manifest_path=manifest_path,
        backup_artifact=_artifact_binding(),
        expected_rows=2,
        expected_groups=1,
    )

    assert [row[0] for row in _journal_rows(database)] == [kept]
    assert receipt["removed_redundant_rows"] == 1


def test_apply_rejects_an_unbound_backup_without_mutating(database, tmp_path):
    module, key, backup_path, manifest_path, _ = _prepare_two_identity_backup(
        database, tmp_path
    )

    with pytest.raises(RuntimeError, match="backup artifact binding is malformed"):
        module.apply_recovery_journal_backup(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
            backup_path=backup_path,
            manifest_path=manifest_path,
            backup_artifact={**_artifact_binding(), "digest": "sha256:bad"},
            expected_rows=4,
            expected_groups=2,
        )

    assert len(_journal_rows(database)) == 4


def test_apply_resumes_from_an_empty_post_truncate_table(database, tmp_path):
    module, key, backup_path, manifest_path, kept = _prepare_two_identity_backup(
        database, tmp_path
    )
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE TABLE public.stock_agent_component_recovery_journals CONTINUE IDENTITY"
        )

    receipt = _apply_backup(module, database, key, backup_path, manifest_path)

    assert [row[0] for row in _journal_rows(database)] == kept
    assert receipt["truncated"] is False
    assert receipt["resumed"] is True


def test_apply_resumes_from_an_exact_partial_retained_set(database, tmp_path):
    module, key, backup_path, manifest_path, kept = _prepare_two_identity_backup(
        database, tmp_path
    )
    record = json.loads(backup_path.read_bytes())["records"][0]
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE TABLE public.stock_agent_component_recovery_journals CONTINUE IDENTITY"
        )
        connection.execute(
            "INSERT INTO public.stock_agent_component_recovery_journals("
            "sequence,project_ref,candidate_sha,run_id,run_attempt,ciphertext,captured_at) "
            "OVERRIDING SYSTEM VALUE VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (
                record["sequence"],
                record["project_ref"],
                record["candidate_sha"],
                record["run_id"],
                record["run_attempt"],
                record["ciphertext"].encode("ascii"),
                record["captured_at"],
            ),
        )

    receipt = _apply_backup(module, database, key, backup_path, manifest_path)

    assert [row[0] for row in _journal_rows(database)] == kept
    assert receipt["truncated"] is False
    assert receipt["resumed"] is True


def test_apply_rejects_an_unexpected_partial_row_without_replacing_it(database, tmp_path):
    module, key, backup_path, manifest_path, _ = _prepare_two_identity_backup(
        database, tmp_path
    )
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            "TRUNCATE TABLE public.stock_agent_component_recovery_journals CONTINUE IDENTITY"
        )
        unexpected = _insert(
            connection,
            cipher,
            run_id="999",
            payload=_terminal_payload(run_id="999"),
        )

    with pytest.raises(RuntimeError, match="does not match the verified backup"):
        _apply_backup(module, database, key, backup_path, manifest_path)

    assert [row[0] for row in _journal_rows(database)] == [unexpected]


def test_apply_is_idempotent_after_exact_restore(database, tmp_path):
    module, key, backup_path, manifest_path, kept = _prepare_two_identity_backup(
        database, tmp_path
    )

    first = _apply_backup(module, database, key, backup_path, manifest_path)
    second = _apply_backup(module, database, key, backup_path, manifest_path)

    assert first["truncated"] is True
    assert second["truncated"] is False
    assert second["resumed"] is False
    assert second["before"] == second["after"] == {"groups": 2, "rows": 2}
    assert second["retained_identity_sha256"] == first["retained_identity_sha256"]
    assert [row[0] for row in _journal_rows(database)] == kept


def test_explicit_cli_commands_round_trip_the_verified_backup(
    database, tmp_path, monkeypatch, capsys
):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
    backup_path = tmp_path / "recovery-journal-backup.json"
    manifest_path = tmp_path / "recovery-journal-backup-manifest.json"
    receipt_path = tmp_path / "recovery-journal-compaction.json"
    common = [
        "--project-ref",
        PROJECT_REF,
        "--main-sha",
        MAIN_SHA,
        "--expected-rows",
        "2",
        "--expected-groups",
        "1",
    ]
    monkeypatch.setenv("RELEASE_RECOVERY_KEY", key.decode("ascii"))
    from scripts import deploy_owner_dashboard_api

    monkeypatch.setattr(
        deploy_owner_dashboard_api,
        "validate_release_admin_session_url",
        lambda project_ref, admin_url: admin_url,
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compact_recovery_journals.py",
            "prepare",
            *common,
            "--admin-url",
            database,
            "--backup",
            str(backup_path),
            "--manifest",
            str(manifest_path),
        ],
    )
    assert module.main() == 0
    prepare_stdout = capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compact_recovery_journals.py",
            "verify",
            *common,
            "--backup",
            str(backup_path),
            "--manifest",
            str(manifest_path),
        ],
    )
    assert module.main() == 0
    verify_stdout = capsys.readouterr().out
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compact_recovery_journals.py",
            "apply",
            *common,
            "--admin-url",
            database,
            "--backup",
            str(backup_path),
            "--manifest",
            str(manifest_path),
            "--backup-artifact-id",
            "42",
            "--backup-artifact-name",
            "recovery-journal-backup-123-1",
            "--backup-artifact-digest",
            "sha256:" + "b" * 64,
            "--backup-artifact-run-id",
            "123",
            "--output",
            str(receipt_path),
        ],
    )
    assert module.main() == 0
    apply_stdout = capsys.readouterr().out
    assert json.loads(prepare_stdout)["format"] == (
        "stocks-recovery-journal-backup-manifest-v1"
    )
    assert json.loads(verify_stdout)["format"] == (
        "stocks-recovery-journal-backup-manifest-v1"
    )
    assert json.loads(apply_stdout)["format"] == "stocks-recovery-journal-compaction-v2"
    assert json.loads(receipt_path.read_bytes()) == json.loads(apply_stdout)


def test_legacy_direct_compaction_entrypoint_fails_closed(database):
    module = _module()
    key = Fernet.generate_key()
    cipher = Fernet(key)
    with psycopg.connect(database, autocommit=True) as connection:
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))
        _insert(connection, cipher, run_id="350", payload=_terminal_payload(run_id="350"))

    with pytest.raises(RuntimeError, match="artifact-bound backup is required"):
        module.compact_recovery_journals(
            admin_url=database,
            project_ref=PROJECT_REF,
            main_sha=MAIN_SHA,
            recovery_key=key,
        )

    assert len(_journal_rows(database)) == 2
