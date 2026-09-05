"""Local failure injection for exact, upgrade-aware release recovery."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import socket
import tempfile
import os
import sys

import psycopg

import pytest

from scripts import deploy_owner_dashboard_api as deploy

ROOT = Path(__file__).resolve().parents[1]
BASE = "432d647ef911ff63da427097f02a852e18038b62"
COMPONENTS = ("runtime-role", "dashboard-secrets", "market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio", "owner-web-site")


def test_unchanged_legacy_recovery_never_mutates_existing_components():
    calls = []
    result = deploy.recover_gateway_from_state(
        {"changed": False, "recovery_required_on_non_success": True, "artifact": {}},
        "p" * 20, "unused", restorer=lambda *args: calls.append(args), releaser=lambda *args: calls.append(args))
    assert calls == []
    assert result["status"] == "unchanged"


def test_audited_migrations_remain_exact_base_bytes():
    names = subprocess.check_output(["git", "ls-tree", "--name-only", BASE, "sql/migrations/"], cwd=ROOT, text=True).splitlines()
    for name in names:
        assert (ROOT / name).read_bytes() == subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT), name


def test_documented_native_baseline_reconciles_and_applies_additive_suffix():
    fixture = json.loads((ROOT / "tests/fixtures/native_ledger_20260907.json").read_text())
    native = []
    for name in subprocess.check_output(["git", "ls-tree", "--name-only", BASE, "sql/migrations/"], cwd=ROOT, text=True).splitlines():
        raw = subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT)
        version = Path(name).name.split("_", 1)[0]
        if version == "20260907":
            assert hashlib.sha256(raw).hexdigest() == fixture["file_sha256"]
            assert deploy.migration_statements_sha256(deploy.normalize_migration_statements(raw.decode())) == fixture["native_statements_sha256"]
        native.append((version, deploy.normalize_migration_statements(raw.decode())))
    class Cursor:
        def __init__(self): self.reads = 0; self.applied = []
        def execute(self, sql, params=None): self.applied.append((sql, params))
        def fetchall(self):
            self.reads += 1
            return [] if self.reads == 1 else native
    receipt = deploy.apply_release_migrations(Cursor())
    assert len(receipt["skipped"]) == len(native)
    assert receipt["applied"][-1]["version"] > "20260930"


def module():
    from scripts import release_components
    return release_components


class Platform:
    """Stateful remote model: failures happen after a real state change."""
    def __init__(self, absent=()):
        self.state = {name: {"exists": name not in absent, "identity": f"{name}-v3" if name not in absent else None,
                            "version": "3" if name not in absent else None,
                            "configuration": {"verify_jwt": False},
                            "files": {"index": "old-bytes"} if name not in absent else {},
                            "values": {"credential": "prior-secret"} if name not in absent else {}}
                      for name in COMPONENTS}
        self.state["owner-web-site"]["configuration"]["access_policy"] = {
            "access_mode": "custom", "allowed_account_user_ids": ["owner"], "external_visitor_count": 0,
            "workspace_group_ids": [], "tenant_group_ids": [],
        }
        self.original = copy.deepcopy(self.state)
        self.mutations = []
    def capture(self, name): return copy.deepcopy(self.state[name])
    def apply(self, name, candidate):
        self.mutations.append(name)
        self.state[name] = copy.deepcopy(candidate)
        return self.capture(name)
    def restore(self, name, prior):
        self.mutations.append("restore:" + name)
        self.state[name] = copy.deepcopy(prior)
    def attest_recovery(self, name, prior, candidate, current): return current == candidate


@pytest.mark.parametrize("boundary", ["preflight", *COMPONENTS])
@pytest.mark.parametrize("absent", [(), COMPONENTS])
def test_failure_after_each_mutation_restores_only_attempted_components(tmp_path, boundary, absent):
    release = module(); platform = Platform(absent)
    candidate = {name: {**copy.deepcopy(prior), "exists": True,
                        "identity": prior["identity"] if name in release.FUNCTIONS and prior["exists"] else name + "-v4", "version": "4",
                        "files": {"index": "candidate"}, "values": {"credential": "new-secret"}}
                 for name, prior in platform.state.items()}
    journal = {}
    def persist(value): journal.clear(); journal.update(copy.deepcopy(value))
    def checkpoint(name):
        if name == boundary: raise RuntimeError("injected failure")
    with pytest.raises(RuntimeError, match="injected failure"):
        release.execute_release(platform, candidate, persist=persist, checkpoint=checkpoint)
    assert platform.state == platform.original
    changed = [] if boundary == "preflight" else list(COMPONENTS[:COMPONENTS.index(boundary) + 1])
    assert platform.mutations == changed + ["restore:" + name for name in reversed(changed)]
    assert journal["status"] == "rolled_back"


def test_missing_site_transport_fails_before_any_component_capture(tmp_path):
    release = module()
    with pytest.raises(RuntimeError, match="Sites.*transport"):
        release.require_site_transport(ROOT, {})


def test_workflow_cli_reports_missing_transport_without_pythonpath_injection():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/release_components.py"), "--check-transport"],
        cwd=ROOT, env={"PATH": os.environ["PATH"], "PYTHONPATH": ""}, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Sites" in result.stderr and "transport" in result.stderr


@pytest.mark.parametrize("absent", [(), COMPONENTS])
@pytest.mark.parametrize("target", COMPONENTS)
def test_pre_mutation_apply_failure_never_restores_or_deletes(absent, target):
    release = module(); platform = Platform(absent)
    candidate = {name: {**copy.deepcopy(prior), "exists": True, "identity": name + "-v4", "version": "4",
                        "files": {"index": "candidate"}, "values": {"credential": "new-secret"}}
                 for name, prior in platform.state.items()}
    candidate = {name: value if name == target else platform.state[name] for name, value in candidate.items()}
    def fail_before_write(name, value): raise RuntimeError("before remote write")
    platform.apply = fail_before_write
    with pytest.raises(RuntimeError, match="before remote write"):
        release.execute_release(platform, candidate, persist=lambda _: None)
    assert platform.mutations == []
    assert platform.state == platform.original


def test_recovery_retry_does_not_redeploy_already_restored_function_version():
    release = module(); platform = Platform(); name = release.FUNCTIONS[0]
    prior = platform.capture(name)
    platform.state[name]["version"] = "5"
    journal = {"format": 1, "components": {component: {"changed": component == name,
        "prior": snapshot, "prior_sha256": hashlib.sha256(release.canonical(snapshot)).hexdigest()}
        for component, snapshot in platform.original.items()}}
    release.recover_components(platform, journal, persist=lambda _: None)
    assert platform.mutations == []
    assert journal["components"][name]["restoration"] == {
        "original_identity": prior["identity"], "original_version": "3", "identity": prior["identity"], "version": "5"}


@pytest.mark.parametrize("absent", [(), COMPONENTS])
@pytest.mark.parametrize("target", COMPONENTS)
def test_unrelated_drift_after_prewrite_failure_is_never_restored(absent, target):
    release = module(); platform = Platform(absent)
    candidates = copy.deepcopy(platform.state)
    candidates[target] = {**candidates[target], "exists": True, "identity": target + "-candidate", "version": "4",
                          "files": {"index": "candidate"}, "values": {"credential": "candidate"}}
    unrelated = {**copy.deepcopy(candidates[target]), "identity": target + "-unrelated", "version": "9",
                 "files": {"index": "unrelated"}, "values": {"credential": "unrelated"}}
    journal = {}
    def persist(value): journal.clear(); journal.update(copy.deepcopy(value))
    def fail_before_write(name, _candidate):
        platform.state[name] = copy.deepcopy(unrelated)  # separate actor's write, not this release
        raise RuntimeError("prewrite failure")
    platform.apply = fail_before_write
    with pytest.raises(RuntimeError, match="recovery remains incomplete"):
        release.execute_release(platform, candidates, persist=persist)
    assert platform.state[target] == unrelated
    assert platform.mutations == []
    assert journal["status"] == "recovery_required" and journal["components"][target]["changed"] is True


def recovery_journal(platform, name, candidate):
    release = module()
    return {"format": 1, "components": {component: {"changed": component == name,
        "prior": copy.deepcopy(snapshot), "prior_sha256": hashlib.sha256(release.canonical(snapshot)).hexdigest(),
        **({"candidate": copy.deepcopy(candidate)} if component == name else {})}
        for component, snapshot in platform.original.items()}}


def test_foreign_identity_with_prior_function_content_never_counts_as_restored():
    release = module(); platform = Platform(); name = release.FUNCTIONS[0]
    prior = platform.capture(name)
    candidate = {**copy.deepcopy(prior), "version": "4", "files": {"index": "candidate"}}
    platform.state[name] = {**copy.deepcopy(prior), "identity": "unrelated-id", "version": "5"}
    journal = recovery_journal(platform, name, candidate)
    with pytest.raises(RuntimeError, match="recovery remains incomplete"):
        release.recover_components(platform, journal, persist=lambda _: None)
    assert journal["status"] == "recovery_required" and journal["components"][name]["changed"] is True
    assert platform.mutations == [] and platform.state[name]["identity"] == "unrelated-id"


def test_restore_result_with_foreign_function_identity_is_not_verified():
    release = module(); platform = Platform(); name = release.FUNCTIONS[0]
    prior = platform.capture(name)
    candidate = {**copy.deepcopy(prior), "version": "4", "files": {"index": "candidate"}}
    platform.state[name] = copy.deepcopy(candidate)
    def restore(name, prior):
        platform.state[name] = {**copy.deepcopy(prior), "identity": "unrelated-restore-id", "version": "5"}
        return platform.capture(name)
    platform.restore = restore
    journal = recovery_journal(platform, name, candidate)
    with pytest.raises(RuntimeError, match="recovery remains incomplete"):
        release.recover_components(platform, journal, persist=lambda _: None)
    assert journal["status"] == "recovery_required" and journal["components"][name]["changed"] is True


def test_site_matching_candidate_bytes_without_identity_proof_cannot_be_restored():
    release = module(); platform = Platform(); name = "owner-web-site"
    prior = platform.capture(name)
    candidate = {**copy.deepcopy(prior), "identity": None, "version": None, "files": {"index": "candidate"}}
    platform.state[name] = {**copy.deepcopy(candidate), "identity": "unknown-platform-id", "version": "4"}
    journal = recovery_journal(platform, name, candidate)
    with pytest.raises(RuntimeError, match="recovery remains incomplete"):
        release.recover_components(platform, journal, persist=lambda _: None)
    assert platform.mutations == [] and journal["components"][name]["changed"] is True


def test_site_missing_recovery_attestation_blocks_before_capture_or_mutation():
    release = module(); platform = Platform()
    platform.project_id = release.site_configuration(ROOT)["project_id"]
    platform.attest_recovery = None
    platform.capture = lambda _name: pytest.fail("capture must not precede proof-capability preflight")
    with pytest.raises(RuntimeError, match="Sites.*transport"):
        release.require_site_transport(ROOT, adapter=platform)
    assert platform.mutations == []


def test_recovery_reader_attests_decision_and_comparison_tables():
    from scripts.protected_evidence import READ_TABLES
    assert {"decision_evaluations", "market_policy_comparisons"} <= set(READ_TABLES)


def test_final_component_readback_rejects_forged_candidate_hash():
    release = module()
    platform = Platform()
    prior = platform.capture("owner-dashboard-api")
    candidate = {**prior, "files": {"index": "candidate"}}
    with pytest.raises(RuntimeError, match="bytes"):
        release.verify_component_readback("owner-dashboard-api", candidate, prior)


def test_site_adapter_must_bind_configured_project_and_all_capabilities():
    release = module()
    adapter = type("Incomplete", (), {"project_id": "wrong"})()
    with pytest.raises(RuntimeError, match="Sites.*transport"):
        release.require_site_transport(ROOT, {}, adapter=adapter)


def test_capture_rejects_managed_secret_values_that_do_not_match_platform_digest():
    release = module()
    inventory = [{"name": name, "digest": hashlib.sha256(b"prior").hexdigest()} for name in deploy.DASHBOARD_SECRET_NAMES]
    with pytest.raises(RuntimeError, match="secret.*digest"):
        release.capture_managed_secrets(inventory, {name: "wrong" for name in deploy.DASHBOARD_SECRET_NAMES})


def test_secret_capture_preserves_exact_values_and_absence():
    release = module()
    name = deploy.DASHBOARD_SECRET_NAMES[0]
    captured = release.capture_managed_secrets([{"name": name, "digest": hashlib.sha256(b"prior").hexdigest()}], {name: "prior"})
    assert captured["values"] == {name: "prior"}
    assert captured["configuration"]["absent"] == list(deploy.DASHBOARD_SECRET_NAMES[1:])


def test_recovery_adapter_contract_does_not_require_a_healthy_current_site():
    release = module()
    adapter = Platform()
    adapter.project_id = release.site_configuration(ROOT)["project_id"]
    adapter.capture = lambda name: (_ for _ in ()).throw(RuntimeError("failed candidate site unavailable"))
    assert release.require_site_transport(ROOT, adapter=adapter, inspect_current=False) is adapter


def test_recovery_journal_encrypts_authenticates_and_retains_before_mutation(tmp_path):
    from cryptography.fernet import Fernet
    release = module(); retained = []
    sink = release.EncryptedJournal(tmp_path / "recovery.enc", Fernet.generate_key(), retain=retained.append)
    journal = {"password": "prior-private-value", "changed": False}
    sink(journal)
    raw = (tmp_path / "recovery.enc").read_bytes()
    assert b"prior-private-value" not in raw and retained == [raw]
    assert sink.read() == journal
    (tmp_path / "recovery.enc").write_bytes(raw[:-1] + b"X")
    with pytest.raises(RuntimeError, match="authentication"): sink.read()


def test_protected_entrypoint_missing_site_adapter_never_captures_or_mutates():
    release = module(); platform = Platform()
    with pytest.raises(RuntimeError, match="Sites.*transport"):
        release.execute_protected_release(platform, {}, repo_root=ROOT, site_adapter=None, persist=lambda _: None)
    assert platform.mutations == [] and platform.state == platform.original


@pytest.mark.parametrize("absent", [(), COMPONENTS])
@pytest.mark.parametrize("boundary", ["preflight", *COMPONENTS, "verification"])
def test_production_orchestration_uses_component_engine_and_recovers(tmp_path, absent, boundary):
    release = module(); platform = Platform(absent)
    platform.project_id = release.site_configuration(ROOT)["project_id"]
    platform.site = platform
    platform.plan = lambda context: {name: {**copy.deepcopy(old), "exists": True,
                                           "identity": old["identity"] if name in release.FUNCTIONS and old["exists"] else name + "-4", "version": "4",
                                           "files": {"index": "candidate"}, "values": {"credential": "new-secret"}}
                                     for name, old in platform.state.items()}
    retained = []
    platform.retain = retained.append
    platform.receipt = lambda candidate: {"candidate_sha": candidate}
    platform.verify = lambda receipt: None
    def checkpoint(name):
        if name == boundary: raise RuntimeError("injected failure")
    from cryptography.fernet import Fernet
    with pytest.raises(RuntimeError, match="injected failure"):
        release.run_native_release(platform, {"candidate_sha": "a" * 40},
            repo_root=ROOT, journal_path=tmp_path / "release.enc", key=Fernet.generate_key(),
            migrate=lambda: None, checkpoint=checkpoint)
    assert platform.state == platform.original
    changed = [] if boundary == "preflight" else list(COMPONENTS if boundary == "verification" else COMPONENTS[:COMPONENTS.index(boundary) + 1])
    assert platform.mutations == changed + ["restore:" + name for name in reversed(changed)]


def test_first_install_binds_identity_returned_by_platform_after_mutation():
    release = module(); platform = Platform(COMPONENTS)
    candidates = {name: {**prior, "exists": True, "identity": None, "version": None,
                         "files": {"index": "candidate"}, "values": {}}
                  for name, prior in platform.state.items()}
    def apply(name, candidate):
        platform.state[name] = {**candidate, "identity": name + "-allocated", "version": "1"}
        return platform.capture(name)
    platform.apply = apply
    result = release.execute_release(platform, candidates, persist=lambda _: None)
    assert result["status"] == "verified"
    assert all(row["identity"].endswith("-allocated") for row in result["components"])


@pytest.mark.parametrize("component", COMPONENTS)
def test_remote_mutation_then_lost_response_is_recovered(component):
    release = module(); platform = Platform()
    candidates = {name: {**copy.deepcopy(prior), "files": {"index": "candidate"}}
                  for name, prior in platform.state.items()}
    original = platform.apply
    def apply(name, candidate):
        result = original(name, candidate)
        if name == component: raise RuntimeError("response lost after remote mutation")
        return result
    platform.apply = apply
    with pytest.raises(RuntimeError, match="response lost"):
        release.execute_release(platform, candidates, persist=lambda _: None)
    assert platform.state == platform.original


def test_failed_pre_mutation_journal_retention_never_restores_untouched_components():
    release = module(); platform = Platform()
    candidates = {name: {**copy.deepcopy(prior), "files": {"index": "candidate"}}
                  for name, prior in platform.state.items()}
    writes = []
    def persist(journal):
        writes.append(copy.deepcopy(journal))
        if len(writes) == 2: raise RuntimeError("retention failed")
    with pytest.raises(RuntimeError, match="retention failed"):
        release.execute_release(platform, candidates, persist=persist)
    assert platform.mutations == [] and platform.state == platform.original


def test_actual_postgres_additive_upgrade_from_native_baseline():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()): pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="track-c-postgres-") as raw:
        root = Path(raw)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        subprocess.run([binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"], check=True, capture_output=True)
        subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "log"), "-o", f"-k {root} -h '' -p {port}", "-w", "start"], check=True, capture_output=True)
        try:
            with psycopg.connect(f"host={root} port={port} dbname=postgres") as connection:
                connection.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role; CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid'; CREATE SCHEMA supabase_migrations; CREATE TABLE supabase_migrations.schema_migrations(version text PRIMARY KEY, statements text[])")
                baseline = subprocess.check_output(["git", "show", f"{BASE}:sql/schema.sql"], cwd=ROOT).decode()
                connection.execute(baseline)
                names = subprocess.check_output(["git", "ls-tree", "--name-only", BASE, "sql/migrations/"], cwd=ROOT, text=True).splitlines()
                for name in names:
                    statements = deploy.normalize_migration_statements(subprocess.check_output(["git", "show", f"{BASE}:{name}"], cwd=ROOT).decode())
                    connection.execute("INSERT INTO supabase_migrations.schema_migrations VALUES (%s,%s)", (Path(name).name.split("_",1)[0], statements))
                with connection.cursor() as cursor:
                    receipt = deploy.apply_release_migrations(cursor)
                    assert receipt["applied"][-1]["version"] > "20260930"
                    assert deploy.apply_release_migrations(cursor)["applied"] == []
                assert connection.execute("SELECT to_regclass('public.market_policy_comparisons')").fetchone()[0]
                assert connection.execute("SELECT has_function_privilege('anon', 'public.read_market_report_decisions(uuid,uuid,jsonb)', 'EXECUTE')").fetchone() == (False,)
        finally:
            subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"], check=True, capture_output=True)
