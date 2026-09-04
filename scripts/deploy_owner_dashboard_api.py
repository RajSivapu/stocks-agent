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
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence
from urllib.parse import unquote, urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.provision_dashboard_runtime_role import (
    disable_dashboard_runtime_login,
    provision_dashboard_role,
)
from scripts.verify_owner_dashboard_role import verify_dashboard_role
from scripts.verify_owner_dashboard_deployment import (
    collect_source_receipts,
    obtain_ephemeral_owner_access_token,
    revoke_ephemeral_owner_session,
    run_http_canary,
)


MIGRATION = ROOT / "sql/migrations/20260906_owner_dashboard_read_role.sql"
SUPABASE_CLI_VERSION = "2.116.0"
FUNCTION_NAME = "owner-dashboard-api"
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


def verify_git_release(repo_root: Path = ROOT, runner: Callable[..., object] = subprocess.run) -> str:
    status = _run(["git", "status", "--porcelain=v1"], cwd=repo_root, runner=runner)
    if getattr(status, "returncode", 1) != 0 or str(getattr(status, "stdout", "")).strip():
        raise RuntimeError("deployment requires a clean working tree")
    head = _run(["git", "rev-parse", "HEAD"], cwd=repo_root, runner=runner)
    upstream = _run(["git", "rev-parse", "@{upstream}"], cwd=repo_root, runner=runner)
    local_sha = str(getattr(head, "stdout", "")).strip()
    remote_sha = str(getattr(upstream, "stdout", "")).strip()
    if getattr(head, "returncode", 1) != 0 or getattr(upstream, "returncode", 1) != 0 or local_sha != remote_sha:
        raise RuntimeError("deployment requires the exact commit to be pushed")
    if not re.fullmatch(r"[0-9a-f]{40}", local_sha):
        raise RuntimeError("deployment git receipt is malformed")
    return local_sha


def run_local_verification(repo_root: Path = ROOT, runner: Callable[..., object] = subprocess.run) -> None:
    result = _run(["npm", "run", "test:all"], cwd=repo_root, runner=runner)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError("local verification suite failed")


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


def dashboard_function_version(
    project_ref: str,
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
        row = next((item for item in functions if item.get("name") == FUNCTION_NAME), None)
        return None if row is None else int(row["version"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("dashboard function version receipt is malformed") from error


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
    rollback_version = dashboard_function_version(project_ref, repo_root, runner)
    if rollback_version is not None:
        raise RuntimeError("initial dashboard function already exists")
    deploy = _run(
        [
            "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "functions", "deploy",
            FUNCTION_NAME, "--project-ref", project_ref, "--no-verify-jwt", "--use-api",
        ],
        cwd=repo_root,
        runner=runner,
    )
    if getattr(deploy, "returncode", 1) != 0:
        raise RuntimeError("Supabase rejected the dashboard function deployment")
    version = dashboard_function_version(project_ref, repo_root, runner)
    if version is None or (rollback_version is not None and version <= rollback_version):
        raise RuntimeError("dashboard function version did not advance")
    return {
        "status": "deployed",
        "function": FUNCTION_NAME,
        "function_version": version,
        "rollback_function_version": rollback_version,
        "git_sha": git_sha,
        "project_ref_digest": _project_digest(project_ref),
        "cli_version": SUPABASE_CLI_VERSION,
        "deployed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


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
    publisher(project_ref, values)
    try:
        return dict(deployer(project_ref, git_sha))
    except Exception as error:
        try:
            rollback(project_ref, admin_url)
        except Exception as rollback_error:
            raise RuntimeError(
                "dashboard function deployment failed and the initial dashboard rollback also failed"
            ) from rollback_error
        raise error


def run_post_deploy_canary(
    project_ref: str,
    allowed_origin: str,
    database_url: str,
    owner_email: str,
    service_key: str,
    publishable_key: str,
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
        result = dict(canary(
            api_url,
            allowed_origin,
            token,
            source_reader=lambda run_id: source_collector(database_url, api_url, run_id),
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
    *,
    verifier: Callable[..., dict[str, object]] = run_post_deploy_canary,
    rollback: Callable[..., dict[str, object]] = rollback_initial_function,
) -> dict[str, object]:
    try:
        return verifier(
            project_ref, allowed_origin, database_url, owner_email, service_key, publishable_key,
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
    arguments = parser.parse_args()
    owner_user_id = os.environ.get("DASHBOARD_OWNER_USER_ID", "").strip()
    owner_email = os.environ.get("DASHBOARD_OWNER_EMAIL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    admin_url = os.environ.get("POSTGRES_URL", "").strip()
    session_template = os.environ.get("SUPAVISOR_SESSION_URL", "").strip()
    if not admin_url or not session_template or not owner_email or not service_key or not publishable_key:
        raise SystemExit(
            "POSTGRES_URL, SUPAVISOR_SESSION_URL, DASHBOARD_OWNER_EMAIL, "
            "SUPABASE_SERVICE_ROLE_KEY, and SUPABASE_PUBLISHABLE_KEY are required"
        )

    validate_static_configuration(
        arguments.project_ref,
        owner_user_id,
        arguments.allowed_origin,
        DASHBOARD_SECRET_NAMES,
    )
    git_sha = verify_git_release()
    run_local_verification()
    ensure_initial_function_absent(arguments.project_ref)
    with psycopg.connect(admin_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(MIGRATION.read_text())
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
    receipt = publish_and_deploy_or_rollback(
        arguments.project_ref,
        {
            "DASHBOARD_ALLOWED_ORIGINS": arguments.allowed_origin,
            "DASHBOARD_DATABASE_URL": database_url,
            "DASHBOARD_OWNER_USER_ID": owner_user_id,
        },
        git_sha,
        admin_url,
    )
    receipt["configuration"] = safe_configuration
    receipt["role"] = role_receipt
    receipt["canary"] = verify_initial_deployment_or_rollback(
        arguments.project_ref,
        arguments.allowed_origin,
        database_url,
        owner_email,
        service_key,
        publishable_key,
        rollback=lambda project_ref: rollback_initial_deployment(project_ref, admin_url),
    )
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
