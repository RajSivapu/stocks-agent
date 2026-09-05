#!/usr/bin/env python3
"""Fail-closed protected deployment for the owner-only dashboard API."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.provision_dashboard_runtime_role import (
    RUNTIME_ROLE,
    disable_dashboard_runtime_login,
    provision_dashboard_role,
    runtime_url,
)
from scripts.build_owner_dashboard_static import build_static_release
from scripts.verify_owner_dashboard_role import verify_dashboard_role
from scripts.verify_owner_dashboard_deployment import (
    collect_source_receipts,
    obtain_ephemeral_owner_access_token,
    revoke_ephemeral_owner_session,
    run_http_canary,
    verify_release_artifact_receipts,
)


MIGRATION_NAME = re.compile(r"^(?P<version>\d{8})_[a-z0-9][a-z0-9_]*\.sql$")
SUPABASE_CLI_VERSION = "2.116.0"
MIGRATION_LEDGER = "public.stock_agent_release_migration_ledger"
FUNCTION_NAME = "owner-dashboard-api"
CHANGED_FUNCTIONS = ("market-briefing-gateway", FUNCTION_NAME)
V1_SURFACES = ("portfolio", "ideas", "intelligence", "reports", "system")
DASHBOARD_SECRET_NAMES = (
    "DASHBOARD_ALLOWED_ORIGINS",
    "DASHBOARD_DATABASE_URL",
    "DASHBOARD_OWNER_USER_ID",
)
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _project_digest(project_ref: str) -> str:
    return hashlib.sha256(project_ref.encode()).hexdigest()[:16]


def _validate_origin(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or value != f"https://{parsed.netloc}"
    ):
        raise ValueError("an exact HTTPS origin without a path is required")
    return value


def _validate_database_url(value: str, project_ref: str) -> None:
    parsed = urlparse(value)
    username = unquote(parsed.username or "")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not parsed.hostname.endswith(".pooler.supabase.com")
        or parsed.port != 5432
        or username != f"stock_agent_dashboard_runtime.{project_ref}"
        or len(unquote(parsed.password or "")) < 24
        or parsed.path != "/postgres"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("a project-matched scoped Supavisor session URL on port 5432 is required")


def validate_release_database_endpoints(
    project_ref: str,
    admin_url: str,
    session_template: str,
) -> dict[str, str]:
    """Bind both privileged database endpoints to the requested project before mutation."""
    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise ValueError("a canonical Supabase project reference is required")
    parsed = urlparse(admin_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname != f"db.{project_ref}.supabase.co"
        or parsed.port != 5432
        or unquote(parsed.username or "") != "postgres"
        or len(unquote(parsed.password or "")) < 24
        or parsed.path != "/postgres"
        or parsed.params
        or parsed.fragment
        or query not in ([], [("sslmode", "require")])
    ):
        raise ValueError("a project-matched administrator database URL is required")
    candidate = runtime_url(session_template, RUNTIME_ROLE, "x" * 32)
    _validate_database_url(candidate, project_ref)
    return {"admin_database": "verified", "session_pooler": "verified"}


def _validate_role_receipt(value: Mapping[str, object]) -> None:
    expected = {
        "status": "verified",
        "runtime_role": "stock_agent_dashboard_runtime",
        "privilege_role": "stock_agent_dashboard",
        "write_privileges": 0,
        "application_function_execute": 0,
        "owned_objects": 0,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ValueError("role verifier evidence is missing or unsafe")
    if not isinstance(value.get("table_count"), int) or int(value["table_count"]) <= 0:
        raise ValueError("role verifier evidence is missing or unsafe")


def validate_static_configuration(
    project_ref: str,
    owner_user_id: str,
    allowed_origin: str,
    secret_names: Sequence[str],
) -> dict[str, object]:
    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise ValueError("a canonical Supabase project reference is required")
    if not UUID_PATTERN.fullmatch(owner_user_id):
        raise ValueError("a canonical owner UUID is required")
    origin = _validate_origin(allowed_origin)
    if tuple(sorted(secret_names)) != tuple(sorted(DASHBOARD_SECRET_NAMES)):
        raise ValueError("dashboard secret manifest is not exact")
    return {
        "project_ref_digest": _project_digest(project_ref),
        "allowed_origin": origin,
        "secret_names": list(DASHBOARD_SECRET_NAMES),
    }


def validate_deployment_configuration(
    *,
    project_ref: str,
    owner_user_id: str,
    allowed_origin: str,
    database_url: str,
    role_receipt: Mapping[str, object],
    secret_names: Sequence[str],
) -> dict[str, object]:
    safe = validate_static_configuration(project_ref, owner_user_id, allowed_origin, secret_names)
    _validate_database_url(database_url, project_ref)
    _validate_role_receipt(role_receipt)
    return {**safe, "role_status": "verified"}


def _run(command: list[str], *, cwd: Path, runner: Callable[..., object]):
    return runner(command, cwd=cwd, capture_output=True, text=True, check=False)


def verify_git_release(
    repo_root: Path = ROOT, runner: Callable[..., object] = subprocess.run,
    expected_sha: str | None = None,
) -> str:
    status = _run(["git", "status", "--porcelain=v1"], cwd=repo_root, runner=runner)
    if getattr(status, "returncode", 1) != 0 or str(getattr(status, "stdout", "")).strip():
        raise RuntimeError("deployment requires a clean working tree")
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root, runner=runner)
    local_sha = str(getattr(head, "stdout", "")).strip()
    if expected_sha is not None:
        if (not re.fullmatch(r"[0-9a-f]{40}", expected_sha)
                or getattr(head, "returncode", 1) != 0 or local_sha != expected_sha):
            raise RuntimeError("deployment candidate SHA/ref mismatch")
        return local_sha
    upstream = _run(["git", "rev-parse", "@{upstream}"], cwd=repo_root, runner=runner)
    remote_sha = str(getattr(upstream, "stdout", "")).strip()
    if getattr(head, "returncode", 1) != 0 or getattr(upstream, "returncode", 1) != 0 or local_sha != remote_sha:
        raise RuntimeError("deployment requires the exact commit to be pushed")
    if not re.fullmatch(r"[0-9a-f]{40}", local_sha):
        raise RuntimeError("deployment git receipt is malformed")
    return local_sha


def verify_reviewed_sha(candidate_sha: str, reviewed_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", reviewed_sha) or reviewed_sha != candidate_sha:
        raise RuntimeError("deployment requires independent review of the exact candidate SHA")
    return candidate_sha


def run_local_verification(repo_root: Path = ROOT, runner: Callable[..., object] = subprocess.run) -> None:
    result = _run(["env", "-u", "RUN_DB_INTEGRATION_TESTS", "npm", "run", "test:all"], cwd=repo_root, runner=runner)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("local verification suite failed")


def verify_v1_dashboard_source(repo_root: Path = ROOT) -> dict[str, object]:
    """Refuse the superseded thin dashboard before any protected mutation."""
    try:
        source = (repo_root / "apps/web/src/app/App.tsx").read_text()
    except OSError as error:
        raise RuntimeError("superseded thin dashboard source is not deployable") from error
    if any(f'path="/{surface}"' not in source for surface in V1_SURFACES):
        raise RuntimeError("superseded thin dashboard source is not deployable")
    return {"status": "verified", "primary_surfaces": list(V1_SURFACES)}


def _tree_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        digest.update(entry.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(entry.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def prepare_gateway_rollback_artifact(
    rollback_ref: str,
    expected_source_sha256: str,
    destination: Path,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Materialize and verify the exact predeployment gateway source before mutation."""
    if not re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256):
        raise ValueError("gateway rollback source SHA-256 is required")
    resolved = _run(["git", "rev-parse", f"{rollback_ref}^{{commit}}"], cwd=repo_root, runner=runner)
    commit = str(getattr(resolved, "stdout", "")).strip()
    if getattr(resolved, "returncode", 1) != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("gateway rollback ref is unavailable")
    added = _run(["git", "worktree", "add", "--detach", str(destination), commit], cwd=repo_root, runner=runner)
    if getattr(added, "returncode", 1) != 0:
        raise RuntimeError("gateway rollback artifact could not be materialized")
    source = destination / "supabase/functions/market-briefing-gateway"
    if not (source / "index.ts").is_file():
        raise RuntimeError("gateway rollback artifact has no deployable source")
    actual = _tree_sha256(source)
    if actual != expected_source_sha256:
        raise RuntimeError("gateway rollback artifact source hash mismatch")
    return {"commit_sha": commit, "source_sha256": actual, "repo_root": destination}


def release_gateway_rollback_artifact(
    artifact: Mapping[str, object], repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    path = Path(str(artifact["repo_root"]))
    result = _run(["git", "worktree", "remove", "--force", str(path)], cwd=repo_root, runner=runner)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("gateway rollback artifact cleanup failed")


def retain_gateway_rollback_artifact(artifact: Mapping[str, object], evidence_directory: Path) -> dict[str, object]:
    """Retain byte-for-byte source for a protected Actions artifact before mutation."""
    from scripts.export_recovery_bundle import exact_file
    exact_file(evidence_directory, exists=False)
    if evidence_directory.exists():
        raise RuntimeError("protected evidence destination must not already exist")
    source = Path(str(artifact["repo_root"])) / "supabase/functions/market-briefing-gateway"
    if any(path.is_symlink() for path in source.rglob("*")) or _tree_sha256(source) != artifact["source_sha256"]:
        raise RuntimeError("captured gateway source hash mismatch")
    destination = evidence_directory / "gateway-source"
    destination.mkdir(parents=True, mode=0o700)
    for path in sorted(source.rglob("*")):
        if path.is_file():
            retained = destination / path.relative_to(source)
            retained.parent.mkdir(parents=True, exist_ok=True)
            retained.write_bytes(path.read_bytes())
            retained.chmod(0o600)
    if _tree_sha256(destination) != artifact["source_sha256"]:
        raise RuntimeError("retained gateway source hash mismatch")
    receipt = {"git_sha": artifact["commit_sha"], "source_sha256": artifact["source_sha256"],
               "captured_at": datetime.now(timezone.utc).isoformat(), "source_directory": "gateway-source"}
    (evidence_directory / "rollback-capture.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def verify_gateway_rollback_preflight(
    project_ref: str, expected_version: int, artifact: Mapping[str, object],
) -> dict[str, object]:
    if isinstance(expected_version, bool) or expected_version <= 0:
        raise ValueError("positive current gateway version is required")
    observed = function_version(project_ref, "market-briefing-gateway")
    if observed != expected_version:
        raise RuntimeError("live gateway version differs from rollback receipt")
    drill = execute_isolated_gateway_rollback_drill(artifact)
    return {
        "status": "ready",
        "function_version": observed,
        "source_sha256": artifact["source_sha256"],
        "isolated_drill": drill,
    }


def execute_isolated_gateway_rollback_drill(artifact: Mapping[str, object]) -> dict[str, object]:
    """Actually stage and verify captured gateway bytes in a disposable root."""
    source = Path(str(artifact.get("repo_root", ""))) / "supabase/functions/market-briefing-gateway"
    expected = str(artifact.get("source_sha256", ""))
    if not source.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", expected) or _tree_sha256(source) != expected:
        raise RuntimeError("isolated rollback drill source is unavailable")
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="stocks-isolated-rollback-drill-") as raw:
        restored = Path(raw) / "restored-gateway"
        shutil.copytree(source, restored, symlinks=False)
        measured = _tree_sha256(restored)
        if measured != expected:
            raise RuntimeError("isolated rollback drill hash mismatch")
    completed = datetime.now(timezone.utc).isoformat()
    return {"status": "verified", "isolated": True, "source_sha256": measured,
            "started_at": started, "completed_at": completed}


def candidate_migration_manifest(migrations_directory: Path = ROOT / "sql/migrations") -> list[dict[str, str]]:
    """Discover every candidate migration in stable byte-bound order."""
    if not migrations_directory.is_dir() or migrations_directory.is_symlink():
        raise RuntimeError("candidate migration directory is unavailable")
    paths = sorted(migrations_directory.iterdir())
    if (not paths or any(not path.is_file() or path.is_symlink() or not MIGRATION_NAME.fullmatch(path.name)
                          for path in paths)):
        raise RuntimeError("candidate migration manifest is malformed")
    manifest = []
    for path in paths:
        raw = path.read_bytes()
        if not raw:
            raise RuntimeError("candidate migration is empty")
        manifest.append({
            "path": f"sql/migrations/{path.name}",
            "version": MIGRATION_NAME.fullmatch(path.name).group("version"),  # type: ignore[union-attr]
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return manifest


def apply_release_migrations(
    cursor, manifest: Sequence[Mapping[str, str]] | None = None,
    migrations_directory: Path = ROOT / "sql/migrations",
) -> dict[str, list[dict[str, str]]]:
    """Atomically ledger and apply only the byte-verified pending migrations.

    The ledger bootstrap and each migration's DDL share the caller's database
    transaction.  A retry therefore sees either a verified applied row or no
    row at all; it never replays a partially recorded migration.
    """
    manifest = list(candidate_migration_manifest() if manifest is None else manifest)
    def valid_item(row: Mapping[str, str]) -> bool:
        path = row.get("path")
        version = row.get("version")
        digest = row.get("sha256")
        return (set(row) == {"path", "version", "sha256"} and isinstance(path, str)
                and isinstance(version, str) and isinstance(digest, str)
                and path == f"sql/migrations/{Path(path).name}"
                and MIGRATION_NAME.fullmatch(Path(path).name) is not None
                and version == Path(path).name.split("_", 1)[0]
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
    if not manifest or any(not isinstance(row, Mapping) or not valid_item(row) for row in manifest):
        raise RuntimeError("candidate migration manifest is incomplete or unordered")
    expected_paths = [row["path"] for row in manifest]
    if expected_paths != sorted(expected_paths) or len(expected_paths) != len(set(expected_paths)):
        raise RuntimeError("candidate migration manifest is incomplete or unordered")
    cursor.execute(
        f"CREATE TABLE IF NOT EXISTS {MIGRATION_LEDGER} ("
        "path TEXT PRIMARY KEY, version TEXT NOT NULL, sha256 TEXT NOT NULL, "
        "applied_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(), "
        "UNIQUE (version, path))"
    )
    cursor.execute(f"SELECT path, version, sha256 FROM {MIGRATION_LEDGER} FOR UPDATE")
    prior = cursor.fetchall()
    if not isinstance(prior, list):
        raise RuntimeError("migration ledger receipt is malformed")
    known: dict[str, tuple[str, str]] = {}
    for row in prior:
        if not isinstance(row, Sequence) or len(row) != 3 or not all(isinstance(value, str) for value in row):
            raise RuntimeError("migration ledger receipt is malformed")
        path, version, digest = row
        if path in known or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RuntimeError("migration ledger receipt is malformed")
        known[path] = (version, digest)
    # A previously applied Supabase migration may not be replayed merely
    # because this private ledger is new. Bootstrap it only from the native
    # statements receipt, and reject any untraceable/extra version.
    if not known:
        cursor.execute("SELECT version, statements FROM supabase_migrations.schema_migrations ORDER BY version FOR UPDATE")
        legacy = cursor.fetchall()
        by_version: dict[str, list[Mapping[str, str]]] = {}
        for item in manifest:
            by_version.setdefault(item["version"], []).append(item)
        for row in legacy:
            if not isinstance(row, Sequence) or len(row) != 2 or not isinstance(row[0], str) or not isinstance(row[1], Sequence):
                raise RuntimeError("native migration receipt is malformed")
            version, statements = row
            candidates = by_version.get(version, [])
            if not candidates or not all(isinstance(part, str) for part in statements):
                raise RuntimeError("native migration version is not an exact candidate")
            normalized = "\n".join(statements).encode()
            matching = [item for item in candidates if hashlib.sha256(normalized).hexdigest() == item["sha256"]]
            if len(matching) != 1:
                raise RuntimeError("native migration hash mismatch")
            item = matching[0]
            cursor.execute(
                f"INSERT INTO {MIGRATION_LEDGER} (path, version, sha256) VALUES (%s, %s, %s)",
                (item["path"], item["version"], item["sha256"]),
            )
            known[item["path"]] = (item["version"], item["sha256"])
    applied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for item in manifest:
        path = migrations_directory / Path(str(item["path"])).name
        if path.is_symlink() or not path.is_file() or not MIGRATION_NAME.fullmatch(path.name):
            raise RuntimeError("candidate migration path is unsafe")
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if item["sha256"] != actual:
            raise RuntimeError("candidate migration hash mismatch")
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError("candidate migration is not UTF-8") from error
        existing = known.get(item["path"])
        if existing is not None:
            if existing != (item["version"], item["sha256"]):
                raise RuntimeError("migration ledger hash mismatch")
            skipped.append(dict(item))
            continue
        # DDL and its immutable hash receipt are deliberately issued in the
        # same transaction.  psycopg's surrounding connection context rolls
        # both back if either statement fails.
        cursor.execute(sql)
        cursor.execute(
            f"INSERT INTO {MIGRATION_LEDGER} (path, version, sha256) VALUES (%s, %s, %s)",
            (item["path"], item["version"], item["sha256"]),
        )
        applied.append(dict(item))
    return {"candidate": [dict(item) for item in manifest], "applied": applied, "skipped": skipped}


def publish_dashboard_secrets(
    project_ref: str,
    values: Mapping[str, str],
    runner: Callable[..., object] = subprocess.run,
    repo_root: Path = ROOT,
) -> None:
    if tuple(sorted(values)) != tuple(sorted(DASHBOARD_SECRET_NAMES)):
        raise ValueError("dashboard secret manifest is not exact")
    if any(not value or "\n" in value or "\r" in value for value in values.values()):
        raise ValueError("dashboard secret values must be non-empty single lines")
    descriptor, raw_path = tempfile.mkstemp(prefix="stocks-dashboard-deploy-", suffix=".env")
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for name in DASHBOARD_SECRET_NAMES:
                handle.write(f"{name}={values[name]}\n")
        result = _run(
            [
                "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "secrets", "set",
                "--env-file", str(path), "--project-ref", project_ref,
            ],
            cwd=repo_root,
            runner=runner,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError("Supabase rejected the dashboard secret manifest")
    finally:
        if path.exists():
            try:
                path.write_bytes(b"\0" * path.stat().st_size)
            finally:
                path.unlink(missing_ok=True)


def function_version(
    project_ref: str,
    function_name: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> int | None:
    listing = _run(
        [
            "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "functions", "list",
            "--project-ref", project_ref, "--output", "json",
        ],
        cwd=repo_root,
        runner=runner,
    )
    if getattr(listing, "returncode", 1) != 0:
        raise RuntimeError("dashboard function version receipt is unavailable")
    try:
        functions = json.loads(str(getattr(listing, "stdout", "")))
        row = next((item for item in functions if item.get("name") == function_name), None)
        return None if row is None else int(row["version"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("dashboard function version receipt is malformed") from error


def dashboard_function_version(
    project_ref: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> int | None:
    return function_version(project_ref, FUNCTION_NAME, repo_root, runner)


def _deploy_named_function(
    project_ref: str,
    function_name: str,
    git_sha: str,
    repo_root: Path,
    runner: Callable[..., object],
    *,
    require_existing: bool,
) -> dict[str, object]:
    prior = function_version(project_ref, function_name, repo_root, runner)
    if require_existing and prior is None:
        raise RuntimeError(f"required existing function is missing: {function_name}")
    if not require_existing and prior is not None:
        raise RuntimeError("initial dashboard function already exists")
    deployed = _run(
        [
            "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "functions", "deploy",
            function_name, "--project-ref", project_ref, "--no-verify-jwt", "--use-api",
        ],
        cwd=repo_root,
        runner=runner,
    )
    if getattr(deployed, "returncode", 1) != 0:
        raise RuntimeError(f"Supabase rejected function deployment: {function_name}")
    version = function_version(project_ref, function_name, repo_root, runner)
    if version is None or (prior is not None and version <= prior):
        raise RuntimeError(f"function version did not advance: {function_name}")
    return {
        "status": "deployed",
        "function": function_name,
        "function_version": version,
        "rollback_function_version": prior,
        "git_sha": git_sha,
        "source_sha256": _tree_sha256(repo_root / "supabase/functions" / function_name),
        "project_ref_digest": _project_digest(project_ref),
        "cli_version": SUPABASE_CLI_VERSION,
        "deployed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def deploy_changed_functions(
    project_ref: str,
    git_sha: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> list[dict[str, object]]:
    if not PROJECT_REF_PATTERN.fullmatch(project_ref) or not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise ValueError("canonical project and git receipts are required")
    return [
        _deploy_named_function(
            project_ref, function_name, git_sha, repo_root, runner,
            require_existing=function_name != FUNCTION_NAME,
        )
        for function_name in CHANGED_FUNCTIONS
    ]


def ensure_initial_function_absent(
    project_ref: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> None:
    if dashboard_function_version(project_ref, repo_root, runner) is not None:
        raise RuntimeError("initial dashboard function already exists")


def deploy_function(
    project_ref: str,
    git_sha: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    if not PROJECT_REF_PATTERN.fullmatch(project_ref) or not re.fullmatch(r"[0-9a-f]{40}", git_sha):
        raise ValueError("canonical project and git receipts are required")
    return _deploy_named_function(
        project_ref, FUNCTION_NAME, git_sha, repo_root, runner, require_existing=False,
    )


def rollback_initial_function(
    project_ref: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise ValueError("a canonical Supabase project reference is required")
    unset = _run(
        [
            "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "secrets", "unset",
            *DASHBOARD_SECRET_NAMES, "--project-ref", project_ref, "--yes",
        ],
        cwd=repo_root,
        runner=runner,
    )
    if getattr(unset, "returncode", 1) != 0:
        raise RuntimeError("dashboard rollback could not activate the configuration kill switch")
    try:
        version = dashboard_function_version(project_ref, repo_root, runner)
    except RuntimeError:
        version = -1  # Unknown is fail-closed: make the idempotent delete attempt.
    if version is not None:
        deletion = _run(
            [
                "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "functions", "delete",
                FUNCTION_NAME, "--project-ref", project_ref, "--yes",
            ],
            cwd=repo_root,
            runner=runner,
        )
        if getattr(deletion, "returncode", 1) != 0:
            raise RuntimeError("dashboard rollback could not remove the initial function")
    return {
        "status": "rolled_back",
        "function": FUNCTION_NAME,
        "dashboard_secrets_unset": list(DASHBOARD_SECRET_NAMES),
    }


def rollback_initial_deployment(
    project_ref: str,
    admin_url: str,
    *,
    edge_rollback: Callable[..., Mapping[str, object]] = rollback_initial_function,
    connector: Callable[..., object] = psycopg.connect,
) -> dict[str, object]:
    """Remove the initial Edge deployment and invalidate its database login."""
    role_receipt: Mapping[str, object] | None = None
    edge_receipt: Mapping[str, object] | None = None
    errors: list[Exception] = []
    try:
        with connector(admin_url) as connection:
            role_receipt = disable_dashboard_runtime_login(connection)
    except Exception as error:
        errors.append(error)
    try:
        edge_receipt = edge_rollback(project_ref)
    except Exception as error:
        errors.append(error)
    if errors:
        raise RuntimeError("dashboard rollback was incomplete") from errors[-1]
    assert role_receipt is not None and edge_receipt is not None
    return {
        **dict(edge_receipt),
        "runtime_login": role_receipt,
    }


def restore_gateway_and_rollback_initial_dashboard(
    project_ref: str,
    admin_url: str,
    gateway_artifact: Mapping[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
    connector: Callable[..., object] = psycopg.connect,
) -> dict[str, object]:
    """Restore the prior gateway bytes and remove all initial dashboard authority."""
    root = Path(str(gateway_artifact.get("repo_root", "")))
    commit = str(gateway_artifact.get("commit_sha", ""))
    expected_hash = str(gateway_artifact.get("source_sha256", ""))
    if not root.is_dir() or _tree_sha256(root / "supabase/functions/market-briefing-gateway") != expected_hash:
        raise RuntimeError("verified gateway rollback artifact is unavailable")
    restored = _deploy_named_function(
        project_ref, "market-briefing-gateway", commit, root, runner, require_existing=True,
    )
    if restored["source_sha256"] != expected_hash:
        raise RuntimeError("restored gateway source receipt mismatch")
    restored_at = datetime.now(timezone.utc).isoformat()
    # Gateway restoration is the first safety action. Dashboard cleanup is best effort only after
    # the prior gateway bytes are independently deployed again.
    try:
        cleanup = rollback_initial_deployment(
            project_ref, admin_url, connector=connector,
            edge_rollback=lambda ref: rollback_initial_function(ref, root, runner),
        )
    except Exception as error:
        raise RuntimeError("gateway restored but dashboard cleanup was incomplete") from error
    return {
        **cleanup,
        "gateway_restored_at": restored_at,
        "dashboard_cleaned_at": datetime.now(timezone.utc).isoformat(),
        "gateway": {
            "status": "restored",
            "git_sha": commit,
            "source_sha256": expected_hash,
            "function_version": restored["function_version"],
        },
    }


def rollback_after_gateway_change(
    project_ref: str,
    admin_url: str,
    gateway_artifact: Mapping[str, object],
    *,
    restorer: Callable[..., Mapping[str, object]] = restore_gateway_and_rollback_initial_dashboard,
) -> dict[str, object]:
    """Use the captured gateway artifact for every deployment failure after gateway mutation."""
    if not all(isinstance(gateway_artifact.get(field), str) and gateway_artifact[field]
               for field in ("repo_root", "commit_sha", "source_sha256")):
        raise RuntimeError("captured gateway rollback artifact is unavailable")
    receipt = dict(restorer(project_ref, admin_url, gateway_artifact))
    gateway = receipt.get("gateway")
    if (not isinstance(gateway, Mapping) or gateway.get("status") != "restored"
            or gateway.get("git_sha") != gateway_artifact["commit_sha"]
            or gateway.get("source_sha256") != gateway_artifact["source_sha256"]):
        raise RuntimeError("captured gateway rollback was incomplete")
    return receipt


def restore_gateway_after_release_failure(
    project_ref: str, admin_url: str, gateway_artifact: Mapping[str, object], *,
    restorer: Callable[..., Mapping[str, object]] = rollback_after_gateway_change,
    releaser: Callable[..., None] = release_gateway_rollback_artifact,
) -> dict[str, object]:
    """Restore live bytes first; a cleanup error must never bypass restoration."""
    restored: dict[str, object] | None = None
    restore_error: Exception | None = None
    try:
        restored = dict(restorer(project_ref, admin_url, gateway_artifact))
    except Exception as error:
        restore_error = error
    if restore_error is not None:
        # Preserve the verified source so a later guarded retry can restore it.
        raise RuntimeError("gateway restoration failed") from restore_error
    try:
        releaser(gateway_artifact)
    except Exception as cleanup_error:
        raise RuntimeError("gateway restored but rollback worktree cleanup failed") from cleanup_error
    assert restored is not None
    return restored


def publish_and_deploy_or_rollback(
    project_ref: str,
    values: Mapping[str, str],
    git_sha: str,
    admin_url: str,
    *,
    preflight: Callable[..., None] = ensure_initial_function_absent,
    publisher: Callable[..., None] = publish_dashboard_secrets,
    deployer: Callable[..., Mapping[str, object]] = deploy_function,
    rollback: Callable[..., Mapping[str, object]] = rollback_initial_deployment,
) -> dict[str, object]:
    """Publish the exact secret manifest and clean it up if deployment fails."""
    preflight(project_ref)
    try:
        publisher(project_ref, values)
        deployed = deployer(project_ref, git_sha)
        if isinstance(deployed, Mapping):
            return dict(deployed)
        return {"status": "deployed", "functions": list(deployed)}
    except Exception as error:
        try:
            rollback(project_ref, admin_url)
        except Exception as rollback_error:
            raise RuntimeError(
                "dashboard function deployment failed and the initial dashboard rollback also failed"
            ) from rollback_error
        raise error


def build_static_or_rollback(
    project_ref: str,
    site_origin: str,
    git_sha: str,
    admin_url: str,
    *,
    builder: Callable[..., Mapping[str, object]] = build_static_release,
    rollback: Callable[..., Mapping[str, object]] = rollback_initial_deployment,
) -> dict[str, object]:
    try:
        receipt = dict(builder(project_ref, site_origin))
        if receipt.get("status") != "verified" or not receipt.get("asset_hashes"):
            raise RuntimeError("static asset receipt is incomplete")
        return {**receipt, "candidate_sha": git_sha}
    except Exception as error:
        try:
            rollback(project_ref, admin_url)
        except Exception as rollback_error:
            raise RuntimeError(
                "static build failed and the initial dashboard rollback also failed"
            ) from rollback_error
        raise error


def run_post_deploy_canary(
    project_ref: str,
    allowed_origin: str,
    database_url: str,
    owner_email: str,
    service_key: str,
    publishable_key: str,
    non_owner_access_token: str | None = None,
    *,
    token_factory: Callable[..., str] = obtain_ephemeral_owner_access_token,
    source_collector: Callable[..., Mapping[str, object]] = collect_source_receipts,
    canary: Callable[..., Mapping[str, object]] = run_http_canary,
    session_revoker: Callable[..., Mapping[str, str] | None] = revoke_ephemeral_owner_session,
) -> dict[str, object]:
    project_url = f"https://{project_ref}.supabase.co"
    api_url = f"{project_url}/functions/v1/{FUNCTION_NAME}"
    _validate_origin(allowed_origin)
    _validate_database_url(database_url, project_ref)
    token = token_factory(project_url, owner_email, allowed_origin, service_key, publishable_key)
    try:
        canary_arguments = {}
        if non_owner_access_token is not None:
            canary_arguments["non_owner_access_token"] = non_owner_access_token
        result = dict(canary(
            api_url, allowed_origin, token,
            source_reader=lambda run_id: source_collector(database_url, api_url, run_id),
            **canary_arguments,
        ))
        expected = {
            "status": "verified",
            "source_reconciliation": "verified",
            "financial_write_routes": 0,
            "brokerage_authority": "none",
            "friend_invitations": "disabled",
        }
        if any(result.get(key) != value for key, value in expected.items()):
            raise RuntimeError("production canary receipt is incomplete")
        if non_owner_access_token is not None and result.get("non_owner_status") != 403:
            raise RuntimeError("production non-owner denial receipt is incomplete")
        return result
    finally:
        session_revoker(project_url, token, publishable_key)


def verify_initial_deployment_or_rollback(
    project_ref: str,
    allowed_origin: str,
    database_url: str,
    owner_email: str,
    service_key: str,
    publishable_key: str,
    non_owner_access_token: str | None = None,
    *,
    verifier: Callable[..., dict[str, object]] = run_post_deploy_canary,
    rollback: Callable[..., dict[str, object]] = rollback_initial_function,
) -> dict[str, object]:
    try:
        return verifier(
            project_ref, allowed_origin, database_url, owner_email, service_key, publishable_key,
            non_owner_access_token,
        )
    except Exception as error:
        try:
            rollback(project_ref)
        except Exception as rollback_error:
            raise RuntimeError("production canary failed and the initial dashboard rollback also failed") from rollback_error
        raise error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--allowed-origin", required=True)
    parser.add_argument("--site-origin", required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--candidate-sha")
    parser.add_argument("--gateway-rollback-ref", required=True)
    parser.add_argument("--gateway-rollback-source-sha256", required=True)
    parser.add_argument("--gateway-current-version", required=True, type=int)
    parser.add_argument("--evidence-directory", required=True, type=Path)
    parser.add_argument("--rollback-worktree", type=Path)
    parser.add_argument("--keep-rollback-worktree", action="store_true")
    parser.add_argument("--release-state", type=Path, required=True)
    arguments = parser.parse_args()
    if not os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip():
        raise SystemExit("SUPABASE_ACCESS_TOKEN is required for protected Supabase mutation")
    owner_user_id = os.environ.get("DASHBOARD_OWNER_USER_ID", "").strip()
    owner_email = os.environ.get("DASHBOARD_OWNER_EMAIL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    non_owner_access_token = os.environ.get("DASHBOARD_NON_OWNER_ACCESS_TOKEN", "").strip()
    admin_url = os.environ.get("POSTGRES_URL", "").strip()
    session_template = os.environ.get("SUPAVISOR_SESSION_URL", "").strip()
    if not admin_url or not session_template or not owner_email or not service_key or not publishable_key or not non_owner_access_token:
        raise SystemExit(
            "POSTGRES_URL, SUPAVISOR_SESSION_URL, DASHBOARD_OWNER_EMAIL, "
            "SUPABASE_SERVICE_ROLE_KEY, SUPABASE_PUBLISHABLE_KEY, and "
            "DASHBOARD_NON_OWNER_ACCESS_TOKEN are required"
        )

    validate_release_database_endpoints(arguments.project_ref, admin_url, session_template)
    # The clean-tree gate intentionally precedes capture/state writes.
    git_sha = verify_git_release(expected_sha=arguments.candidate_sha)
    verify_reviewed_sha(git_sha, arguments.reviewed_sha)
    if arguments.keep_rollback_worktree and arguments.rollback_worktree is None:
        raise SystemExit("--keep-rollback-worktree requires --rollback-worktree")
    rollback_directory = (arguments.rollback_worktree if arguments.rollback_worktree is not None
                          else Path(tempfile.mkdtemp(prefix="stocks-gateway-rollback-")) / "checkout")
    gateway_rollback = prepare_gateway_rollback_artifact(
        arguments.gateway_rollback_ref,
        arguments.gateway_rollback_source_sha256,
        rollback_directory,
    )
    gateway_rollback_preflight = verify_gateway_rollback_preflight(
        arguments.project_ref, arguments.gateway_current_version, gateway_rollback
    )
    retained_gateway = retain_gateway_rollback_artifact(gateway_rollback, arguments.evidence_directory)

    def write_state(changed: bool) -> None:
        state = {"changed": changed, "artifact": {"repo_root": str(gateway_rollback["repo_root"]), "commit_sha": gateway_rollback["commit_sha"], "source_sha256": gateway_rollback["source_sha256"]}}
        arguments.release_state.parent.mkdir(parents=True, exist_ok=True)
        temporary = arguments.release_state.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
        os.replace(temporary, arguments.release_state)

    # Persist the artifact identity before further validation. The changed
    # marker itself is written immediately before the first gateway mutation.
    write_state(False)

    validate_static_configuration(
        arguments.project_ref,
        owner_user_id,
        arguments.allowed_origin,
        DASHBOARD_SECRET_NAMES,
    )
    run_local_verification()
    dashboard_source = verify_v1_dashboard_source()
    ensure_initial_function_absent(arguments.project_ref)
    migration_manifest = candidate_migration_manifest()
    with psycopg.connect(admin_url) as connection:
        with connection.cursor() as cursor:
            migration_receipts = apply_release_migrations(cursor, migration_manifest)
        database_secret = provision_dashboard_role(connection, session_template)
        role_receipt = verify_dashboard_role(connection)
    database_url = database_secret["DASHBOARD_DATABASE_URL"]
    safe_configuration = validate_deployment_configuration(
        project_ref=arguments.project_ref,
        owner_user_id=owner_user_id,
        allowed_origin=arguments.allowed_origin,
        database_url=database_url,
        role_receipt=role_receipt,
        secret_names=DASHBOARD_SECRET_NAMES,
    )
    rollback_release = lambda project_ref, admin: rollback_after_gateway_change(
        project_ref, admin, gateway_rollback
    )
    # A subprocess can die after its remote mutation and before it returns a
    # receipt, so mark this state atomically before attempting that command.
    write_state(True)
    receipt = publish_and_deploy_or_rollback(
        arguments.project_ref,
        {
            "DASHBOARD_ALLOWED_ORIGINS": arguments.allowed_origin,
            "DASHBOARD_DATABASE_URL": database_url,
            "DASHBOARD_OWNER_USER_ID": owner_user_id,
        },
        git_sha,
        admin_url,
        deployer=deploy_changed_functions,
        rollback=rollback_release,
    )
    receipt["candidate_sha"] = git_sha
    receipt["migrations"] = migration_receipts["candidate"]
    receipt["migration_application"] = migration_receipts
    receipt["dashboard_source"] = dashboard_source
    receipt["configuration"] = safe_configuration
    receipt["role"] = role_receipt
    receipt["canary"] = verify_initial_deployment_or_rollback(
        arguments.project_ref,
        arguments.allowed_origin,
        database_url,
        owner_email,
        service_key,
        publishable_key,
        non_owner_access_token,
        rollback=lambda project_ref: rollback_release(project_ref, admin_url),
    )
    receipt["static_assets"] = build_static_or_rollback(
        arguments.project_ref,
        arguments.site_origin,
        git_sha,
        admin_url, rollback=rollback_release,
    )
    try:
        receipt["artifact_verification"] = verify_release_artifact_receipts(git_sha, receipt, migration_manifest)
    except Exception as error:
        try:
            rollback_release(arguments.project_ref, admin_url)
        except Exception as rollback_error:
            raise RuntimeError(
                "artifact verification failed and the initial dashboard rollback also failed"
            ) from rollback_error
        raise error
    receipt["gateway_rollback_artifact"] = {
        **retained_gateway,
        "repo_root": str(gateway_rollback["repo_root"]),
        "git_sha": gateway_rollback["commit_sha"],
        "source_sha256": gateway_rollback["source_sha256"],
        "predeployment_function_version": gateway_rollback_preflight["function_version"],
    }
    receipt["rollback_readiness"] = gateway_rollback_preflight
    receipt["deployment_outcome"] = "succeeded"
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    if not arguments.keep_rollback_worktree:
        release_gateway_rollback_artifact(gateway_rollback)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
