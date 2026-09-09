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
from psycopg import sql
import pytest

from scripts import release_components as release


def adapter_module():
    from scripts import configured_native_release_adapter
    return configured_native_release_adapter


class Supabase:
    def __init__(self):
        self.functions = {name: {"id": name + "-id", "slug": name, "version": 3,
            "verify_jwt": False,
            "entrypoint_path": f"file:///tmp/function/source/supabase/functions/{name}/index.ts",
            "import_map": False,
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
                "verify_jwt": cfg["verify_jwt"],
                "entrypoint_path": f"file:///tmp/function/source/supabase/functions/{name}/index.ts",
                "import_map": False,
                "files": {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}}
        elif args[:2] == ["functions", "delete"]:
            self.functions.pop(args[2], None)
        elif args[:2] == ["secrets", "list"]:
            output = json.dumps([{"name": name, "value": hashlib.sha256(value.encode()).hexdigest()} for name, value in self.secrets.items()])
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
        "release_run_id": "123", "release_run_attempt": "1"}, runner=platform, environment={
            "DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps(platform.secrets)}, **kwargs)


def test_native_backend_factory_is_lazy_and_performs_no_platform_operation(monkeypatch):
    module = adapter_module()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("no CLI during construction"))
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: pytest.fail("no DB during construction"))
    adapter = release.load_native_release_adapter({"project_ref": "p" * 20})
    assert isinstance(adapter, module.NativeReleaseAdapter)


def test_supabase_cli_subprocess_receives_only_allowlisted_runtime_and_token_values():
    platform = Supabase()
    captured = []
    def runner(command, **options):
        captured.append(options["env"])
        return platform(command, **options)
    adapter = adapter_module().NativeReleaseAdapter(
        {"project_ref": "p" * 20, "candidate_sha": "a" * 40}, runner=runner,
        environment={"PATH": "/usr/bin", "HOME": "/tmp/home",
            "SUPABASE_ACCESS_TOKEN": "token", "POSTGRES_URL": "private-admin",
            "SUPABASE_SERVICE_ROLE_KEY": "private-service",
            "RELEASE_RECOVERY_KEY": "private-recovery"},
    )

    adapter.managed_secret_digests()

    assert captured == [{"PATH": "/usr/bin", "HOME": "/tmp/home",
        "SUPABASE_ACCESS_TOKEN": "token"}]


def test_backend_receipt_requires_an_explicit_absolute_evidence_directory():
    adapter = native(Supabase())
    with pytest.raises(RuntimeError, match="evidence directory"):
        adapter._evidence_root()


def test_backend_plan_rejects_missing_evidence_parent_before_database_or_platform_io(tmp_path):
    platform = Supabase()
    adapter = native(
        platform,
        connector=lambda *_args, **_kwargs: pytest.fail(
            "database access must follow evidence preflight"
        ),
    )
    adapter.context.update(
        {
            "evidence_directory": str(tmp_path / "missing"),
            "allowed_origin": "https://owner.example",
            "owner_user_id": "owner",
            "candidate_sha": "a" * 40,
        }
    )

    with pytest.raises(RuntimeError, match="evidence directory"):
        adapter.plan(adapter.context)

    assert platform.calls == []


def test_upgrade_plan_preserves_existing_runtime_credential_and_database_url(monkeypatch):
    platform = Supabase()
    database_url = (
        "postgresql://stock_agent_dashboard_runtime."
        + "p" * 20
        + ":existing-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    platform.secrets = {
        "DASHBOARD_ALLOWED_ORIGINS": "https://owner.example",
        "DASHBOARD_DATABASE_URL": database_url,
        "DASHBOARD_OWNER_USER_ID": "owner",
    }
    adapter = native(platform)
    adapter.context.update({"allowed_origin": "https://owner.example", "owner_user_id": "owner"})
    prior_role = {
        "exists": True,
        "identity": adapter_module().RUNTIME_ROLE,
        "version": "role-version",
        "configuration": {
            "attributes": {
                "rolsuper": False, "rolinherit": True, "rolcreaterole": False,
                "rolcreatedb": False, "rolcanlogin": True, "rolreplication": False,
                "rolbypassrls": False, "rolconnlimit": -1, "rolvaliduntil": None,
            },
            "memberships": [{
                "role": adapter_module().PRIVILEGE_ROLE, "grantor": "postgres",
                "admin_option": False, "inherit_option": True, "set_option": True,
            }],
            "settings": [{"database": "", "setconfig": ["search_path=pg_catalog, public"]}],
        },
        "files": {},
        "values": {"password_verifier": "SCRAM-SHA-256$4096:existing$stored:server"},
    }
    prior_role["version"] = hashlib.sha256(release.canonical([
        prior_role["configuration"], prior_role["values"],
    ])).hexdigest()

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, *_args): return self
        def fetchone(self): return {"name": "postgres"}

    monkeypatch.setattr(adapter, "_evidence_root", lambda: None)
    monkeypatch.setattr(adapter, "_connection", lambda: Connection())
    monkeypatch.setattr(adapter, "_capture_role", lambda: copy.deepcopy(prior_role))
    monkeypatch.setattr(
        "scripts.verify_personal_stock_agent_v1.git_function_runtime",
        lambda _root, _sha, name: (
            {"index.ts": f"export const name = '{name}';\n".encode()},
            {"verify_jwt": False, "entrypoint": "index.ts", "import_map": None},
        ),
    )

    candidates = adapter.plan(adapter.context)

    assert candidates["runtime-role"] == prior_role
    assert candidates["dashboard-secrets"] == adapter.original["dashboard-secrets"]
    assert candidates["dashboard-secrets"]["values"]["DASHBOARD_DATABASE_URL"] == database_url


def test_upgrade_plan_refuses_to_rotate_a_live_role_when_existing_database_url_is_missing(monkeypatch):
    platform = Supabase()
    del platform.secrets["DASHBOARD_DATABASE_URL"]
    adapter = native(platform)
    adapter.context.update({"allowed_origin": "https://owner.example", "owner_user_id": "owner"})
    prior_role = {
        "exists": True, "identity": adapter_module().RUNTIME_ROLE, "version": "role-version",
        "configuration": {
            "attributes": {
                "rolsuper": False, "rolinherit": True, "rolcreaterole": False,
                "rolcreatedb": False, "rolcanlogin": True, "rolreplication": False,
                "rolbypassrls": False, "rolconnlimit": -1, "rolvaliduntil": None,
            },
            "memberships": [], "settings": [],
        },
        "files": {},
        "values": {"password_verifier": "SCRAM-SHA-256$4096:existing$stored:server"},
    }

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, *_args): return self
        def fetchone(self): return {"name": "postgres"}

    monkeypatch.setattr(adapter, "_evidence_root", lambda: None)
    monkeypatch.setattr(adapter, "_connection", lambda: Connection())
    monkeypatch.setattr(adapter, "_capture_role", lambda: copy.deepcopy(prior_role))

    with pytest.raises(RuntimeError, match="existing dashboard database credential"):
        adapter.plan(adapter.context)


def test_first_function_apply_probes_candidate_runtime_before_remote_write(monkeypatch):
    platform = Supabase()
    adapter = native(platform)
    database_url = (
        "postgresql://stock_agent_dashboard_runtime."
        + "p" * 20
        + ":existing-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    adapter.candidate_database_url = database_url
    events = []
    monkeypatch.setattr(adapter, "_verify_candidate_runtime", lambda value: events.append(("ready", value)))
    monkeypatch.setattr(adapter, "_write_function", lambda name, _candidate: events.append(("write", name)))
    monkeypatch.setattr(adapter, "capture", lambda name: {
        "exists": True, "identity": name + "-id", "version": "4", "configuration": {
            "verify_jwt": False, "entrypoint": "index.ts", "import_map": None,
        }, "files": {"index.ts": base64.b64encode(b"candidate").decode()}, "values": {},
    })
    candidate = {
        "exists": True, "identity": None, "version": None, "configuration": {
            "verify_jwt": False, "entrypoint": "index.ts", "import_map": None,
        }, "files": {"index.ts": base64.b64encode(b"candidate").decode()}, "values": {},
    }

    adapter.apply("market-briefing-gateway", candidate)
    adapter.apply("owner-dashboard-api", candidate)

    assert events == [
        ("ready", database_url),
        ("write", "market-briefing-gateway"),
        ("write", "owner-dashboard-api"),
    ]


def test_candidate_runtime_probe_rejects_private_database_errors_without_leaking_url():
    database_url = (
        "postgresql://stock_agent_dashboard_runtime."
        + "p" * 20
        + ":do-not-leak-this-password-12345@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )

    def connector(*_args, **_kwargs):
        raise psycopg.OperationalError("private pooler response do-not-leak-this-password-12345")

    adapter = native(Supabase(), connector=connector)

    with pytest.raises(RuntimeError, match="candidate dashboard database credential is not ready") as failure:
        adapter._verify_candidate_runtime(database_url)

    assert "do-not-leak" not in str(failure.value)


def test_candidate_runtime_probe_authenticates_read_only_identity_and_projection(monkeypatch):
    database_url = (
        "postgresql://stock_agent_dashboard_runtime."
        + "p" * 20
        + ":existing-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    projection = {
        "intelligence_version": 2, "run_id": None, "data_as_of": None,
        "themes": [], "companies": [], "evidence": [], "source_health": [],
        "coverage": {"mode": "bounded", "complete_market_coverage": False},
        "reference": {"state": "unavailable"},
        "scope": {"research_only": True, "market_wide": True},
        "backlog": {"available": 0, "returned": 0, "deferred": 0, "byte_truncated": False},
        "omissions": [],
        "boundaries": {
            "research_only": True, "execution_disabled": True, "valuation_unavailable": True,
        },
    }
    rows = iter([
        {
            "database_user": adapter_module().RUNTIME_ROLE,
            "transaction_read_only": "on",
        },
        {"projection": projection},
    ])
    statements = []

    class Result:
        def fetchone(self): return next(rows)

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, *_args):
            statements.append(statement)
            return None if statement.startswith("SET ") else Result()

    def connector(value, **options):
        assert value == database_url
        assert options["connect_timeout"] == 15
        return Connection()

    adapter = native(Supabase(), connector=connector)
    monkeypatch.setattr(
        "scripts.verify_owner_dashboard_role.verify_dashboard_role",
        lambda _connection: {
            "status": "verified", "write_privileges": 0,
            "application_function_execute": 0, "owned_objects": 0,
        },
    )

    adapter._verify_candidate_runtime(database_url)

    assert statements[0].startswith("SET TRANSACTION")
    assert "statement_timeout" in statements[1]
    assert "lock_timeout" in statements[2]
    assert "current_user" in statements[3]
    assert "read_owner_intelligence_v2(1)" in statements[4]


def test_candidate_runtime_probe_bounds_a_blocking_projection_and_suppresses_error(monkeypatch):
    database_url = (
        "postgresql://stock_agent_dashboard_runtime."
        + "p" * 20
        + ":existing-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    )
    statements = []

    class Result:
        def fetchone(self):
            return {"database_user": adapter_module().RUNTIME_ROLE, "transaction_read_only": "on"}

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, statement, *_args):
            statements.append(statement)
            if "read_owner_intelligence_v2" in statement:
                raise psycopg.errors.QueryCanceled("private blocking query detail")
            return None if statement.startswith("SET ") else Result()

    monkeypatch.setattr(
        "scripts.verify_owner_dashboard_role.verify_dashboard_role",
        lambda _connection: {
            "status": "verified", "write_privileges": 0,
            "application_function_execute": 0, "owned_objects": 0,
        },
    )
    adapter = native(Supabase(), connector=lambda *_args, **_kwargs: Connection())

    with pytest.raises(RuntimeError, match="candidate dashboard database credential is not ready") as failure:
        adapter._verify_candidate_runtime(database_url)

    assert "private blocking" not in str(failure.value)
    assert any("statement_timeout" in statement for statement in statements)
    assert any("lock_timeout" in statement for statement in statements)


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


@pytest.mark.parametrize("row", [None, {}, {"name": None}, {"name": ""}, {"name": 7}])
def test_native_secret_inventory_rejects_malformed_platform_rows(row):
    platform = Supabase()
    adapter = native(platform)
    adapter.runner = lambda _command, **_kwargs: SimpleNamespace(
        returncode=0,
        stdout=json.dumps([row]),
        stderr="",
    )
    with pytest.raises(RuntimeError, match="secret inventory"):
        adapter.capture("dashboard-secrets")


def test_native_function_path_preserves_nested_standard_prefixes_after_source_root():
    name = "market-briefing-gateway"
    nested = f"nested/supabase/functions/{name}/index.ts"
    value = f"file:///tmp/runtime/source/supabase/functions/{name}/{nested}"
    assert adapter_module().function_path(value, name) == nested


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
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("UPDATE public.stock_agent_release_mutation_lease SET owner='release-123', state='recovery_required'")
    return platform, adapter


def test_dashboard_authority_audits_superuser_edges_and_unreachable_schema_acls(database):
    from scripts.verify_owner_dashboard_role import collect_dashboard_privileges

    runtime = "stock_agent_dashboard_runtime"
    ordinary = "ordinary_incoming_dashboard_member"
    hidden_schema = "unreachable_dashboard_authority"
    with psycopg.connect(database, autocommit=True) as connection:
        administrator = connection.execute("SELECT current_user").fetchone()[0]
        assert connection.execute(
            "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user"
        ).fetchone() == (True,)
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT").format(sql.Identifier(runtime)))
        connection.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(ordinary)))
        assert connection.execute(
            "SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = %s", (ordinary,)
        ).fetchone() == (False,)
        connection.execute(
            sql.SQL("GRANT stock_agent_dashboard TO {} WITH ADMIN FALSE, INHERIT TRUE, SET TRUE")
            .format(sql.Identifier(runtime))
        )
        for role in (administrator, ordinary):
            connection.execute(
                sql.SQL("GRANT stock_agent_dashboard TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
                .format(sql.Identifier(role))
            )
            connection.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE")
                .format(sql.Identifier(runtime), sql.Identifier(role))
            )
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(hidden_schema)))
        connection.execute(
            sql.SQL("CREATE TABLE {}.hidden_table(id integer, note text)")
            .format(sql.Identifier(hidden_schema))
        )
        connection.execute(
            sql.SQL("CREATE SEQUENCE {}.hidden_sequence").format(sql.Identifier(hidden_schema))
        )
        connection.execute(
            sql.SQL("GRANT SELECT ON {}.hidden_table TO stock_agent_dashboard")
            .format(sql.Identifier(hidden_schema))
        )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {}.hidden_sequence TO stock_agent_dashboard")
            .format(sql.Identifier(hidden_schema))
        )
    try:
        with psycopg.connect(database, autocommit=True) as connection:
            snapshot = collect_dashboard_privileges(connection)
            privilege_edges = {row["member"]: row for row in snapshot["privilege_members"]}
            runtime_edges = {row["member"]: row for row in snapshot["runtime_members"]}
            assert privilege_edges[administrator]["member_superuser"] is True
            assert runtime_edges[administrator]["member_superuser"] is True
            assert ordinary in {row["member"] for row in snapshot["privilege_members"]}
            assert ordinary in {row["member"] for row in snapshot["runtime_members"]}
            assert not any(key.startswith(hidden_schema + ".") for key in snapshot["table_privileges"])
            assert not any(key.startswith(hidden_schema + ".") for key in snapshot["column_privileges"])
            assert not any(key.startswith(hidden_schema + ".") for key in snapshot["sequence_privileges"])

            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(runtime)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    sql.SQL("SELECT id FROM {}.hidden_table").format(sql.Identifier(hidden_schema))
                )
            connection.execute("RESET ROLE")
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO stock_agent_dashboard")
                .format(sql.Identifier(hidden_schema))
            )

            reachable = collect_dashboard_privileges(connection)
            assert reachable["other_schema_privileges"] == {hidden_schema: {"USAGE"}}
            assert reachable["table_privileges"][hidden_schema + ".hidden_table"] == {"SELECT"}
            assert reachable["column_privileges"][hidden_schema + ".hidden_table"] == {"id", "note"}
            assert reachable["sequence_privileges"][hidden_schema + ".hidden_sequence"] == {
                "SELECT", "USAGE",
            }
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(runtime)))
            assert connection.execute(
                sql.SQL("SELECT id FROM {}.hidden_table").format(sql.Identifier(hidden_schema))
            ).fetchall() == []
            connection.execute("RESET ROLE")
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(hidden_schema)))
            for role in (ordinary, runtime):
                connection.execute(
                    sql.SQL("REVOKE stock_agent_dashboard FROM {}").format(sql.Identifier(role))
                )
            for role in (ordinary, administrator):
                connection.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(runtime), sql.Identifier(role)
                    )
                )
            connection.execute(
                sql.SQL("REVOKE stock_agent_dashboard FROM {}").format(
                    sql.Identifier(administrator)
                )
            )
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(ordinary)))
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime)))


def test_supabase_project_admin_creator_edges_have_exact_non_runtime_shape(database):
    from scripts.verify_owner_dashboard_role import (
        _is_administrative_incoming_edge,
        collect_dashboard_privileges,
    )

    runtime = "stock_agent_dashboard_runtime"
    project_admin = "postgres"
    other_creator = "other_dashboard_role_creator"
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} LOGIN CREATEROLE").format(sql.Identifier(project_admin))
        )
        connection.execute(sql.SQL("CREATE ROLE {} CREATEROLE").format(sql.Identifier(other_creator)))
        for role in (project_admin, other_creator):
            connection.execute(
                sql.SQL(
                    "GRANT stock_agent_dashboard TO {} "
                    "WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
                ).format(sql.Identifier(role))
            )
        connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(project_admin)))
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT").format(sql.Identifier(runtime)))
        connection.execute(
            sql.SQL(
                "GRANT stock_agent_dashboard TO {} "
                "WITH ADMIN FALSE, INHERIT TRUE, SET TRUE"
            ).format(sql.Identifier(runtime))
        )
        connection.execute("RESET ROLE")
        connection.execute(
            sql.SQL(
                "GRANT {} TO {} WITH ADMIN TRUE, INHERIT FALSE, SET FALSE"
            ).format(sql.Identifier(runtime), sql.Identifier(other_creator))
        )

    try:
        with psycopg.connect(database, autocommit=True) as connection:
            snapshot = collect_dashboard_privileges(connection)
            privilege_edges = {row["member"]: row for row in snapshot["privilege_members"]}
            runtime_edges = {row["member"]: row for row in snapshot["runtime_members"]}
            expected_project_admin_edge = {
                "member": project_admin,
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
                "member_superuser": False,
                "member_createrole": True,
            }
            assert privilege_edges[project_admin] == expected_project_admin_edge
            assert runtime_edges[project_admin] == expected_project_admin_edge
            assert _is_administrative_incoming_edge(privilege_edges[project_admin])
            assert _is_administrative_incoming_edge(runtime_edges[project_admin])
            assert not _is_administrative_incoming_edge(privilege_edges[other_creator])
            assert not _is_administrative_incoming_edge(runtime_edges[other_creator])

        with psycopg.connect(database + " user=postgres", autocommit=True) as project_connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                project_connection.execute("SET ROLE stock_agent_dashboard")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                project_connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(runtime)))
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(runtime)))
            for role in (project_admin, other_creator):
                connection.execute(
                    sql.SQL("REVOKE stock_agent_dashboard FROM {}").format(sql.Identifier(role))
                )
                connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


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


def test_runtime_role_update_works_through_hosted_non_superuser_without_privileged_alter(database):
    """Supabase's hosted postgres role cannot ALTER SUPERUSER attributes."""
    platform = Supabase()
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("CREATE ROLE hosted_release_admin CREATEROLE")
        connection.execute("GRANT SELECT ON pg_catalog.pg_authid TO hosted_release_admin")
        connection.execute(
            "GRANT stock_agent_dashboard TO hosted_release_admin WITH ADMIN OPTION"
        )
        connection.execute("SET ROLE hosted_release_admin")
        connection.execute(
            "CREATE ROLE stock_agent_dashboard_runtime LOGIN NOINHERIT "
            "CONNECTION LIMIT 7 PASSWORD 'prior-long-password-for-test'"
        )
        connection.execute("RESET ROLE")

    def hosted_connector(_url, **options):
        connection = psycopg.connect(database, **options)
        connection.execute("SET ROLE hosted_release_admin")
        return connection

    adapter = native(platform, connector=hosted_connector)
    adapter.environment["POSTGRES_URL"] = "hosted-admin"
    try:
        prior = adapter.capture("runtime-role")
        candidate = copy.deepcopy(prior)
        candidate["configuration"]["attributes"]["rolinherit"] = True
        candidate["configuration"]["attributes"]["rolconnlimit"] = -1
        candidate["values"]["password_verifier"] = adapter_module().scram_verifier(
            "new-long-password-for-test"
        )

        deployed = adapter.apply("runtime-role", candidate)

        assert deployed["configuration"]["attributes"]["rolinherit"] is True
        assert deployed["configuration"]["attributes"]["rolsuper"] is False
        assert deployed["configuration"]["attributes"]["rolbypassrls"] is False
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("DROP ROLE IF EXISTS stock_agent_dashboard_runtime")
            connection.execute("REVOKE SELECT ON pg_catalog.pg_authid FROM hosted_release_admin")
            connection.execute("DROP ROLE IF EXISTS hosted_release_admin")


@pytest.mark.parametrize("prior_password", [None, "md5" + "a" * 32])
def test_runtime_role_recovery_restores_valid_disabled_or_legacy_prior_credentials(
    database, prior_password,
):
    platform, adapter = database_adapter(database)
    with psycopg.connect(database, autocommit=True) as connection:
        password = "NULL" if prior_password is None else "'" + prior_password + "'"
        connection.execute(
            "CREATE ROLE stock_agent_dashboard_runtime NOLOGIN NOINHERIT PASSWORD "
            + password
        )
    try:
        prior = adapter.capture("runtime-role")
        assert prior["values"]["password_verifier"] == prior_password
        with pytest.raises(RuntimeError, match="credential or limit"):
            adapter.apply("runtime-role", prior)
        candidate = copy.deepcopy(prior)
        candidate["configuration"]["attributes"]["rolcanlogin"] = True
        candidate["configuration"]["attributes"]["rolinherit"] = True
        candidate["values"]["password_verifier"] = adapter_module().scram_verifier(
            "new-long-password-for-test"
        )

        adapter.apply("runtime-role", candidate)
        adapter.restore("runtime-role", prior)

        assert adapter.capture("runtime-role") == prior
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("DROP ROLE IF EXISTS stock_agent_dashboard_runtime")


def test_native_encrypted_retention_is_committed_bound_and_recoverable_in_new_process(database, tmp_path):
    from cryptography.fernet import Fernet
    platform, adapter = database_adapter(database)
    adapter.context["lease_owner"] = "release-123"
    key = Fernet.generate_key(); adapter.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    journal = {"release_context": dict(adapter.context), "prior": "private-value"}
    sink = release.EncryptedJournal(tmp_path / "state.enc", key, retain=adapter.retain)
    sink(journal)
    second = adapter_module().NativeReleaseAdapter(adapter.context, environment=adapter.environment)
    raw = second.recover_retained(123, 1)
    assert b"private-value" not in raw
    assert json.loads(Fernet(key).decrypt(raw)) == journal
    adapter.context["lease_owner"] = "unrelated"
    with pytest.raises(RuntimeError, match="held protected lease"): sink(journal)


def test_retained_encrypted_journal_is_selected_and_authenticated_by_run_attempt(database, tmp_path):
    """A delayed recovery for attempt 1 must never load attempt 2's journal."""
    from cryptography.fernet import Fernet

    platform, first = database_adapter(database)
    key = Fernet.generate_key(); first.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    first.context.update({"lease_owner": "release-123-1", "release_run_attempt": "1"})
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("UPDATE public.stock_agent_release_mutation_lease SET owner='release-123-1'")
    first_state = {"release_context": dict(first.context), "prior": "attempt-one"}
    release.EncryptedJournal(tmp_path / "first.enc", key, retain=first.retain)(first_state)

    second_context = {**first.context, "lease_owner": "release-123-2", "release_run_attempt": "2"}
    second = adapter_module().NativeReleaseAdapter(second_context, environment=first.environment)
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("UPDATE public.stock_agent_release_mutation_lease SET owner='release-123-2'")
    second_state = {"release_context": dict(second.context), "prior": "attempt-two"}
    release.EncryptedJournal(tmp_path / "second.enc", key, retain=second.retain)(second_state)

    delayed_first = adapter_module().NativeReleaseAdapter(first.context, environment=first.environment)
    recovered = delayed_first.recover_retained(123, 1)
    assert json.loads(Fernet(key).decrypt(recovered)) == first_state
    missing_attempt = adapter_module().NativeReleaseAdapter(
        {**first.context, "release_run_attempt": "3"}, environment=first.environment
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        missing_attempt.recover_retained(123, 3)
    # Even a corrupted row that claims to be attempt 1 must be rejected when
    # its authenticated journal says attempt 2.
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("UPDATE public.stock_agent_component_recovery_journals SET run_attempt='1' WHERE run_attempt='2'")
    with pytest.raises(RuntimeError, match="identity mismatch"):
        delayed_first.recover_retained(123, 1)


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
    def attest_recovery(self, name, prior, candidate, current): return current == candidate
    def release_receipt(self, candidate, prior, current, static):
        return {"candidate_sha": candidate, "captured_components": list(prior), "readback_components": list(current)}


@pytest.mark.parametrize("first_install", [False, True])
@pytest.mark.parametrize("boundary", ["preflight", *release.BACKEND_COMPONENTS, "verification"])
def test_native_production_engine_failure_boundaries(database, tmp_path, monkeypatch, first_install, boundary):
    from cryptography.fernet import Fernet
    from scripts import build_owner_dashboard_static, verify_personal_stock_agent_v1
    platform, adapter = database_adapter(database)
    if first_install:
        platform.functions = {}; platform.secrets = {}
        adapter.environment["DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"] = "{}"
    else:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("CREATE ROLE stock_agent_dashboard_runtime LOGIN PASSWORD 'old-password-at-least-24-characters'")
        platform.secrets["DASHBOARD_DATABASE_URL"] = (
            "postgresql://stock_agent_dashboard_runtime."
            + "p" * 20
            + ":old-password-at-least-24-characters@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
        )
        adapter.environment["DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"] = json.dumps(platform.secrets)
    evidence_directory = tmp_path / "capture"
    evidence_directory.mkdir()
    adapter.context.update({"allowed_origin": "https://owner.example", "site_origin": "https://owner.example",
        "owner_user_id": "owner", "lease_owner": "release-123",
        "evidence_directory": str(evidence_directory)})
    adapter.environment["SUPAVISOR_SESSION_URL"] = "postgresql://postgres.pppppppppppppppppppp:admin-template-password@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    key = Fernet.generate_key(); adapter.environment["RELEASE_RECOVERY_KEY"] = key.decode()
    original = {name: adapter.capture(name) for name in release.BACKEND_COMPONENTS}
    monkeypatch.setattr(build_owner_dashboard_static, "build_static_release", lambda *a, **k: {"status": "verified"})
    monkeypatch.setattr(adapter, "_verify_candidate_runtime", lambda _database_url: None)
    monkeypatch.setattr(verify_personal_stock_agent_v1, "git_files", lambda *a: {"index.ts": b"candidate"})
    monkeypatch.setattr(
        verify_personal_stock_agent_v1,
        "git_function_runtime",
        lambda *a: (
            {"index.ts": b"candidate"},
            {"verify_jwt": False, "entrypoint": "index.ts", "import_map": None},
        ),
    )
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
        if "to_regclass" in sql:
            return [{"present": True}]
        if "relrowsecurity AS rls_enabled" in sql:
            return [{"rls_enabled": True, "reader_is_not_owner": True,
                "unrestricted_select": True, "no_restrictive_filter": True}]
        checked.append(params[0])
        return [{"readable": readable if params[0] == "public." + table else True,
                 "writable": writable if params[0] == "public." + table else False}]
    source.query = query
    with pytest.raises(RuntimeError, match="lacks SELECT or has write authority"): source.__enter__()
    assert "public." + table in checked and connection.closed


def test_protected_dry_run_reader_records_only_tables_present_before_migration(monkeypatch):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def rollback(self): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(
        f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres",
        "p" * 20, pre_migration_baseline=True,
    )
    def query(statement, params=()):
        if "current_user AS role" in statement:
            return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
                "rolbypassrls": False, "server": "127.0.0.1", "port": 5432,
                "database": "postgres"}]
        if "to_regclass" in statement:
            table = params[0].removeprefix("public.")
            return [{"present": table not in evidence.PRE_MIGRATION_ABSENT_TABLES}]
        if "has_table_privilege" in statement:
            table = params[0].removeprefix("public.")
            return [{"readable": table not in evidence.PRE_MIGRATION_UNREADABLE_TABLES,
                "writable": False}]
        if "relrowsecurity AS rls_enabled" in statement:
            return [{"rls_enabled": True, "reader_is_not_owner": True,
                "unrestricted_select": True, "no_restrictive_filter": True}]
        if "to_jsonb" in statement:
            return []
        raise AssertionError(statement)
    source.query = query
    with source:
        snapshot = source.dry_run_snapshot()
    deferred = set(evidence.PRE_MIGRATION_ABSENT_TABLES) | set(
        evidence.PRE_MIGRATION_UNREADABLE_TABLES
    )
    assert set(snapshot["tables"]) == set(evidence.READ_TABLES) - deferred
    assert snapshot["pre_migration_omissions"] == {
        "reason": "candidate migrations have not been applied",
        "absent_tables": list(evidence.PRE_MIGRATION_ABSENT_TABLES),
        "unreadable_tables": list(evidence.PRE_MIGRATION_UNREADABLE_TABLES),
    }


def test_pre_migration_reader_rejects_unexpected_missing_baseline_table(monkeypatch):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(
        f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres",
        "p" * 20, pre_migration_baseline=True,
    )
    def query(statement, params=()):
        if "current_user AS role" in statement:
            return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
                "rolbypassrls": False, "server": "127.0.0.1", "port": 5432,
                "database": "postgres"}]
        table = params[0].removeprefix("public.")
        if "to_regclass" in statement:
            return [{"present": table != "holdings" and table not in evidence.PRE_MIGRATION_ABSENT_TABLES}]
        if "has_table_privilege" in statement:
            return [{"readable": table not in evidence.PRE_MIGRATION_UNREADABLE_TABLES,
                "writable": False}]
        if "relrowsecurity AS rls_enabled" in statement:
            return [{"rls_enabled": True, "reader_is_not_owner": True,
                "unrestricted_select": True, "no_restrictive_filter": True}]
        raise AssertionError(statement)
    source.query = query
    with pytest.raises(RuntimeError, match="pre-migration release reader baseline mismatch"):
        source.__enter__()
    assert connection.closed


def test_pre_migration_reader_accepts_fully_migrated_retry_state(monkeypatch):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def rollback(self): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(
        f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres",
        "p" * 20, pre_migration_baseline=True,
    )
    def query(statement, params=()):
        if "current_user AS role" in statement:
            return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
                "rolbypassrls": False, "server": "127.0.0.1", "port": 5432,
                "database": "postgres"}]
        if "to_regclass" in statement:
            return [{"present": True}]
        if "has_table_privilege" in statement:
            return [{"readable": True, "writable": False}]
        if "relrowsecurity AS rls_enabled" in statement:
            return [{"rls_enabled": True, "reader_is_not_owner": True,
                "unrestricted_select": True, "no_restrictive_filter": True}]
        if "to_jsonb" in statement:
            return []
        raise AssertionError(statement)
    source.query = query
    with source:
        snapshot = source.dry_run_snapshot()
    assert set(snapshot["tables"]) == set(evidence.READ_TABLES)
    assert snapshot["pre_migration_omissions"] == {
        "reason": "candidate migrations are already applied",
        "absent_tables": [],
        "unreadable_tables": [],
    }


@pytest.mark.parametrize("policy_field", [
    "rls_enabled", "reader_is_not_owner", "unrestricted_select",
    "no_restrictive_filter",
])
def test_protected_reader_rejects_incomplete_row_security_coverage(monkeypatch, policy_field):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(
        f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres",
        "p" * 20,
    )
    def query(statement, params=()):
        if "current_user AS role" in statement:
            return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
                "rolbypassrls": False, "server": "127.0.0.1", "port": 5432,
                "database": "postgres"}]
        if "to_regclass" in statement:
            return [{"present": True}]
        if "has_table_privilege" in statement:
            return [{"readable": True, "writable": False}]
        if "relrowsecurity AS rls_enabled" in statement:
            policy = {"rls_enabled": True, "reader_is_not_owner": True,
                "unrestricted_select": True, "no_restrictive_filter": True}
            if params[0] == "holdings":
                policy[policy_field] = False
            return [policy]
        raise AssertionError(statement)
    source.query = query
    with pytest.raises(RuntimeError, match="incomplete row security coverage"):
        source.__enter__()
    assert connection.closed


def test_normal_protected_reader_rejects_a_missing_release_table(monkeypatch):
    from scripts import protected_evidence as evidence
    class Connection:
        closed = False
        def execute(self, _sql): pass
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(evidence.psycopg, "connect", lambda *a, **k: connection)
    source = evidence.PostgresReadOnlySource(
        f"postgresql://{evidence.READER}:password@db.{'p' * 20}.supabase.co:5432/postgres",
        "p" * 20,
    )
    def query(statement, params=()):
        if "current_user AS role" in statement:
            return [{"role": evidence.READER, "read_only": "on", "rolsuper": False,
                "rolbypassrls": False, "server": "127.0.0.1", "port": 5432,
                "database": "postgres"}]
        if "to_regclass" in statement:
            return [{"present": False}]
        raise AssertionError(statement)
    source.query = query
    with pytest.raises(RuntimeError, match="release table is missing"):
        source.__enter__()
    assert connection.closed


def test_candidate_dry_run_uses_the_pre_migration_reader_mode():
    script = (release.ROOT / "scripts/collect_protected_dry_run_evidence.py").read_text()
    assert "PostgresReadOnlySource(url, project_ref, pre_migration_baseline=True)" in script


def test_release_reader_scope_repairs_every_legacy_and_enrichment_table():
    migration = (release.ROOT / "sql/migrations/20261015_release_reader_source_tables.sql").read_text()
    for table in (
        "market_source_items", "market_intelligence_run_items",
        "market_source_item_provenance", "market_run_source_item_provenance",
        "market_enrichment_selection_manifests",
        "market_enrichment_request_descriptors",
    ):
        assert f"'{table}'" in migration
    assert "GRANT SELECT ON public.%I TO stock_agent_release_reader" in migration
    assert "REVOKE ALL ON public.%I FROM stock_agent_release_reader,stock_agent_release_reader_runtime" in migration
    assert "CREATE POLICY release_evidence_select ON public.%I" in migration


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
    archive_digest = "sha256:" + hashlib.sha256(payload.getvalue()).hexdigest()
    def get(self, path, **kwargs):
        if path.endswith("/zip"): return payload.getvalue()
        if "/actions/runs/" in path:
            return {"id": 123, "head_sha": "a" * 40, "head_branch": "main", "status": "in_progress",
                    "conclusion": None, "path": ".github/workflows/owner-dashboard-release.yml",
                    "repository": {"full_name": "owner/repo"}, "event": "workflow_dispatch",
                    "name": "Protected owner dashboard release", "run_attempt": 1}
        return {"id": 17, "name": "backend-component-evidence-123-1", "digest": archive_digest,
                "expired": False, "workflow_run": {"id": 123, "head_sha": "a" * 40}}
    monkeypatch.setattr(evidence.GitHubProductionDataSource, "_get", get)
    adapter = native(Supabase()); adapter.environment["GITHUB_REPOSITORY"] = "owner/repo"
    assert adapter.artifact(17) == {"index.ts": b"downloaded"}
    adapter.context["release_run_id"] = "456"
    with pytest.raises(RuntimeError, match="protected candidate"): adapter.artifact(17)
    source = evidence.GitHubProductionDataSource("owner/repo", "p" * 20, None); source.candidate = "a" * 40
    with pytest.raises(RuntimeError, match="protected candidate"): source.artifact(17)


def single_component_journal(name, prior, candidate):
    return {"format": 1, "components": {component: {"changed": component == name,
        "prior": copy.deepcopy(prior) if component == name else adapter_module().absent(),
        "prior_sha256": hashlib.sha256(release.canonical(prior if component == name else adapter_module().absent())).hexdigest(),
        **({"candidate": copy.deepcopy(candidate)} if component == name else {})}
        for component in release.COMPONENTS}}


def secret_snapshot(values):
    return release.capture_managed_secrets([{"name": key, "digest": hashlib.sha256(value.encode()).hexdigest()}
        for key, value in values.items()], values)


@pytest.mark.parametrize("name", release.FUNCTIONS)
@pytest.mark.parametrize("first_install", [False, True])
def test_native_attestation_recovers_exact_candidate_after_lost_function_response(name, first_install):
    platform = Supabase(); adapter = native(platform)
    template = adapter.capture(name)
    if first_install: del platform.functions[name]
    prior = adapter.capture(name)
    candidate = {**template, "identity": None, "version": None,
                 "files": {"index.ts": base64.b64encode(b"candidate").decode()}}
    # The command completed, but no apply return value/assigned ID was retained.
    adapter.apply(name, candidate)
    current = adapter.capture(name)
    assert adapter.attest_recovery(name, prior, candidate, current) is True
    journal = single_component_journal(name, prior, candidate)
    release.recover_components(adapter, journal, persist=lambda _: None)
    restored = adapter.capture(name)
    assert journal["status"] == "rolled_back"
    if first_install:
        assert restored == prior and name not in platform.functions
    else:
        assert restored["identity"] == prior["identity"]
        assert restored["version"] == "5" and restored["files"] == prior["files"]


def test_native_recovery_stages_git_bound_external_support_only_for_restore(monkeypatch):
    module = adapter_module()
    platform = Supabase()
    name = "owner-dashboard-api"
    platform.functions[name]["files"] = {
        "index.ts": b'import "../../../packages/dashboard-contracts/src/index.ts";\n'
    }
    support = {
        "packages/dashboard-contracts/src/index.ts": b"export const contract = 1;\n"
    }
    deploy_support = []

    def runner(command, **options):
        if command[3:5] == ["functions", "deploy"]:
            deploy_support.append(
                (Path(options["cwd"]) / "packages/dashboard-contracts/src/index.ts").is_file()
            )
        return platform(command, **options)

    adapter = module.NativeReleaseAdapter(
        {
            "project_ref": "p" * 20,
            "candidate_sha": "a" * 40,
            "release_run_id": "123",
            "release_run_attempt": "1",
        },
        runner=runner,
        environment={"DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps(platform.secrets)},
    )
    prior = adapter.capture(name)
    candidate = {
        **copy.deepcopy(prior),
        "identity": None,
        "version": None,
        "files": {"index.ts": base64.b64encode(b"export {};\n").decode()},
    }
    adapter.apply(name, candidate)
    monkeypatch.setattr(
        "scripts.function_runtime_manifest.git_function_recovery_support",
        lambda *_args, **_kwargs: support,
    )
    journal = single_component_journal(name, prior, candidate)

    release.recover_components(adapter, journal, persist=lambda _value: None)

    assert deploy_support == [False, True]
    assert journal["status"] == "rolled_back"
    assert adapter.capture(name)["files"] == prior["files"]


def test_native_recovery_replays_support_bearing_restore_after_lost_response(monkeypatch):
    module = adapter_module()
    platform = Supabase()
    name = "owner-dashboard-api"
    platform.functions[name]["files"] = {
        "index.ts": b'import "../../../packages/dashboard-contracts/src/index.ts";\n'
    }
    support = {
        "packages/dashboard-contracts/src/index.ts": b"export const contract = 1;\n"
    }
    deploys = []

    def runner(command, **options):
        if command[3:5] == ["functions", "deploy"]:
            deploys.append(command[5])
        return platform(command, **options)

    adapter = module.NativeReleaseAdapter(
        {
            "project_ref": "p" * 20,
            "candidate_sha": "a" * 40,
            "release_run_id": "123",
            "release_run_attempt": "1",
        },
        runner=runner,
        environment={"DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps(platform.secrets)},
    )
    prior = adapter.capture(name)
    candidate = {
        **copy.deepcopy(prior),
        "identity": None,
        "version": None,
        "files": {"index.ts": base64.b64encode(b"export {};\n").decode()},
    }
    adapter.apply(name, candidate)
    monkeypatch.setattr(
        "scripts.function_runtime_manifest.git_function_recovery_support",
        lambda *_args, **_kwargs: support,
    )
    adapter.hydrate_recovery(name, prior, candidate)
    adapter.restore(name, prior)  # remote success whose caller response was lost
    journal = single_component_journal(name, prior, candidate)

    release.recover_components(adapter, journal, persist=lambda _value: None)

    assert deploys == [name, name, name]
    assert journal["status"] == "rolled_back"
    assert journal["components"][name]["restoration"]["version"] == "6"


@pytest.mark.parametrize(
    ("bound_deployment", "current_advance"),
    [(True, 0), (True, 2), (False, 0), (False, 2)],
)
def test_native_recovery_accepts_gapped_or_advanced_exact_git_bound_candidate_version(
    monkeypatch, bound_deployment, current_advance
):
    platform = Supabase()
    name = "owner-dashboard-api"
    platform.functions[name]["files"] = {
        "index.ts": b'import "../../../packages/dashboard-contracts/src/index.ts";\n'
    }
    support = {
        "packages/dashboard-contracts/src/index.ts": b"export const contract = 1;\n"
    }
    deploy_support = []

    def runner(command, **options):
        if command[3:5] == ["functions", "deploy"]:
            deploy_support.append(
                (Path(options["cwd"]) / "packages/dashboard-contracts/src/index.ts").is_file()
            )
        return platform(command, **options)

    adapter = adapter_module().NativeReleaseAdapter(
        {
            "project_ref": "p" * 20,
            "candidate_sha": "a" * 40,
            "release_run_id": "123",
            "release_run_attempt": "1",
        },
        runner=runner,
        environment={"DASHBOARD_PRIOR_MANAGED_SECRETS_JSON": json.dumps(platform.secrets)},
    )
    prior = adapter.capture(name)
    candidate = {
        **copy.deepcopy(prior),
        "identity": None,
        "version": None,
        "files": {"index.ts": base64.b64encode(b"candidate\n").decode()},
    }
    deployed = adapter.apply(name, candidate)
    # The successful candidate upload itself may follow a consumed ordinal.
    deployed["version"] = str(int(prior["version"]) + 2)
    # Failed server-side recovery uploads may consume versions while leaving
    # the last active candidate bytes and configuration unchanged.
    platform.functions[name]["version"] = int(deployed["version"]) + current_advance
    current = adapter.capture(name)
    monkeypatch.setattr(
        "scripts.function_runtime_manifest.git_function_recovery_support",
        lambda *_args, **_kwargs: support,
    )
    monkeypatch.setattr(
        "scripts.verify_personal_stock_agent_v1.git_function_runtime",
        lambda *_args, **_kwargs: (
            {"index.ts": b"candidate\n"},
            copy.deepcopy(deployed["configuration"]),
        ),
    )
    journal = single_component_journal(name, prior, candidate)
    if bound_deployment:
        journal["components"][name]["deployed"] = copy.deepcopy(deployed)
    attested_candidate = deployed if bound_deployment else candidate

    assert adapter.attest_recovery(name, prior, attested_candidate, current) is True
    release.recover_components(adapter, journal, persist=lambda _value: None)

    assert journal["status"] == "rolled_back"
    assert adapter.capture(name)["files"] == prior["files"]
    assert deploy_support == [False, True]


@pytest.mark.parametrize(
    "drift", [
        "regressed_version",
        "non_monotonic_deployed",
        "git_unavailable",
        "git_content",
        "git_config",
    ]
)
def test_native_advanced_function_attestation_requires_monotonic_git_bound_candidate(
    monkeypatch, drift
):
    platform = Supabase()
    name = "owner-dashboard-api"
    adapter = native(platform)
    prior = adapter.capture(name)
    candidate = {
        **copy.deepcopy(prior),
        "identity": None,
        "version": None,
        "files": {"index.ts": base64.b64encode(b"candidate\n").decode()},
    }
    deployed = adapter.apply(name, candidate)
    if drift == "non_monotonic_deployed":
        deployed["version"] = prior["version"]
    platform.functions[name]["version"] = (
        int(deployed["version"]) - 1
        if drift == "regressed_version"
        else int(deployed["version"]) + (0 if drift == "non_monotonic_deployed" else 2)
    )
    current = adapter.capture(name)
    git_files = {"index.ts": b"different\n" if drift == "git_content" else b"candidate\n"}
    git_configuration = copy.deepcopy(deployed["configuration"])
    if drift == "git_config":
        git_configuration["verify_jwt"] = not git_configuration["verify_jwt"]
    def git_runtime(*_args, **_kwargs):
        if drift == "git_unavailable":
            raise RuntimeError("missing candidate")
        return git_files, git_configuration

    monkeypatch.setattr(
        "scripts.verify_personal_stock_agent_v1.git_function_runtime", git_runtime
    )

    assert adapter.attest_recovery(name, prior, deployed, current) is False


@pytest.mark.parametrize("drift", ["git_unavailable", "git_content", "git_config"])
def test_native_unbound_advanced_function_attestation_requires_exact_git_candidate(
    monkeypatch, drift
):
    platform = Supabase()
    name = "owner-dashboard-api"
    adapter = native(platform)
    prior = adapter.capture(name)
    candidate = {
        **copy.deepcopy(prior),
        "identity": None,
        "version": None,
        "files": {"index.ts": base64.b64encode(b"candidate\n").decode()},
    }
    adapter.apply(name, candidate)
    platform.functions[name]["version"] = int(prior["version"]) + 3
    current = adapter.capture(name)
    git_files = {"index.ts": b"different\n" if drift == "git_content" else b"candidate\n"}
    git_configuration = copy.deepcopy(candidate["configuration"])
    if drift == "git_config":
        git_configuration["verify_jwt"] = not git_configuration["verify_jwt"]

    def git_runtime(*_args, **_kwargs):
        if drift == "git_unavailable":
            raise RuntimeError("missing candidate")
        return git_files, git_configuration

    monkeypatch.setattr(
        "scripts.verify_personal_stock_agent_v1.git_function_runtime", git_runtime
    )

    assert adapter.attest_recovery(name, prior, candidate, current) is False


@pytest.mark.parametrize("drift", ["content", "identity", "version"])
def test_native_function_attestation_rejects_non_candidate_drift(drift):
    platform = Supabase(); adapter = native(platform); name = release.FUNCTIONS[0]
    prior = adapter.capture(name)
    candidate = {**copy.deepcopy(prior), "identity": None, "version": None,
                 "files": {"index.ts": base64.b64encode(b"candidate").decode()}}
    adapter.apply(name, candidate)
    if drift == "content": platform.functions[name]["files"] = {"index.ts": b"unrelated"}
    elif drift == "identity": platform.functions[name]["id"] = "foreign-id"
    else: platform.functions[name]["version"] = 8
    current = adapter.capture(name)
    assert adapter.attest_recovery(name, prior, candidate, current) is False
    before = len(platform.calls)
    journal = single_component_journal(name, prior, candidate)
    with pytest.raises(RuntimeError, match="recovery remains incomplete"):
        release.recover_components(adapter, journal, persist=lambda _: None)
    assert not any(command[4] in {"deploy", "delete", "set", "unset"} for command in platform.calls[before:])
    assert journal["status"] == "recovery_required" and adapter.capture(name) == current


@pytest.mark.parametrize("state", ["partial_set", "partial_unset", "foreign_value", "foreign_absence"])
def test_native_secret_partial_proof_accepts_only_exact_prior_or_candidate_values_and_presence(state):
    platform = Supabase()
    first, second, third = release.MANAGED_SECRETS
    prior_values = {first: "old-first", second: "old-second"}
    candidate_values = {first: "new-first", third: "new-third"}
    if state == "partial_unset":
        prior_values[third] = "old-third"
        candidate_values = {first: "new-first"}  # two removals; only one has completed
    platform.secrets = copy.deepcopy(prior_values); adapter = native(platform)
    prior = adapter.capture("dashboard-secrets"); candidate = secret_snapshot(candidate_values)
    states = {"partial_set": {first: "new-first", second: "old-second"},
              "partial_unset": {first: "new-first", second: "old-second"},
              "foreign_value": {first: "foreign", second: "old-second"},
              "foreign_absence": {second: "old-second"}}
    current_values = states[state]; platform.secrets = copy.deepcopy(current_values)
    current = secret_snapshot(current_values)
    owned = state in {"partial_set", "partial_unset"}
    assert adapter.attest_recovery("dashboard-secrets", prior, candidate, current) is owned
    journal = single_component_journal("dashboard-secrets", prior, candidate)
    before = len(platform.calls)
    if owned:
        release.recover_components(adapter, journal, persist=lambda _: None)
        assert platform.secrets == prior_values and journal["status"] == "rolled_back"
    else:
        with pytest.raises(RuntimeError, match="recovery remains incomplete"):
            release.recover_components(adapter, journal, persist=lambda _: None)
        assert platform.secrets == current_values and journal["status"] == "recovery_required"
        assert not any(command[4] in {"set", "unset"} for command in platform.calls[before:])


def test_native_atomic_role_attestation_requires_complete_candidate_state(database):
    platform, adapter = database_adapter(database)
    with psycopg.connect(database, autocommit=True) as connection:
        connection.execute("CREATE ROLE stock_agent_dashboard_runtime LOGIN PASSWORD 'test-password-at-least-24-characters'")
    try:
        prior = adapter.capture("runtime-role")
        candidate = copy.deepcopy(prior); candidate["version"] = None
        candidate["configuration"]["attributes"]["rolconnlimit"] = 7
        deployed = adapter.apply("runtime-role", candidate)
        assert adapter.attest_recovery("runtime-role", prior, candidate, deployed) is True
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("ALTER ROLE stock_agent_dashboard_runtime CONNECTION LIMIT 9")
        drift = adapter.capture("runtime-role")
        assert adapter.attest_recovery("runtime-role", prior, candidate, drift) is False
        journal = single_component_journal("runtime-role", prior, candidate)
        with pytest.raises(RuntimeError, match="recovery remains incomplete"):
            release.recover_components(adapter, journal, persist=lambda _: None)
        assert adapter.capture("runtime-role") == drift
    finally:
        with psycopg.connect(database, autocommit=True) as connection:
            connection.execute("DROP ROLE stock_agent_dashboard_runtime")
