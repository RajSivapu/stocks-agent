"""Offline platform command contracts and disposable PostgreSQL integration."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
from types import SimpleNamespace

import psycopg
import pytest

from scripts import release_components as release


def adapter_module():
    from scripts import configured_native_release_adapter
    return configured_native_release_adapter


class Supabase:
    def __init__(self):
        self.functions = {name: {"id": name + "-id", "slug": name, "version": 3,
            "verify_jwt": False, "entrypoint_path": "index.ts", "import_map_path": None,
            "files": {"index.ts": b"old\x00bytes", "deno.json": b"{}"}} for name in release.FUNCTIONS}
        self.secrets = {name: "old-" + name for name in release.MANAGED_SECRETS}
        self.calls = []
        self.fail = None
    def __call__(self, command, *, cwd, **kwargs):
        assert command[:3] == ["npx", "--yes", "supabase@2.116.0"]
        self.calls.append(command)
        args = command[3:]
        if self.fail == args[:2]:
            return SimpleNamespace(returncode=1, stdout="", stderr="private-error")
        output = ""
        if args[:2] == ["functions", "list"]:
            output = json.dumps([{k: v for k, v in row.items() if k != "files"} for row in self.functions.values()])
        elif args[:2] == ["functions", "download"]:
            root = Path(cwd) / "supabase/functions" / args[2]
            root.mkdir(parents=True)
            for name, raw in self.functions[args[2]]["files"].items():
                target = root / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
        elif args[:2] == ["functions", "deploy"]:
            import tomllib
            name = args[2]; root = Path(cwd) / "supabase/functions" / name
            cfg = tomllib.loads((Path(cwd) / "supabase/config.toml").read_text())["functions"][name]
            old = self.functions.get(name, {"version": 0})
            self.functions[name] = {"id": name + "-id", "slug": name, "version": old["version"] + 1,
                "verify_jwt": cfg["verify_jwt"], "entrypoint_path": "index.ts", "import_map_path": None,
                "files": {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}}
        elif args[:2] == ["functions", "delete"]:
            self.functions.pop(args[2], None)
        elif args[:2] == ["secrets", "list"]:
            output = json.dumps([{"name": name, "digest": hashlib.sha256(value.encode()).hexdigest()} for name, value in self.secrets.items()])
        elif args[:2] == ["secrets", "set"]:
            path = Path(args[args.index("--env-file") + 1])
            assert path.stat().st_mode & 0o777 == 0o600
            for line in path.read_text().splitlines():
                key, value = line.split("=", 1); self.secrets[key] = value
        elif args[:2] == ["secrets", "unset"]:
            for name in args[2:args.index("--project-ref")]: self.secrets.pop(name, None)
        else: raise AssertionError(command)
        return SimpleNamespace(returncode=0, stdout=output, stderr="")


def native(platform, **kwargs):
    return adapter_module().NativeReleaseAdapter({"project_ref": "p" * 20, "candidate_sha": "a" * 40,
        "release_run_id": "123"}, runner=platform, environment={
            "DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps(platform.secrets)}, **kwargs)


def test_native_factory_is_lazy_and_site_gate_precedes_every_platform_operation(monkeypatch):
    module = adapter_module()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("no CLI before Sites gate"))
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: pytest.fail("no DB before Sites gate"))
    with pytest.raises(RuntimeError, match="Sites.*transport"):
        release.load_native_release_adapter({"project_ref": "p" * 20})


@pytest.mark.parametrize("name", release.FUNCTIONS)
def test_native_function_download_apply_restore_preserves_bytes_config_and_records_new_version(name):
    platform = Supabase(); adapter = native(platform)
    prior = adapter.capture(name)
    assert base64.b64decode(prior["files"]["index.ts"]) == b"old\x00bytes"
    candidate = {**copy.deepcopy(prior), "files": {"index.ts": base64.b64encode(b"new").decode()}}
    deployed = adapter.apply(name, candidate)
    assert deployed["version"] == "4"
    restored = adapter.restore(name, prior)
    assert restored["version"] == "5"
    assert restored["files"] == prior["files"] and restored["configuration"] == prior["configuration"]
    assert adapter.capture(name) == restored
    assert all("--project-ref" in command for command in platform.calls)
    assert any(command[3:5] == ["functions", "download"] and "--use-api" in command for command in platform.calls)


@pytest.mark.parametrize("name", release.FUNCTIONS)
def test_native_first_install_deletes_only_new_function(name):
    platform = Supabase(); template = native(platform).capture(name); del platform.functions[name]
    adapter = native(platform); prior = adapter.capture(name)
    adapter.apply(name, template); adapter.restore(name, prior)
    assert adapter.capture(name) == prior
    assert set(platform.functions) == set(release.FUNCTIONS) - {name}


def test_native_download_missing_exact_config_or_bytes_fails_closed():
    platform = Supabase(); del platform.functions[release.FUNCTIONS[0]]["verify_jwt"]
    with pytest.raises(RuntimeError, match="configuration"):
        native(platform).capture(release.FUNCTIONS[0])
    assert not any(command[4] in {"deploy", "delete"} for command in platform.calls)


def test_native_secret_capture_requires_recoverable_values_and_restores_partial_absence():
    platform = Supabase(); missing = release.MANAGED_SECRETS[0]; del platform.secrets[missing]
    adapter = native(platform); prior = adapter.capture("dashboard-secrets")
    candidate = release.capture_managed_secrets([{"name": key, "digest": hashlib.sha256(b"candidate").hexdigest()}
        for key in release.MANAGED_SECRETS], {key: "candidate" for key in release.MANAGED_SECRETS})
    adapter.apply("dashboard-secrets", candidate)
    adapter.restore("dashboard-secrets", prior)
    assert adapter.capture("dashboard-secrets") == prior
    assert missing not in platform.secrets
    assert all("candidate" not in " ".join(command) for command in platform.calls)
    adapter.known_secrets = {}
    with pytest.raises(RuntimeError, match="secret.*digest"):
        adapter.capture("dashboard-secrets")


def test_native_download_detects_version_race():
    platform = Supabase(); delegate = platform.__call__
    def runner(command, **kwargs):
        result = delegate(command, **kwargs)
        if command[3:5] == ["functions", "download"]: platform.functions[command[5]]["version"] += 1
        return result
    adapter = native(platform); adapter.runner = runner
    with pytest.raises(RuntimeError, match="changed during capture"):
        adapter.capture(release.FUNCTIONS[0])


@pytest.fixture(scope="module")
def database():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl")}
    if not all(binaries.values()): pytest.skip("disposable PostgreSQL binaries unavailable")
    with tempfile.TemporaryDirectory(prefix="native-adapter-pg-") as directory:
        root = Path(directory)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
        subprocess.run([binaries["initdb"], "-D", str(root / "db"), "-A", "trust", "-E", "UTF8", "--no-locale"], check=True, capture_output=True)
        subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-l", str(root / "log"),
            "-o", f"-k {root} -h '' -p {port}", "-w", "start"], check=True, capture_output=True)
        try:
            dsn = f"host={root} port={port} dbname=postgres"
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute("CREATE ROLE stock_agent_dashboard; CREATE ROLE prior_reader")
                connection.execute("CREATE TABLE public.stock_agent_release_mutation_lease(singleton boolean PRIMARY KEY,owner text,state text)")
                connection.execute("INSERT INTO public.stock_agent_release_mutation_lease VALUES(true,'release-123','recovery_required')")
            yield dsn
        finally:
            subprocess.run([binaries["pg_ctl"], "-D", str(root / "db"), "-m", "immediate", "-w", "stop"], check=True, capture_output=True)


def database_adapter(database):
    platform = Supabase(); adapter = native(platform)
    adapter.environment["POSTGRES_URL"] = database
    return platform, adapter


def test_runtime_role_exact_attributes_verifier_memberships_and_database_settings(database):
    platform, adapter = database_adapter(database)
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("CREATE ROLE stock_agent_dashboard_runtime LOGIN NOINHERIT CONNECTION LIMIT 7 PASSWORD 'prior-long-password-for-test'")
        connection.execute("GRANT prior_reader TO stock_agent_dashboard_runtime WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
        connection.execute('ALTER ROLE stock_agent_dashboard_runtime SET search_path TO "$user", public')
        connection.execute("ALTER ROLE stock_agent_dashboard_runtime IN DATABASE postgres SET statement_timeout TO '17s'")
    try:
        prior = adapter.capture("runtime-role")
        assert prior["values"]["password_verifier"].startswith("SCRAM-SHA-256$")
        assert prior["configuration"]["attributes"]["rolvaliduntil"] is None
        candidate = copy.deepcopy(prior)
        candidate["configuration"]["attributes"]["rolinherit"] = True
        candidate["configuration"]["attributes"]["rolconnlimit"] = -1
        candidate["configuration"]["settings"] = []
        candidate["configuration"]["memberships"] = []
        candidate["values"]["password_verifier"] = adapter_module().scram_verifier("new-long-password-for-test")
        adapter.apply("runtime-role", candidate)
        adapter.restore("runtime-role", prior)
        assert adapter.capture("runtime-role") == prior
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("DROP ROLE stock_agent_dashboard_runtime")


def test_native_encrypted_retention_is_committed_bound_and_recoverable_in_new_process(database, tmp_path):
    from cryptography.fernet import Fernet
    platform, adapter = database_adapter(database)
    adapter.context["lease_owner"] = "release-123"
    key = Fernet.generate_key(); adapter.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    journal = {"release_context": dict(adapter.context), "prior": "private-value"}
    sink = release.EncryptedJournal(tmp_path / "state.enc", key, retain=adapter.retain)
    sink(journal)
    second = adapter_module().NativeReleaseAdapter(adapter.context, environment=adapter.environment)
    raw = second.recover_retained(123)
    assert b"private-value" not in raw
    assert json.loads(Fernet(key).decrypt(raw)) == journal
    adapter.context["lease_owner"] = "unrelated"
    with pytest.raises(RuntimeError, match="held protected lease"): sink(journal)


def test_new_process_secret_recovery_uses_encrypted_attempted_values():
    platform = Supabase(); adapter = native(platform)
    prior = adapter.capture("dashboard-secrets")
    values = {key: "new-value" for key in release.MANAGED_SECRETS}
    candidate = release.capture_managed_secrets([{"name": key, "digest": hashlib.sha256(value.encode()).hexdigest()}
        for key, value in values.items()], values)
    platform.secrets = values
    adapter = native(platform); adapter.environment["DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"] = "{}"
    adapter.hydrate_recovery("dashboard-secrets", prior, candidate)
    adapter.restore("dashboard-secrets", prior)
    assert adapter.capture("dashboard-secrets") == prior


class Site:
    project_id = release.site_configuration()["project_id"]
    def __init__(self):
        self.state = {"exists": True, "identity": "site-old", "version": "3", "values": {},
            "configuration": {"access_policy": {"access_mode": "custom", "allowed_account_user_ids": ["owner"],
                "external_visitor_count": 0, "workspace_group_ids": [], "tenant_group_ids": []}},
            "files": {"index.html": base64.b64encode(b"old").decode()}}
    def capture(self, _name): return copy.deepcopy(self.state)
    def plan(self, _context):
        return {**copy.deepcopy(self.state), "identity": "site-new", "version": "4",
                "files": {"index.html": base64.b64encode(b"candidate").decode()}}
    def apply(self, name, candidate): self.state = copy.deepcopy(candidate); return self.capture(name)
    def restore(self, name, prior): self.state = copy.deepcopy(prior)
    def release_receipt(self, candidate, prior, current, static):
        return {"candidate_sha": candidate, "captured_components": list(prior), "readback_components": list(current)}


@pytest.mark.parametrize("first_install", [False, True])
@pytest.mark.parametrize("boundary", ["preflight", *release.COMPONENTS, "verification"])
def test_native_production_engine_failure_boundaries(database, tmp_path, monkeypatch, first_install, boundary):
    from cryptography.fernet import Fernet
    from scripts import build_owner_dashboard_static, verify_personal_stock_agent_v1
    platform, adapter = database_adapter(database)
    site = Site(); adapter.site = site
    if first_install:
        platform.functions = {}; platform.secrets = {}
        adapter.environment["DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"] = "{}"
    else:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("CREATE ROLE stock_agent_dashboard_runtime LOGIN PASSWORD 'old-password-at-least-24-characters'")
    adapter.context.update({"allowed_origin": "https://owner.example", "site_origin": "https://owner.example",
        "owner_user_id": "owner", "lease_owner": "release-123"})
    adapter.environment["SUPAVISOR_SESSION_URL"] = "postgresql://postgres.pppppppppppppppppppp:admin-template-password@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    key = Fernet.generate_key(); adapter.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    original = {name: adapter.capture(name) for name in release.COMPONENTS if name != "owner-web-site"}
    original_site = site.capture("owner-web-site")
    monkeypatch.setattr(build_owner_dashboard_static, "build_static_release", lambda *a, **k: {"status": "verified"})
    monkeypatch.setattr(verify_personal_stock_agent_v1, "git_files", lambda *a: {"index.ts": b"candidate"})
    def checkpoint(name):
        if name == boundary: raise RuntimeError("injected native boundary " + name)
    try:
        with pytest.raises(RuntimeError, match="injected native boundary"):
            release.run_native_release(adapter, adapter.context, repo_root=release.ROOT,
                journal_path=tmp_path / "state.enc", key=key, migrate=lambda: None,
                checkpoint=checkpoint, verify_receipt=lambda receipt: None)
        journal = release.EncryptedJournal(tmp_path / "state.enc", key, retain=lambda _: None).read()
        assert journal["status"] == "rolled_back"
        for name, prior in original.items():
            current = adapter.capture(name)
            if name in release.FUNCTIONS and prior["exists"] and journal["components"][name].get("restoration"):
                restored = journal["components"][name]["restoration"]
                assert restored["original_identity"] == prior["identity"]
                assert restored["original_version"] == prior["version"] == "3"
                assert restored["version"] == current["version"] == "5"
                assert {k: v for k, v in prior.items() if k not in {"identity", "version"}} == {
                    k: v for k, v in current.items() if k not in {"identity", "version"}}
            else: assert current == prior
        assert site.state == original_site
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("DROP ROLE IF EXISTS stock_agent_dashboard_runtime")


@pytest.mark.parametrize("table", ["decision_evaluations", "market_policy_comparisons"])
@pytest.mark.parametrize("readable,writable", [(False, False), (True, True)])
def test_protected_reader_rejects_incomplete_policy_table_privileges(monkeypatch, table, readable, writable):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres", "p" * 20)
    checked = []
    def query(sql, params=()):
        if not params: return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
            "rolbypassrls": False, "server": "127.0.0.1", "port": 5432, "database": "postgres"}]
        checked.append(params[0])
        return [{"readable": readable if params[0] == "public." + table else True,
                 "writable": writable if params[0] == "public." + table else False}]
    source.query = query
    with pytest.raises(RuntimeError, match="lacks SELECT or has write authority"): source.__enter__()
    assert "public." + table in checked and connection.closed


def test_protected_workflow_passes_recoverable_prior_secret_values():
    import yaml
    workflow = yaml.safe_load((release.ROOT / ".github/workflows/owner-dashboard-release.yml").read_text())
    step = next(row for row in workflow["jobs"]["release"]["steps"] if row.get("name") == "Execute protected deployment with encrypted component recovery")
    assert step["env"]["DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"] == "${{ secrets.DASHBOARD_PRIOR_MANAGED_SECRETS_JSON }}"


def test_native_journal_has_no_supabase_default_public_grants(database):
    from cryptography.fernet import Fernet
    platform, adapter = database_adapter(database)
    key = Fernet.generate_key(); adapter.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    adapter.context["lease_owner"] = "release-123"
    state = {"release_context": dict(adapter.context)}
    adapter.retain(Fernet(key).encrypt(release.canonical(state)))
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role")
        connection.execute("GRANT ALL ON public.stock_agent_component_recovery_journals TO anon,authenticated,service_role")
    adapter.retain(Fernet(key).encrypt(release.canonical(state)))
    with psycopg.connect(database) as connection:
        for role in ("anon", "authenticated", "service_role"):
            assert connection.execute("SELECT has_table_privilege(%s,'public.stock_agent_component_recovery_journals','SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')", (role,)).fetchone() == (False,)


def test_native_secret_capture_rejects_values_that_dotenv_cannot_restore_literally():
    platform = Supabase(); platform.secrets[release.MANAGED_SECRETS[0]] = '"quoted-value"'
    with pytest.raises(RuntimeError, match="literal env-file"):
        native(platform).capture("dashboard-secrets")
    assert all(command[4] == "list" for command in platform.calls)


def test_active_release_artifact_readback_is_exact_run_bound_and_does_not_weaken_final_verifier(monkeypatch):
    import io
    import zipfile
    from scripts import protected_evidence as evidence
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive: archive.writestr("index.ts", b"downloaded")
    def get(self, path, **kwargs):
        if path.endswith("/zip"): return payload.getvalue()
        if "/actions/runs/" in path:
            return {"id": 123, "head_sha": "a" * 40, "head_branch": "main", "status": "in_progress",
                    "conclusion": None, "path": ".github/workflows/owner-dashboard-release.yml"}
        return {"expired": False, "workflow_run": {"id": 123, "head_sha": "a" * 40}}
    monkeypatch.setattr(evidence.GitHubProductionDataSource, "_get", get)
    adapter = native(Supabase()); adapter.environment["GITHUB_REPOSITORY"] = "owner/repo"
    assert adapter.artifact(17) == {"index.ts": b"downloaded"}
    adapter.context["release_run_id"] = "456"
    with pytest.raises(RuntimeError, match="protected candidate"): adapter.artifact(17)
    source = evidence.GitHubProductionDataSource("owner/repo", "p" * 20, None); source.candidate = "a" * 40
    with pytest.raises(RuntimeError, match="protected candidate"): source.artifact(17)
