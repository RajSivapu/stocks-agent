import stat
from pathlib import Path

import pytest

from scripts import deploy_owner_dashboard_api as deploy


PROJECT_REF = "hlxpxbxhqctwsqizwjjy"
OWNER_ID = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
ORIGIN = "https://stocks.example.com"
DATABASE_URL = (
    "postgresql://stock_agent_dashboard_runtime.hlxpxbxhqctwsqizwjjy:"
    "dashboard-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)
ADMIN_URL = (
    "postgresql://postgres:admin-password-longer-than-24@"
    "db.hlxpxbxhqctwsqizwjjy.supabase.co:5432/postgres?sslmode=require"
)
SESSION_TEMPLATE = (
    "postgresql://postgres.hlxpxbxhqctwsqizwjjy:admin-password-longer-than-24@"
    "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)
ROLE_RECEIPT = {
    "status": "verified", "runtime_role": "stock_agent_dashboard_runtime",
    "privilege_role": "stock_agent_dashboard", "table_count": 15,
    "write_privileges": 0, "application_function_execute": 0, "owned_objects": 0,
}


def configuration(**overrides):
    values = {
        "project_ref": PROJECT_REF,
        "owner_user_id": OWNER_ID,
        "allowed_origin": ORIGIN,
        "database_url": DATABASE_URL,
        "role_receipt": ROLE_RECEIPT,
        "secret_names": deploy.DASHBOARD_SECRET_NAMES,
    }
    values.update(overrides)
    return values


def test_valid_configuration_is_normalized_without_secret_values():
    result = deploy.validate_deployment_configuration(**configuration())
    assert result["project_ref_digest"] != PROJECT_REF
    assert result["allowed_origin"] == ORIGIN
    assert "dashboard-password" not in str(result)


def test_static_configuration_can_be_validated_before_database_mutation():
    result = deploy.validate_static_configuration(
        PROJECT_REF, OWNER_ID, ORIGIN, deploy.DASHBOARD_SECRET_NAMES,
    )
    assert result["allowed_origin"] == ORIGIN
    with pytest.raises(ValueError, match="owner UUID"):
        deploy.validate_static_configuration(PROJECT_REF, "", ORIGIN, deploy.DASHBOARD_SECRET_NAMES)


def test_database_endpoints_are_bound_to_the_exact_project_before_mutation():
    result = deploy.validate_release_database_endpoints(PROJECT_REF, ADMIN_URL, SESSION_TEMPLATE)
    assert result == {"admin_database": "verified", "session_pooler": "verified"}

    assert deploy.release_admin_database_url(
        PROJECT_REF, ADMIN_URL, SESSION_TEMPLATE,
    ) == SESSION_TEMPLATE
    assert deploy.release_admin_database_url(
        PROJECT_REF, ADMIN_URL.replace(":5432", ""), SESSION_TEMPLATE,
    ) == SESSION_TEMPLATE

    with pytest.raises(ValueError, match="project-matched administrator"):
        deploy.validate_release_database_endpoints(
            PROJECT_REF,
            ADMIN_URL.replace(PROJECT_REF, "aaaaaaaaaaaaaaaaaaaa"),
            SESSION_TEMPLATE,
        )
    with pytest.raises(ValueError, match="project-matched administrator Supavisor"):
        deploy.validate_release_database_endpoints(
            PROJECT_REF,
            ADMIN_URL,
            SESSION_TEMPLATE.replace(PROJECT_REF, "aaaaaaaaaaaaaaaaaaaa"),
        )


@pytest.mark.parametrize(
    "session_template",
    (
        SESSION_TEMPLATE.replace(f"postgres.{PROJECT_REF}", f"service_role.{PROJECT_REF}"),
        SESSION_TEMPLATE.replace(PROJECT_REF, "aaaaaaaaaaaaaaaaaaaa"),
        SESSION_TEMPLATE.replace("pooler.supabase.com", "example.com"),
        SESSION_TEMPLATE.replace(":5432/postgres", ":6543/postgres"),
        SESSION_TEMPLATE.replace("/postgres", "/template1"),
        SESSION_TEMPLATE.replace("admin-password-longer-than-24", "short"),
        SESSION_TEMPLATE + "?sslmode=require",
    ),
)
def test_release_admin_database_url_rejects_unbound_pooler_templates(session_template):
    with pytest.raises(ValueError, match="project-matched administrator Supavisor"):
        deploy.release_admin_database_url(PROJECT_REF, ADMIN_URL, session_template)


def test_release_database_transport_connects_read_only_through_the_admin_pooler():
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement):
            calls.append(statement)

        def fetchone(self):
            if calls[-1] == "SHOW transaction_read_only":
                return ("on",)
            return ("postgres", "postgres")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    connected = {}

    def connector(url, **options):
        connected.update(url=url, options=options)
        return Connection()

    result = deploy.verify_release_database_transport(
        PROJECT_REF, ADMIN_URL, SESSION_TEMPLATE, connector=connector,
    )

    assert connected == {
        "url": SESSION_TEMPLATE,
        "options": {
            "connect_timeout": 15,
        },
    }
    assert calls == [
        "BEGIN READ ONLY",
        "SHOW transaction_read_only",
        "SELECT current_user, current_database()",
    ]
    assert result == {
        "admin_database": "verified",
        "session_pooler": "verified",
        "read_only_connectivity": "verified",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("project_ref", "project-ref", "project reference"),
        ("owner_user_id", "not-a-uuid", "owner UUID"),
        ("allowed_origin", "https://stocks.example.com/path", "exact HTTPS origin"),
        ("database_url", "postgresql://postgres:secret@db.example.com/postgres", "Supavisor"),
        ("role_receipt", {**ROLE_RECEIPT, "write_privileges": 1}, "role verifier"),
        ("secret_names", (*deploy.DASHBOARD_SECRET_NAMES, "SUPABASE_SERVICE_ROLE_KEY"), "secret manifest"),
    ),
)
def test_configuration_rejects_every_unsafe_input(field, value, message):
    with pytest.raises(ValueError, match=message):
        deploy.validate_deployment_configuration(**configuration(**{field: value}))


def test_git_release_refuses_dirty_and_unpushed_commits(tmp_path):
    outputs = iter([" M file.ts\n", "abc\n", "abc\n"])

    def dirty_runner(*_args, **_kwargs):
        return type("Result", (), {"returncode": 0, "stdout": next(outputs), "stderr": ""})()

    with pytest.raises(RuntimeError, match="clean"):
        deploy.verify_git_release(tmp_path, dirty_runner)

    values = iter(["", "local\n", "remote\n"])

    def unpushed_runner(*_args, **_kwargs):
        return type("Result", (), {"returncode": 0, "stdout": next(values), "stderr": ""})()

    with pytest.raises(RuntimeError, match="pushed"):
        deploy.verify_git_release(tmp_path, unpushed_runner)


def test_reviewed_sha_accepts_only_the_exact_candidate_or_identical_reviewed_tree(tmp_path):
    candidate = "a" * 40
    reviewed = "b" * 40
    calls = []

    trees = iter(["shared-tree\n", "shared-tree\n"])

    def identical_tree_runner(command, **_options):
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": next(trees), "stderr": ""})()

    assert deploy.verify_reviewed_sha(candidate, reviewed, tmp_path, identical_tree_runner) == candidate
    assert calls == [
        ["git", "rev-parse", f"{reviewed}^{{tree}}"],
        ["git", "rev-parse", f"{candidate}^{{tree}}"],
    ]

    different_trees = iter(["reviewed-tree\n", "candidate-tree\n"])

    def ancestor_with_different_tree_runner(command, **_options):
        return type("Result", (), {"returncode": 0, "stdout": next(different_trees), "stderr": ""})()

    with pytest.raises(RuntimeError, match="exact reviewed"):
        deploy.verify_reviewed_sha(candidate, reviewed, tmp_path, ancestor_with_different_tree_runner)


def test_local_suite_failure_stops_deployment(tmp_path):
    runner = lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
    with pytest.raises(RuntimeError, match="local verification"):
        deploy.run_local_verification(tmp_path, runner)


def test_local_verification_cannot_inherit_database_integration_opt_in(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    deploy.run_local_verification(tmp_path, runner)

    assert commands == [["env", "-u", "RUN_DB_INTEGRATION_TESTS", "npm", "run", "test:all"]]


def test_candidate_migration_manifest_discovers_every_ordered_candidate_migration_and_hash():
    manifest = deploy.candidate_migration_manifest()
    names = [row["path"] for row in manifest]
    assert names == sorted(names)
    assert "sql/migrations/20260926_report_suppression_reasons.sql" in names
    assert "sql/migrations/20260927_release_evidence_reader.sql" in names
    assert all(len(row["sha256"]) == 64 for row in manifest)
    assert deploy.CHANGED_FUNCTIONS == ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")


def test_release_source_refuses_the_superseded_thin_dashboard(tmp_path):
    app = tmp_path / "apps/web/src/app/App.tsx"
    app.parent.mkdir(parents=True)
    app.write_text('<Route path="/portfolio" />')
    with pytest.raises(RuntimeError, match="superseded thin dashboard"):
        deploy.verify_v1_dashboard_source(tmp_path)

    app.write_text("\n".join(f'<Route path="/{surface}" />' for surface in deploy.V1_SURFACES))
    assert deploy.verify_v1_dashboard_source(tmp_path) == {
        "status": "verified", "primary_surfaces": list(deploy.V1_SURFACES),
    }


def test_release_migrations_are_applied_in_order_once_with_candidate_hashes(tmp_path):
    migrations = []
    for name in ("20260926_report_suppression_reasons.sql", "20260927_release_evidence_reader.sql", "20260928_future_addition.sql"):
        path = tmp_path / name
        path.write_text(f"-- {name}\nSELECT 1;\n")
        migrations.append(path)

    class Cursor:
        def __init__(self):
            self.statements = []
            self.rows = []

        def execute(self, statement, params=None):
            self.statements.append(statement)

        def fetchall(self):
            return self.rows

    cursor = Cursor()
    manifest = deploy.candidate_migration_manifest(tmp_path)
    receipt = deploy.apply_release_migrations(cursor, manifest, tmp_path)
    assert [row["version"] for row in receipt["applied"]] == ["20260926", "20260927", "20260928"]
    assert receipt["skipped"] == []
    assert [path.read_text() for path in migrations] == [s for s in cursor.statements if s.startswith("--")]
    assert all(len(row["sha256"]) == 64 for row in receipt["candidate"])
    manifest[0]["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="hash"):
        deploy.apply_release_migrations(Cursor(), manifest, tmp_path)


def test_migration_ledger_skips_verified_rows_and_refuses_hash_drift(tmp_path):
    path = tmp_path / "20260926_report_suppression_reasons.sql"
    path.write_text("SELECT 26;\n")
    manifest = deploy.candidate_migration_manifest(tmp_path)

    class Cursor:
        def __init__(self, rows):
            self.rows, self.calls = rows, 0
            self.statements = []

        def execute(self, statement, params=None):
            self.statements.append((statement, params))

        def fetchall(self):
            self.calls += 1
            return self.rows if self.calls == 1 else [
                (row[1], [path.read_text()]) for row in self.rows
            ]

    existing = [(manifest[0]["path"], manifest[0]["version"], manifest[0]["sha256"])]
    receipt = deploy.apply_release_migrations(Cursor(existing), manifest, tmp_path)
    assert receipt["applied"] == []
    assert receipt["skipped"] == manifest

    drifted = [(manifest[0]["path"], manifest[0]["version"], "0" * 64)]
    with pytest.raises(RuntimeError, match="ledger|diverge"):
        deploy.apply_release_migrations(Cursor(drifted), manifest, tmp_path)


def test_migration_ledger_bootstraps_only_matching_native_statement_receipts(tmp_path):
    path = tmp_path / "20260928_native.sql"; path.write_text("SELECT 28;\n")
    manifest = deploy.candidate_migration_manifest(tmp_path)

    class Cursor:
        def __init__(self, native): self.native, self.calls = native, 0
        def execute(self, *_args, **_kwargs): pass
        def fetchall(self):
            self.calls += 1
            return [] if self.calls == 1 else self.native

    receipt = deploy.apply_release_migrations(Cursor([("20260928", [path.read_text()])]), manifest, tmp_path)
    assert receipt["applied"] == [] and receipt["skipped"] == manifest
    with pytest.raises(RuntimeError, match="native migration hash"):
        deploy.apply_release_migrations(Cursor([("20260928", ["SELECT changed;"]) ]), manifest, tmp_path)


@pytest.fixture
def reconciliation_ledger(tmp_path, monkeypatch):
    import hashlib

    baseline_path = "sql/reconciliation/20261004_production_schema_reconciliation.sql"
    baseline = tmp_path / baseline_path
    baseline.parent.mkdir(parents=True)
    baseline.write_text("-- immutable baseline\nSELECT 1;\n")
    migrations = tmp_path / "sql/migrations"
    migrations.mkdir()
    for version in ("20260926", "20261005", "20261006"):
        (migrations / f"{version}_change.sql").write_text(f"SELECT {version};\n")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    manifest = deploy.candidate_migration_manifest(migrations)
    baseline_row = (baseline_path, "20261004", hashlib.sha256(b'["SELECT 1"]').hexdigest())

    class Cursor:
        def __init__(self, private=None, native=None):
            self.private = [baseline_row] if private is None else private
            self.native = [("20261004", ["SELECT 1"])] if native is None else native
            self.statements = []
            self.reads = 0

        def execute(self, statement, params=None):
            self.statements.append((statement, params))

        def fetchall(self):
            self.reads += 1
            return self.private if self.reads == 1 else self.native

    return migrations, manifest, baseline_row, Cursor


@pytest.mark.parametrize("suffix_length", (0, 1, 2))
def test_reconciliation_baseline_skips_history_without_fabricating_receipts(reconciliation_ledger, suffix_length):
    migrations, manifest, baseline, Cursor = reconciliation_ledger
    suffix = [tuple(item[key] for key in ("path", "version", "sha256")) for item in manifest[1:1 + suffix_length]]
    cursor = Cursor(private=[baseline, *suffix])

    receipt = deploy.apply_release_migrations(cursor, manifest, migrations)

    assert receipt["candidate"] == manifest
    assert [item["version"] for item in receipt["skipped"]] == ["20260926", "20261005", "20261006"][:1 + suffix_length]
    assert [item["version"] for item in receipt["applied"]] == ["20261005", "20261006"][suffix_length:]
    inserts = [params for statement, params in cursor.statements if statement.startswith("INSERT")]
    assert [params[1] for params in inserts] == ["20261005", "20261006"][suffix_length:]
    assert not any(statement == "SELECT 20260926;\n" for statement, _params in cursor.statements)


@pytest.mark.parametrize("corruption", (
    "private_missing", "native_missing", "private_hash", "native_hash", "private_version",
    "lookalike", "duplicate_version", "suffix_gap", "suffix_hash", "older_private", "native_extra",
    "candidate_version_collision", "baseline_file_drift",
))
def test_reconciliation_baseline_rejects_unproven_coverage_before_migration_bodies(reconciliation_ledger, corruption):
    migrations, manifest, baseline, Cursor = reconciliation_ledger
    private, native = [baseline], [("20261004", ["SELECT 1"])]
    as_tuple = lambda item: tuple(item[key] for key in ("path", "version", "sha256"))
    if corruption == "private_missing": private = []
    elif corruption == "native_missing": native = []
    elif corruption == "private_hash": private = [(baseline[0], baseline[1], "0" * 64)]
    elif corruption == "native_hash": native = [("20261004", ["SELECT 2"])]
    elif corruption == "private_version": private = [(baseline[0], "20261003", baseline[2])]
    elif corruption == "lookalike": private = [(baseline[0].replace("production_schema", "other_schema"), baseline[1], baseline[2])]
    elif corruption == "duplicate_version": private.append(("sql/migrations/20261004_fake.sql", baseline[1], baseline[2]))
    elif corruption == "suffix_gap": private.append(as_tuple(manifest[2]))
    elif corruption == "suffix_hash": private.append((manifest[1]["path"], manifest[1]["version"], "0" * 64))
    elif corruption == "older_private": private.append(as_tuple(manifest[0]))
    elif corruption == "native_extra": native.append(("20260926", ["SELECT 20260926"]))
    elif corruption == "candidate_version_collision":
        (migrations / "20261004_collision.sql").write_text("SELECT 4;")
        manifest = deploy.candidate_migration_manifest(migrations)
    elif corruption == "baseline_file_drift":
        (deploy.ROOT / baseline[0]).write_text("SELECT 2;")
    cursor = Cursor(private=private, native=native)

    with pytest.raises(RuntimeError, match="migration|reconciliation"):
        deploy.apply_release_migrations(cursor, manifest, migrations)

    assert not any(statement.startswith(("INSERT", "SELECT 2026")) for statement, _params in cursor.statements)


def test_post_deploy_restoration_precedes_cleanup_even_when_cleanup_fails():
    calls = []
    artifact = {"repo_root": "/safe/rollback", "commit_sha": "a" * 40, "source_sha256": "b" * 64}

    def restore(*_args):
        calls.append("restore")
        return {"gateway": {"status": "restored"}}

    def cleanup(*_args):
        calls.append("cleanup")
        raise RuntimeError("filesystem cleanup failed")

    with pytest.raises(RuntimeError, match="gateway restored"):
        deploy.restore_gateway_after_release_failure(PROJECT_REF, ADMIN_URL, artifact, restorer=restore, releaser=cleanup)
    assert calls == ["restore", "cleanup"]


def test_isolated_rollback_drill_measures_the_captured_gateway_bytes(tmp_path):
    source = tmp_path / "checkout/supabase/functions/market-briefing-gateway"
    source.mkdir(parents=True)
    (source / "index.ts").write_text("export default 1\n")
    digest = deploy._tree_sha256(source)
    drill = deploy.execute_isolated_gateway_rollback_drill({"repo_root": tmp_path / "checkout", "commit_sha": "a" * 40, "source_sha256": digest})
    assert drill["status"] == "verified"
    assert drill["source_sha256"] == digest
    assert drill["started_at"] <= drill["completed_at"]


def test_isolated_rollback_drill_uses_the_restore_path_and_removes_failed_candidate(tmp_path):
    source = tmp_path / "checkout/supabase/functions/market-briefing-gateway"
    source.mkdir(parents=True)
    (source / "index.ts").write_text("export const gateway = 'prior'\n")
    digest = deploy._tree_sha256(source)
    drill = deploy.execute_isolated_gateway_rollback_drill({"repo_root": tmp_path / "checkout", "commit_sha": "a" * 40, "source_sha256": digest})
    assert drill["commit_sha"] == "a" * 40
    assert drill["deploy_command"] == "functions deploy"
    assert drill["candidate_removed"] is True


def test_recovery_metadata_archive_has_only_the_normalized_download_paths(tmp_path):
    # upload-artifact receives the directory itself.  Its download therefore
    # has exactly one recovery-metadata root, not a duplicated directory.
    root = tmp_path / "downloaded-artifact/recovery-metadata"; root.mkdir(parents=True)
    (root / "rollback-capture.json").write_text("{}")
    (root / "release-state.json").write_text("{}")
    assert deploy.recovery_metadata_members(tmp_path / "downloaded-artifact") == {
        "recovery-metadata/rollback-capture.json", "recovery-metadata/release-state.json",
    }
    (root / "unexpected").write_text("x")
    with pytest.raises(RuntimeError, match="archive layout"):
        deploy.recovery_metadata_members(tmp_path / "downloaded-artifact")


def test_migration_statement_hash_handles_multiple_ordered_statements_and_duplicate_versions(tmp_path):
    first = tmp_path / "202609120001_first.sql"; first.write_text("SELECT 'a;';\nSELECT 2;\n")
    second = tmp_path / "202609120002_second.sql"; second.write_text("-- comment\nSELECT 3;\n")
    manifest = deploy.candidate_migration_manifest(tmp_path)
    assert len(manifest) == 2 and manifest[0]["sha256"] == deploy.migration_statements_sha256(["SELECT 'a;';", "SELECT 2;"])
    duplicate = tmp_path / "202609120001_duplicate.sql"; duplicate.write_text("SELECT 4;")
    with pytest.raises(RuntimeError, match="globally unique"):
        deploy.candidate_migration_manifest(tmp_path)


def test_native_supabase_statement_receipts_are_compared_without_joining_or_reparsing():
    from scripts import verify_owner_dashboard_deployment as verifier

    candidate = deploy.normalize_migration_statements(" -- receipt comment\n SELECT 1; /* ignored */\n SELECT 2; ")
    assert candidate == ["SELECT 1", "SELECT 2"]
    assert deploy.migration_statements_sha256(candidate) == deploy.migration_statements_sha256(
        ["\n SELECT 1  ", "SELECT 2\n"]
    )
    assert deploy.migration_statements_sha256(
        deploy.normalize_migration_statements("SELECT 1; SELECT 2;")
    ) == deploy.migration_statements_sha256(["SELECT 1", "SELECT 2"])
    assert verifier.migration_statements_sha256(["SELECT 1", "SELECT 2"]) == deploy.migration_statements_sha256(candidate)


def test_shared_durable_lease_blocks_new_release_and_allows_recovery_takeover_after_lock():
    release_owner = "release-123456789"
    recovery_owner = "recovery-123456789"

    class Cursor:
        def __init__(self, row): self.row, self.calls, self.rowcount = row, [], 1
        def execute(self, statement, params=None): self.calls.append((statement, params))
        def fetchall(self): return [self.row]

    blocked = Cursor((release_owner, "release", "recovery_required", True))
    with pytest.raises(RuntimeError, match="remains unresolved"):
        deploy.acquire_durable_release_lease(blocked, "release-987654321", "release")
    takeover = Cursor((release_owner, "release", "recovery_required", True))
    deploy.acquire_protected_release_lock(takeover)
    deploy.acquire_durable_release_lease(takeover, recovery_owner, "recovery")
    assert "pg_advisory_lock" in takeover.calls[0][0]
    assert takeover.calls[-1][1] == (recovery_owner, "recovery")


def test_durable_lease_allows_matching_recovery_when_a_rerun_has_not_taken_ownership():
    class Cursor:
        def __init__(self): self.calls, self.rowcount = [], 1
        def execute(self, statement, params=None): self.calls.append((statement, params))
        def fetchall(self): return [("release-123456789-1", "release", "recovery_required", True)]

    cursor = Cursor()
    deploy.acquire_durable_release_lease(cursor, "recovery-123456789-1", "recovery")
    assert cursor.calls[-1][1] == ("recovery-123456789-1", "recovery")


@pytest.mark.parametrize(
    ("current_owner", "current_state", "requested_owner"),
    (
        ("release-123456789-2", "recovery_required", "recovery-123456789-1"),
        ("release-123456789-2", "resolved", "recovery-123456789-1"),
        ("release-123456790-1", "resolved", "recovery-123456789-9"),
    ),
)
def test_durable_lease_rejects_recovery_older_than_the_current_canonical_attempt(current_owner, current_state, requested_owner):
    class Cursor:
        def __init__(self): self.calls, self.rowcount = [], 1
        def execute(self, statement, params=None): self.calls.append((statement, params))
        def fetchall(self): return [(current_owner, "release", current_state, current_state == "recovery_required")]

    cursor = Cursor()
    with pytest.raises(RuntimeError, match="newer protected release attempt"):
        deploy.acquire_durable_release_lease(cursor, requested_owner, "recovery")
    assert len(cursor.calls) == 2


def test_durable_lease_allows_recovery_for_the_current_canonical_attempt():
    class Cursor:
        def __init__(self): self.calls, self.rowcount = [], 1
        def execute(self, statement, params=None): self.calls.append((statement, params))
        def fetchall(self): return [("release-123456789-2", "release", "resolved", False)]

    cursor = Cursor()
    deploy.acquire_durable_release_lease(cursor, "recovery-123456789-2", "recovery")
    assert cursor.calls[-1][1] == ("recovery-123456789-2", "recovery")


def test_durable_lease_rejects_an_older_release_after_a_newer_resolved_attempt():
    class Cursor:
        def __init__(self): self.calls, self.rowcount = [], 1
        def execute(self, statement, params=None): self.calls.append((statement, params))
        def fetchall(self): return [("release-123456790-1", "release", "resolved", False)]

    cursor = Cursor()
    with pytest.raises(RuntimeError, match="newer protected release attempt"):
        deploy.acquire_durable_release_lease(cursor, "release-123456789-9", "release")
    assert len(cursor.calls) == 2


def test_candidate_dry_run_installs_dependencies_and_uses_only_protected_vite_values(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    app = root / "apps/web/src/app"; app.mkdir(parents=True)
    (app / "App.tsx").write_text("\n".join(f'path="/{surface}"' for surface in deploy.V1_SURFACES))
    migrations = root / "sql/migrations"; migrations.mkdir(parents=True)
    (migrations / "20260928_candidate.sql").write_text("SELECT 1;\n")
    monkeypatch.setattr(deploy, "verify_git_release", lambda *_args, **_kwargs: "a" * 40)
    commands = []
    def runner(command, *, cwd, **options):
        commands.append((command, options.get("env", {})))
        if command[:2] == ["npm", "run"]:
            output = cwd / "apps/web/dist"; output.mkdir(parents=True); (output / "index.html").write_text("ok")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    receipt = deploy.run_protected_candidate_dry_run(
        project_ref=PROJECT_REF, owner_user_id=OWNER_ID, allowed_origin=ORIGIN, site_origin=ORIGIN,
        candidate_sha="a" * 40, reviewed_sha="a" * 40,
        publishable_key="sb_publishable_abcdefghijklmnopqrstuvwx", repo_root=root, runner=runner,
    )
    assert commands[0][0] == ["npm", "ci", "--ignore-scripts"]
    assert commands[1][1]["VITE_SUPABASE_URL"] == f"https://{PROJECT_REF}.supabase.co"
    assert commands[1][1]["VITE_DASHBOARD_API_URL"].endswith("/owner-dashboard-api")
    assert commands[1][1]["VITE_SUPABASE_PUBLISHABLE_KEY"].startswith("sb_publishable_")
    assert "POSTGRES_URL" not in commands[0][1]
    assert "SUPABASE_SERVICE_ROLE_KEY" not in commands[1][1]
    assert receipt["request_plan"]["telegram_mutations"] == 0


def test_migration_ledger_accepts_the_contiguous_private_suffix_created_after_migration(tmp_path):
    path = tmp_path / "20260928_native.sql"; path.write_text("SELECT 28;\n")
    manifest = deploy.candidate_migration_manifest(tmp_path)

    class Cursor:
        def __init__(self): self.calls = 0
        def execute(self, *_args, **_kwargs): pass
        def fetchall(self):
            self.calls += 1
            if self.calls == 1:
                return [(manifest[0]["path"], manifest[0]["version"], manifest[0]["sha256"])]
            return []

    receipt = deploy.apply_release_migrations(Cursor(), manifest, tmp_path)
    assert receipt["applied"] == [] and receipt["skipped"] == manifest


def test_deploy_and_release_verifiers_share_the_complete_candidate_migration_manifest():
    from scripts.verify_owner_dashboard_deployment import verify_release_artifact_receipts

    manifest = deploy.candidate_migration_manifest()
    deployment = {
        "migrations": manifest,
        "functions": [
            {"function": "market-briefing-gateway", "git_sha": "a" * 40, "function_version": 1, "source_sha256": "b" * 64},
            {"function": "owner-dashboard-api", "git_sha": "a" * 40, "function_version": 1, "source_sha256": "c" * 64},
            {"function": "telegram-portfolio", "git_sha": "a" * 40, "function_version": 1, "source_sha256": "e" * 64},
        ],
        "static_assets": {"status": "verified", "candidate_sha": "a" * 40, "asset_hashes": ["d" * 64]},
    }
    verified = verify_release_artifact_receipts("a" * 40, deployment, manifest)
    assert "20260926" in verified["migration_version"]
    assert "20260927" in verified["migration_version"]
    deployment["migrations"] = [*manifest[:-1], {**manifest[-1], "sha256": "0" * 64}]
    with pytest.raises(RuntimeError, match="migration"):
        verify_release_artifact_receipts("a" * 40, deployment, manifest)


def test_changed_function_deploy_updates_gateway_and_creates_dashboard(tmp_path):
    commands = []
    inventories = iter([
        '[{"name":"market-briefing-gateway","version":17}]',
        '[{"name":"market-briefing-gateway","version":18}]',
        '[{"name":"market-briefing-gateway","version":18}]',
        '[{"name":"market-briefing-gateway","version":18},{"name":"owner-dashboard-api","version":1}]',
        '[{"name":"telegram-portfolio","version":4}]',
        '[{"name":"telegram-portfolio","version":5}]',
    ])

    def runner(command, **_options):
        commands.append(command)
        output = next(inventories) if "list" in command else "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.deploy_changed_functions(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert [row["function"] for row in receipt] == list(deploy.CHANGED_FUNCTIONS)
    deployed = [command[command.index("deploy") + 1] for command in commands if "deploy" in command]
    assert deployed == list(deploy.CHANGED_FUNCTIONS)
    assert "telegram-portfolio" in str(commands)


def test_existing_dashboard_upgrade_advances_version_without_requiring_absence(tmp_path):
    versions = iter([3, 4])
    def runner(command, **kwargs):
        output = '[{"name":"owner-dashboard-api","version":%d}]' % next(versions) if "list" in command else "ok"
        return type("Result", (), {"returncode": 0, "stdout": output})()
    result = deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert result["rollback_function_version"] == 3 and result["function_version"] == 4


def test_secret_manifest_uses_a_private_file_and_never_command_arguments(tmp_path, monkeypatch):
    observed = {}
    monkeypatch.setattr(deploy.tempfile, "gettempdir", lambda: str(tmp_path))

    def runner(command, **_options):
        secret_path = Path(command[command.index("--env-file") + 1])
        observed["mode"] = stat.S_IMODE(secret_path.stat().st_mode)
        observed["contents"] = secret_path.read_text()
        observed["command"] = command
        observed["path"] = secret_path
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    deploy.publish_dashboard_secrets(
        PROJECT_REF,
        {
            "DASHBOARD_DATABASE_URL": DATABASE_URL,
            "DASHBOARD_OWNER_USER_ID": OWNER_ID,
            "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
        },
        runner,
    )
    assert observed["mode"] == 0o600
    assert DATABASE_URL in observed["contents"]
    assert DATABASE_URL not in " ".join(observed["command"])
    assert not observed["path"].exists()


def test_function_deploy_is_pinned_and_returns_a_bounded_receipt(tmp_path):
    commands = []
    list_count = 0

    def runner(command, **_options):
        nonlocal list_count
        commands.append(command)
        if "list" in command:
            list_count += 1
            output = "[]" if list_count == 1 else '[{"name":"owner-dashboard-api","version":1}]'
        else:
            output = "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert receipt["git_sha"] == "a" * 40
    assert receipt["function_version"] == 1
    assert receipt["rollback_function_version"] is None
    assert any(command[-2:] == ["--no-verify-jwt", "--use-api"] for command in commands)
    assert all("service_role" not in " ".join(command).lower() for command in commands)


def test_upgrade_rejects_a_platform_version_that_does_not_advance(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        return type("Result", (), {
            "returncode": 0,
            "stdout": '[{"name":"owner-dashboard-api","version":4}]',
            "stderr": "",
        })()

    with pytest.raises(RuntimeError, match="did not advance"):
        deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert any("deploy" in command for command in commands)


def test_failed_initial_canary_deletes_only_the_new_dashboard_function(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    receipt = deploy.rollback_initial_function(PROJECT_REF, tmp_path, runner)
    assert receipt == {
        "status": "rolled_back", "function": "owner-dashboard-api",
        "dashboard_secrets_unset": list(deploy.DASHBOARD_SECRET_NAMES),
    }
    assert commands == [
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "secrets", "unset",
            *deploy.DASHBOARD_SECRET_NAMES, "--project-ref", PROJECT_REF, "--yes",
        ],
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "functions", "list",
            "--project-ref", PROJECT_REF, "--output", "json",
        ],
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "functions", "delete",
            "owner-dashboard-api", "--project-ref", PROJECT_REF, "--yes",
        ],
    ]


def test_rollback_skips_delete_when_no_dashboard_function_exists(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        output = "[]" if "list" in command else "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.rollback_initial_function(PROJECT_REF, tmp_path, runner)
    assert receipt["status"] == "rolled_back"
    assert not any("delete" in command for command in commands)


def test_release_rollback_restores_the_verified_prior_gateway(tmp_path, monkeypatch):
    source = tmp_path / "supabase/functions/market-briefing-gateway"
    source.mkdir(parents=True)
    (source / "index.ts").write_text("export const prior = true;\n")
    source_hash = deploy._tree_sha256(source)
    monkeypatch.setattr(deploy, "rollback_initial_deployment", lambda *_args, **_kwargs: {
        "status": "rolled_back", "function": "owner-dashboard-api",
        "dashboard_secrets_unset": list(deploy.DASHBOARD_SECRET_NAMES),
        "runtime_login": {"status": "disabled", "login": False, "memberships": 0},
    })
    monkeypatch.setattr(deploy, "_deploy_named_function", lambda *_args, **_kwargs: {
        "source_sha256": source_hash, "function_version": 19,
    })
    receipt = deploy.restore_gateway_and_rollback_initial_dashboard(
        PROJECT_REF, ADMIN_URL,
        {"repo_root": tmp_path, "commit_sha": "a" * 40, "source_sha256": source_hash},
    )
    assert receipt["gateway"] == {
        "status": "restored", "commit_sha": "a" * 40,
        "source_sha256": source_hash, "function_version": 19,
    }


def test_predeployment_gateway_bytes_are_retained_and_rehashed_before_cleanup(tmp_path):
    root = tmp_path / "checkout"
    gateway = root / "supabase/functions/market-briefing-gateway"
    gateway.mkdir(parents=True)
    (gateway / "index.ts").write_text("export const prior = true;\n")
    evidence = tmp_path / "protected-evidence"
    artifact = {"repo_root": root, "commit_sha": "a" * 40, "source_sha256": deploy._tree_sha256(gateway)}
    receipt = deploy.retain_gateway_rollback_artifact(artifact, evidence)
    assert (evidence / "gateway-source/index.ts").read_text() == "export const prior = true;\n"
    assert receipt["source_sha256"] == artifact["source_sha256"]
    assert receipt["commit_sha"] == artifact["commit_sha"]
    (gateway / "index.ts").write_text("tampered")
    with pytest.raises(RuntimeError, match="hash"):
        deploy.retain_gateway_rollback_artifact(artifact, tmp_path / "other-evidence")


def test_gateway_restore_precedes_dashboard_cleanup_failure(tmp_path, monkeypatch):
    source = tmp_path / "supabase/functions/market-briefing-gateway"
    source.mkdir(parents=True)
    (source / "index.ts").write_text("export const prior = true;\n")
    source_hash = deploy._tree_sha256(source)
    events = []
    monkeypatch.setattr(deploy, "_deploy_named_function", lambda *_args, **_kwargs: events.append("restore") or {"source_sha256": source_hash, "function_version": 19})
    monkeypatch.setattr(deploy, "rollback_initial_deployment", lambda *_args, **_kwargs: events.append("cleanup") or (_ for _ in ()).throw(RuntimeError("cleanup")))
    with pytest.raises(RuntimeError, match="cleanup"):
        deploy.restore_gateway_and_rollback_initial_dashboard(PROJECT_REF, ADMIN_URL, {"repo_root": tmp_path, "commit_sha": "a" * 40, "source_sha256": source_hash})
    assert events == ["restore", "cleanup"]


def test_every_post_gateway_failure_uses_the_captured_gateway_restore_artifact():
    artifact = {"repo_root": "/verified/rollback", "commit_sha": "a" * 40, "source_sha256": "b" * 64}
    calls = []

    def restorer(project_ref, admin_url, received_artifact):
        calls.append((project_ref, admin_url, received_artifact))
        return {
            "status": "rolled_back", "function": "owner-dashboard-api",
            "gateway": {"status": "restored", "commit_sha": "a" * 40, "source_sha256": "b" * 64, "function_version": 19},
        }

    receipt = deploy.rollback_after_gateway_change(
        PROJECT_REF, ADMIN_URL, artifact, restorer=restorer,
    )
    assert receipt["gateway"]["status"] == "restored"
    assert calls == [(PROJECT_REF, ADMIN_URL, artifact)]


def test_rollback_attempts_edge_cleanup_even_if_runtime_login_disable_fails():
    events = []

    def connector(*_args, **_kwargs):
        events.append("disable")
        raise RuntimeError("database unavailable")

    def edge_rollback(*_args, **_kwargs):
        events.append("edge")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="rollback was incomplete"):
        deploy.rollback_initial_deployment(
            PROJECT_REF,
            "postgresql://admin:secret@example.com/postgres",
            edge_rollback=edge_rollback,
            connector=connector,
        )
    assert events == ["disable", "edge"]


def test_post_publication_deploy_failure_rolls_back_before_propagating():
    events = []

    def publisher(*_args, **_kwargs):
        events.append("publish")

    def deployer(*_args, **_kwargs):
        events.append("deploy")
        raise RuntimeError("function deploy failed")

    def rollback(*_args, **_kwargs):
        events.append("rollback")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="function deploy failed"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=lambda *_args, **_kwargs: events.append("preflight"),
            publisher=publisher,
            deployer=deployer,
            rollback=rollback,
        )
    assert events == ["preflight", "publish", "deploy", "rollback"]


def test_secret_publication_failure_runs_fail_closed_rollback():
    events = []

    def publisher(*_args, **_kwargs):
        events.append("publish")
        raise RuntimeError("secret publication failed")

    with pytest.raises(RuntimeError, match="secret publication failed"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=lambda *_args, **_kwargs: events.append("preflight"),
            publisher=publisher,
            deployer=lambda *_args, **_kwargs: events.append("deploy"),
            rollback=lambda *_args, **_kwargs: events.append("rollback"),
        )
    assert events == ["preflight", "publish", "rollback"]


def test_existing_function_stops_before_secret_publication_or_rollback():
    events = []

    def preflight(*_args, **_kwargs):
        events.append("preflight")
        raise RuntimeError("initial dashboard function already exists")

    with pytest.raises(RuntimeError, match="already exists"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=preflight,
            publisher=lambda *_args, **_kwargs: events.append("publish"),
            deployer=lambda *_args, **_kwargs: events.append("deploy"),
            rollback=lambda *_args, **_kwargs: events.append("rollback"),
        )
    assert events == ["preflight"]


def test_canary_failure_invokes_rollback_before_propagating():
    events = []

    def verifier(*_args):
        events.append("canary")
        raise RuntimeError("production canary failed")

    def rollback(*_args):
        events.append("rollback")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="production canary failed"):
        deploy.verify_initial_deployment_or_rollback(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            verifier=verifier, rollback=rollback,
        )
    assert events == ["canary", "rollback"]


def test_post_deploy_canary_keeps_runtime_database_url_and_auth_token_out_of_receipt():
    observed = {}

    def token_factory(project_url, owner_email, redirect_origin, service_key, publishable_key):
        observed["auth"] = (project_url, owner_email, redirect_origin, service_key, publishable_key)
        return "owner-access-token"

    def source_collector(database_url, api_url, run_id):
        observed["source"] = (database_url, api_url, run_id)
        return {"source": "receipt"}

    def canary(api_url, origin, token, *, source_reader):
        observed["canary"] = (api_url, origin, token)
        assert source_reader(OWNER_ID) == {"source": "receipt"}
        return {
            "status": "verified", "source_reconciliation": "verified",
            "financial_write_routes": 0, "brokerage_authority": "none",
            "friend_invitations": "disabled",
        }

    def session_revoker(project_url, token, publishable_key):
        observed["revoked"] = (project_url, token, publishable_key)

    receipt = deploy.run_post_deploy_canary(
        PROJECT_REF, ORIGIN, DATABASE_URL, OWNER_EMAIL := "owner@example.com",
        "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
        token_factory=token_factory, source_collector=source_collector, canary=canary,
        session_revoker=session_revoker,
    )
    assert receipt["status"] == "verified"
    assert DATABASE_URL not in str(receipt)
    assert OWNER_EMAIL not in str(receipt)
    assert "owner-access-token" not in str(receipt)
    assert observed["source"][0] == DATABASE_URL
    assert observed["revoked"][1] == "owner-access-token"


def test_post_deploy_canary_revokes_owner_session_when_canary_fails():
    events = []

    def canary(*_args, **_kwargs):
        events.append("canary")
        raise RuntimeError("receipt mismatch")

    def session_revoker(*_args):
        events.append("revoke")

    with pytest.raises(RuntimeError, match="receipt mismatch"):
        deploy.run_post_deploy_canary(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            token_factory=lambda *_args: "owner-access-token",
            source_collector=lambda *_args: {},
            canary=canary,
            session_revoker=session_revoker,
        )
    assert events == ["canary", "revoke"]


def test_post_deploy_canary_rejects_an_incomplete_receipt():
    with pytest.raises(RuntimeError, match="production canary"):
        deploy.run_post_deploy_canary(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            token_factory=lambda *_args: "owner-token",
            source_collector=lambda *_args: {},
            canary=lambda *_args, **_kwargs: {"status": "verified", "source_reconciliation": "missing"},
            session_revoker=lambda *_args: None,
        )
