#!/usr/bin/env python3
"""Retained validator for the completed, unchanged production V1 baseline.

Sites evidence comes from the native Codex Sites connector because GitHub Actions
has no equivalent Sites transport. Database, Auth, and Edge evidence is read
again inside the protected GitHub environment. The protected result contains
identities and digests only. Its native input may contain bounded source and
live-asset bytes captured by the authenticated Sites connector; it never
contains portfolio rows or credentials.

This was a one-time bridge for the already-deployed V1 runtime and its workflow
is now retired. It intentionally
requires full database inventory equality with the completed reconciliation.
Routine scheduled data growth invalidates that equality; later code releases use
the normal protected deployment and recovery workflow.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

if __package__ in {None, ""}:
    _REPOSITORY_ROOT = str(Path(__file__).resolve().parents[1])
    if _REPOSITORY_ROOT not in sys.path:
        sys.path.insert(0, _REPOSITORY_ROOT)

from scripts.configured_native_release_adapter import NativeReleaseAdapter
from scripts.inspect_production_schema_baseline import RELATION_PRESENCE, inspect_production_schema
from scripts.managed_isolated_restore import PROJECT_REF, SupabaseManagementApi, canonical_json
from scripts.provision_owner_dashboard_auth import (
    UUID as AUTH_UUID,
    validate_configuration as validate_auth_admin_configuration,
    validate_email_otp_configuration,
)
from scripts.verify_owner_dashboard_deployment import (
    migration_statements_sha256,
    normalize_migration_statements,
    obtain_ephemeral_owner_access_token,
    revoke_ephemeral_owner_session,
)
from scripts.verify_personal_stock_agent_v1 import (
    FUNCTIONS, SHA, git_function_runtime, git_files, path_is_safe, tree_sha256,
)


ROOT = Path(__file__).resolve().parents[1]
MAX_RECEIPT_BYTES = 250_000_000
MAX_NATIVE_SITE_RECEIPT_AGE_SECONDS = 24 * 60 * 60
SITE_SOURCE_PATHS = (
    ".openai/hosting.json",
    "apps/web",
    "packages/dashboard-contracts",
    "package.json",
    "package-lock.json",
)
SCHEMA_SOURCE_PATHS = (
    "sql/schema.sql",
    "sql/migrations",
    "sql/reconciliation/20261004_production_schema_reconciliation.sql",
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SITE_HASH = re.compile(r"sha256:([0-9a-f]{64})\Z")
_SITE_RECEIPT_KEYS = {
    "format", "captured_at", "trust_domain", "site", "retained_prior_version",
    "active_version", "active_deployment", "candidate_build", "archive_captures",
    "live_bundle",
}


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    require(result.returncode == 0, "candidate Git object is unavailable")
    return result.stdout


def _git_files_for_paths(repo: Path, sha: str, paths: tuple[str, ...]) -> dict[str, bytes]:
    require(SHA.fullmatch(sha) is not None, "candidate SHA is malformed")
    files: dict[str, bytes] = {}
    for entry in _git(repo, "ls-tree", "-rz", sha, "--", *paths).split(b"\0"):
        if not entry:
            continue
        metadata, encoded_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = encoded_path.decode()
        require(mode in {"100644", "100755"} and kind == "blob", "Site source contains a non-file Git object")
        require(any(path == allowed or path.startswith(allowed + "/") for allowed in paths), "Site source path is outside the allowlist")
        files[path] = _git(repo, "cat-file", "blob", object_id)
    require(files, "allowlisted candidate source is unavailable")
    return files


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("native Site receipt timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeError("native Site receipt timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise RuntimeError("native Site receipt timestamp is invalid")
    return parsed.astimezone(UTC)


def _static_build_files(root: Path) -> dict[str, bytes]:
    require(root.is_dir() and not root.is_symlink(), "candidate Site build is unavailable")
    require(not any(path.is_symlink() for path in root.rglob("*")),
            "candidate Site build contains a symlink")
    files = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in sorted(root.rglob("*")) if path.is_file()}
    require("index.html" in files and files, "candidate Site build is incomplete")
    return files


def _captured_archive(value: object, *, version_id: object, expected_hash: object) -> dict[str, bytes]:
    require(isinstance(value, Mapping) and set(value) == {
        "version_id", "capture_method", "content_base64",
    }, "native Site archive capture is malformed")
    require(
        value.get("version_id") == version_id
        and value.get("capture_method") == "owner_authenticated_native_connector"
        and isinstance(value.get("content_base64"), str),
        "native Site archive capture is not owner-authenticated",
    )
    try:
        raw = base64.b64decode(value["content_base64"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise RuntimeError("native Site archive capture is malformed") from error
    require(
        0 < len(raw) <= 100_000_000
        and expected_hash == "sha256:" + hashlib.sha256(raw).hexdigest(),
        "native Site archive capture digest is invalid",
    )
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            require(
                members and len(members) <= 10_000
                and len({member.name for member in members}) == len(members)
                and all(
                    member.isfile() and path_is_safe(member.name)
                    and member.size <= 10_000_000
                    for member in members
                )
                and sum(member.size for member in members) <= 100_000_000,
                "native Site archive capture contains unsafe members",
            )
            files = {
                member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
                for member in members
            }
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError("native Site archive capture is unreadable") from error
    return files


def _captured_live_asset(value: object) -> bytes:
    require(isinstance(value, str), "native Site live capture is malformed")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise RuntimeError("native Site live capture is malformed") from error
    require(0 < len(raw) <= 10_000_000, "native Site live capture exceeds its bound")
    return raw


def _validate_restorable_site_archive(files: Mapping[str, bytes], project_id: object) -> None:
    """Require enough bounded source and the exact manifest to rebuild a prior Site."""
    required_files = {".openai/hosting.json", "package.json", "package-lock.json"}
    require(
        required_files <= set(files)
        and any(path.startswith("apps/web/") for path in files)
        and any(path.startswith("packages/dashboard-contracts/") for path in files),
        "native Site retained prior archive is not restorable",
    )
    try:
        hosting = json.loads(files[".openai/hosting.json"])
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("native Site retained prior archive is not restorable") from error
    require(
        hosting == {
            "project_id": project_id,
            "static": {
                "directory": "dist",
                "not_found_handling": "single-page-application",
            },
        },
        "native Site retained prior archive is not restorable",
    )


def validate_native_site_receipt(receipt: Mapping[str, object], candidate_sha: str, project_ref: str,
                                 repo_root: Path = ROOT, *, now: datetime | None = None,
                                 static_root: Path | None = None,
                                 protected_build_receipt: Mapping[str, object] | None = None) -> dict[str, object]:
    """Compare copied Site metadata/bytes without treating the copy as provenance.

    This deterministic boundary can reject inconsistent content, but caller-supplied
    JSON cannot prove that an authenticated Sites connector produced it. Only a
    current root-agent connector observation may close the owner-Site evidence class.
    """
    require(isinstance(receipt, Mapping) and set(receipt) == _SITE_RECEIPT_KEYS, "native Site receipt is malformed")
    require(receipt.get("format") == "stocks-native-sites-release-v3"
            and receipt.get("trust_domain") == "codex-native-sites-connector", "native Site receipt provenance is malformed")
    captured_at = _timestamp(receipt.get("captured_at"))
    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    age = (observed_at - captured_at).total_seconds()
    require(0 <= age <= MAX_NATIVE_SITE_RECEIPT_AGE_SECONDS, "native Site observation is not fresh")
    require(PROJECT_REF.fullmatch(project_ref) is not None, "exact production project identity is required")
    site = receipt.get("site")
    prior = receipt.get("retained_prior_version")
    version = receipt.get("active_version")
    deployment = receipt.get("active_deployment")
    candidate_build = receipt.get("candidate_build")
    captures = receipt.get("archive_captures")
    bundle = receipt.get("live_bundle")
    require(isinstance(site, Mapping) and set(site) == {
        "project_id", "status", "live_url", "latest_version_number", "current_user_role",
        "access_mode", "allowed_owner_count", "external_visitor_count", "allowed_group_count",
    }, "native Site receipt is malformed")
    require(isinstance(version, Mapping) and set(version) == {
        "id", "version_number", "source_commit_sha", "archive_format", "archive_content_hash",
        "archive_files", "archive_tree_sha256", "file_count", "size_bytes",
    }, "native Site version receipt is malformed")
    require(isinstance(deployment, Mapping) and set(deployment) == {
        "id", "version_id", "type", "status", "url",
    }, "native Site deployment receipt is malformed")
    require(isinstance(prior, Mapping) and set(prior) == {
        "id", "version_number", "deployment_id", "archive_content_hash", "rollback_eligible",
    }, "native Site retained prior-version receipt is malformed")
    require(isinstance(candidate_build, Mapping) and set(candidate_build) == {
        "candidate_sha", "build_sha256", "files",
    }, "native Site candidate-build receipt is malformed")
    require(isinstance(bundle, Mapping) and set(bundle) == {
        "files", "supabase_project_ref", "dashboard_api_url",
    }, "native Site live-bundle receipt is malformed")
    require(isinstance(captures, Mapping) and set(captures) == {"active", "prior"},
            "native Site archive captures are malformed")

    try:
        hosting = json.loads(_git(repo_root, "show", f"{candidate_sha}:.openai/hosting.json"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("candidate Sites manifest is malformed") from error
    require(hosting == {"project_id": site.get("project_id"), "static": {
        "directory": "dist", "not_found_handling": "single-page-application",
    }}, "native Site project does not match the candidate manifest")
    require(
        site.get("status") == "active"
        and site.get("current_user_role") == "owner"
        and site.get("access_mode") == "custom"
        and site.get("allowed_owner_count") == 1
        and site.get("external_visitor_count") == 0
        and site.get("allowed_group_count") == 0,
        "native Site is not owner-only",
    )
    live_url = site.get("live_url")
    require(isinstance(live_url, str) and re.fullmatch(r"https://[a-z0-9-]+(?:\.[a-z0-9-]+)*\.chatgpt\.site", live_url),
            "native Site live URL is malformed")
    require(type(site.get("latest_version_number")) is int and site["latest_version_number"] > 0
            and site["latest_version_number"] == version.get("version_number"), "native Site latest version is inconsistent")
    require(isinstance(version.get("id"), str) and version["id"]
            and isinstance(deployment.get("id"), str) and deployment["id"]
            and deployment.get("version_id") == version["id"]
            and deployment.get("type") == "publish"
            and deployment.get("status") == "succeeded"
            and deployment.get("url") == live_url, "native Site active deployment is inconsistent")
    require(version.get("archive_format") == "tar"
            and isinstance(version.get("archive_content_hash"), str)
            and _SITE_HASH.fullmatch(version["archive_content_hash"]) is not None
            and type(version.get("file_count")) is int and version["file_count"] > 0
            and type(version.get("size_bytes")) is int and 0 < version["size_bytes"] <= 100_000_000,
            "native Site archive receipt is malformed")
    require(isinstance(prior.get("id"), str) and prior["id"]
            and prior["id"] != version["id"]
            and isinstance(prior.get("deployment_id"), str) and prior["deployment_id"]
            and prior["deployment_id"] != deployment["id"]
            and type(prior.get("version_number")) is int
            and 0 < prior["version_number"] < version["version_number"]
            and isinstance(prior.get("archive_content_hash"), str)
            and _SITE_HASH.fullmatch(prior["archive_content_hash"]) is not None
            and prior.get("rollback_eligible") is True,
            "native Site retained prior version is unavailable for rollback")
    source_sha = version.get("source_commit_sha")
    require(isinstance(source_sha, str) and SHA.fullmatch(source_sha) is not None, "native Site source SHA is malformed")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source_sha, candidate_sha], cwd=repo_root,
        capture_output=True, check=False,
    )
    require(ancestor.returncode == 0, "native Site source is not an ancestor of the candidate")
    active_archive_files = _captured_archive(
        captures["active"], version_id=version["id"],
        expected_hash=version["archive_content_hash"],
    )
    prior_archive_files = _captured_archive(
        captures["prior"], version_id=prior["id"],
        expected_hash=prior["archive_content_hash"],
    )
    _validate_restorable_site_archive(prior_archive_files, site.get("project_id"))
    source_files = _git_files_for_paths(repo_root, source_sha, SITE_SOURCE_PATHS)
    candidate_files = _git_files_for_paths(repo_root, candidate_sha, SITE_SOURCE_PATHS)
    require(all(path in source_files for path in (".openai/hosting.json", "package.json", "package-lock.json")),
            "complete Site source is unavailable")
    require(source_files == candidate_files, "Site source differs from the active native version")
    archive_source_files = {
        path: raw for path, raw in active_archive_files.items()
        if any(path == allowed or path.startswith(allowed + "/") for allowed in SITE_SOURCE_PATHS)
    }
    archive_hashes = {
        path: hashlib.sha256(raw).hexdigest() for path, raw in active_archive_files.items()
    }
    require(archive_source_files == source_files
            and version.get("archive_files") == archive_hashes
            and version.get("archive_tree_sha256") == tree_sha256(active_archive_files)
            and version.get("file_count") == len(active_archive_files)
            and version.get("size_bytes") == sum(len(raw) for raw in active_archive_files.values()),
            "native Site downloaded archive differs from candidate source")
    build_files = _static_build_files(static_root or repo_root / "dist")
    build_hashes = {path: hashlib.sha256(raw).hexdigest() for path, raw in build_files.items()}
    require(candidate_build.get("candidate_sha") == source_sha
            and candidate_build.get("files") == build_hashes
            and candidate_build.get("build_sha256") == tree_sha256(build_files)
            and isinstance(protected_build_receipt, Mapping)
            and protected_build_receipt.get("status") == "verified"
            and protected_build_receipt.get("candidate_sha") == candidate_sha
            and protected_build_receipt.get("files") == build_hashes
            and protected_build_receipt.get("build_sha256") == tree_sha256(build_files),
            "native Site candidate build differs from the protected build receipt")
    expected_api_url = f"https://{project_ref}.supabase.co/functions/v1/owner-dashboard-api"
    assets = bundle.get("files")
    served_files = {path: raw for path, raw in build_files.items() if not path.startswith("_")}
    require(isinstance(assets, list) and len(assets) == len(served_files),
            "native Site live-bundle receipt is malformed")
    asset_paths: set[str] = set()
    live_bytes: dict[str, bytes] = {}
    for asset in assets:
        path = asset.get("path") if isinstance(asset, Mapping) else None
        expected_url = live_url + ("/" if path == "index.html" else "/" + str(path))
        require(isinstance(asset, Mapping) and set(asset) == {
                    "path", "url", "sha256", "bytes", "capture_method", "content_base64",
                }
                and isinstance(path, str) and path in served_files and path not in asset_paths
                and asset.get("url") == expected_url
                and asset.get("sha256") == hashlib.sha256(served_files[path]).hexdigest()
                and asset.get("bytes") == len(served_files[path])
                and asset.get("capture_method") == "owner_authenticated_native_connector"
                and 0 < len(served_files[path]) <= 10_000_000,
                "native Site live-bundle asset receipt differs from candidate build")
        observed = _captured_live_asset(asset.get("content_base64"))
        require(observed == served_files[path],
                "native Site live bytes differ from candidate build")
        asset_paths.add(path)
        live_bytes[path] = observed
    require(asset_paths == set(served_files), "native Site live-bundle coverage is incomplete")
    combined = b"\n".join(live_bytes.values())
    require(bundle.get("supabase_project_ref") == project_ref
            and bundle.get("dashboard_api_url") == expected_api_url
            and project_ref.encode() in combined
            and expected_api_url.encode() in combined,
            "native Site backend binding differs from protected Supabase")
    return {
        "status": "content_consistent",
        "provenance": "offline_copy_only",
        "captured_at": receipt["captured_at"],
        "project_id": site["project_id"],
        "live_url": live_url,
        "access_mode": "custom",
        "allowed_owner_count": 1,
        "external_visitor_count": 0,
        "allowed_group_count": 0,
        "version_id": version["id"],
        "version_number": version["version_number"],
        "deployment_id": deployment["id"],
        "source_commit_sha": source_sha,
        "source_sha256": tree_sha256(source_files),
        "archive_content_hash": version["archive_content_hash"],
        "archive_file_count": version["file_count"],
        "retained_prior_version_id": prior["id"],
        "retained_prior_deployment_id": prior["deployment_id"],
        "candidate_build_sha256": candidate_build["build_sha256"],
        "live_files": [{
            key: asset[key] for key in ("path", "url", "sha256", "bytes")
        } for asset in assets],
        "supabase_project_ref": project_ref,
        "dashboard_api_url": expected_api_url,
    }


def attest_edge_functions(adapter: object, candidate_sha: str, repo_root: Path = ROOT) -> list[dict[str, object]]:
    """Compare active Management API downloads to candidate production files."""
    require(SHA.fullmatch(candidate_sha) is not None, "candidate SHA is malformed")
    rows: list[dict[str, object]] = []
    for name in FUNCTIONS:
        expected, expected_config = git_function_runtime(repo_root, candidate_sha, name)
        require(expected and expected_config["entrypoint"] in expected, "candidate function production bytes are incomplete")
        snapshot = adapter.capture(name)
        require(isinstance(snapshot, Mapping) and snapshot.get("exists") is True
                and isinstance(snapshot.get("identity"), str) and snapshot["identity"]
                and isinstance(snapshot.get("version"), str) and snapshot["version"].isdigit()
                and snapshot.get("configuration") == expected_config
                and snapshot.get("values") == {}, "active function identity or configuration differs from candidate")
        encoded_files = snapshot.get("files")
        require(isinstance(encoded_files, Mapping) and encoded_files, "active function download is unavailable")
        deployed: dict[str, bytes] = {}
        try:
            for path, encoded in encoded_files.items():
                require(isinstance(path, str) and isinstance(encoded, str), "active function download is malformed")
                deployed[path] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise RuntimeError("active function download is malformed") from error
        require(deployed == expected, f"{name} deployed bytes differ from the candidate")
        rows.append({
            "function": name,
            "status": "verified",
            "identity": snapshot["identity"],
            "version": snapshot["version"],
            "configuration": expected_config,
            "source_sha256": tree_sha256(deployed),
            "source_file_count": len(deployed),
        })
    return rows


def validate_schema_inventory(receipt: Mapping[str, object], project_ref: str, candidate_sha: str) -> dict[str, object]:
    """Reduce the detailed inventory to a bounded all-required-relations proof."""
    require(PROJECT_REF.fullmatch(project_ref) is not None, "exact production project identity is required")
    require(SHA.fullmatch(candidate_sha) is not None, "candidate SHA is malformed")
    require(isinstance(receipt, Mapping) and receipt.get("format") == "stocks-production-schema-inventory-v1"
            and receipt.get("main_sha") == candidate_sha, "schema inventory candidate binding is invalid")
    # Match the established inventory format's literal backslash-zero separator.
    binding = hashlib.sha256(("stocks-production-schema-inventory-v1\\0" + project_ref).encode()).hexdigest()
    require(receipt.get("production_binding_sha256") == binding, "schema inventory project binding is invalid")
    unsigned = dict(receipt)
    supplied_digest = unsigned.pop("receipt_sha256", None)
    require(isinstance(supplied_digest, str) and _DIGEST.fullmatch(supplied_digest) is not None
            and hashlib.sha256(canonical_json(unsigned).encode()).hexdigest() == supplied_digest,
            "schema inventory receipt hash is invalid")
    presence = receipt.get("relation_presence")
    expected = {f"{schema}.{name}" for schema, name in RELATION_PRESENCE}
    require(isinstance(presence, Mapping) and set(presence) == expected, "schema relation inventory is incomplete")
    require(all(presence.get(name) is True for name in expected), "required production relation is absent")
    catalog = receipt.get("catalog")
    roots = receipt.get("protected_roots")
    require(isinstance(catalog, Mapping) and isinstance(catalog.get("relations"), list) and catalog["relations"]
            and receipt.get("root_algorithm") == "sha256-sorted-row-hashes-v1"
            and isinstance(roots, list) and roots
            and any(isinstance(row, Mapping) and row.get("relation") == "stock_agent_release_migration_ledger" for row in roots),
            "schema inventory evidence is incomplete")
    return {
        "status": "verified",
        "inventory_receipt_sha256": supplied_digest,
        "required_relation_count": len(expected),
        "all_required_relations": True,
        "root_algorithm": receipt["root_algorithm"],
        "protected_root_count": len(receipt["protected_roots"]),
    }


_RECONCILIATION_RECEIPT_KEYS = {
    "after_inventory_receipt_sha256", "after_projected_roots_sha256", "before_projected_roots_sha256",
    "ci_workflow_run_id", "expected_relation_count", "format", "main_sha",
    "prior_inventory_receipt_sha256", "prior_inventory_run_id", "receipt_sha256",
    "reconciliation_path", "reconciliation_sha256", "row_count", "snapshot_artifact_sha256",
    "snapshot_plaintext_sha256", "table_count", "transaction_status", "writer_attempts",
}


def validate_reconciliation_continuity(current: Mapping[str, object], reconciliation: Mapping[str, object],
                                       project_ref: str, candidate_sha: str,
                                       repo_root: Path = ROOT) -> dict[str, object]:
    """Seal a one-time full-inventory baseline before routine scheduled data advances."""
    summary = validate_schema_inventory(current, project_ref, candidate_sha)
    require(isinstance(reconciliation, Mapping) and set(reconciliation) == _RECONCILIATION_RECEIPT_KEYS
            and reconciliation.get("format") == "stocks-production-schema-reconciliation-v1"
            and reconciliation.get("transaction_status") == "committed"
            and reconciliation.get("writer_attempts") == 1, "schema reconciliation receipt is invalid")
    unsigned = dict(reconciliation)
    receipt_digest = unsigned.pop("receipt_sha256", None)
    require(isinstance(receipt_digest, str) and _DIGEST.fullmatch(receipt_digest) is not None
            and hashlib.sha256(canonical_json(unsigned).encode()).hexdigest() == receipt_digest,
            "schema reconciliation receipt hash is invalid")
    baseline_sha = reconciliation.get("main_sha")
    require(isinstance(baseline_sha, str) and SHA.fullmatch(baseline_sha) is not None,
            "schema reconciliation source SHA is invalid")
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", baseline_sha, candidate_sha], cwd=repo_root,
        capture_output=True, check=False,
    )
    require(ancestor.returncode == 0, "schema reconciliation is not an ancestor of the candidate")
    require(_git_files_for_paths(repo_root, baseline_sha, SCHEMA_SOURCE_PATHS)
            == _git_files_for_paths(repo_root, candidate_sha, SCHEMA_SOURCE_PATHS),
            "candidate schema or migration source differs from the reconciled source")
    path = reconciliation.get("reconciliation_path")
    require(path == "sql/reconciliation/20261004_production_schema_reconciliation.sql",
            "schema reconciliation path is invalid")
    raw = _git(repo_root, "show", f"{candidate_sha}:{path}").decode()
    require(migration_statements_sha256(normalize_migration_statements(raw))
            == reconciliation.get("reconciliation_sha256"), "schema reconciliation SQL differs from the candidate")
    require(reconciliation.get("expected_relation_count") == summary["required_relation_count"],
            "schema reconciliation relation count differs from the current contract")
    rebased = dict(current)
    rebased["main_sha"] = baseline_sha
    rebased.pop("receipt_sha256", None)
    baseline_inventory_digest = hashlib.sha256(canonical_json(rebased).encode()).hexdigest()
    require(baseline_inventory_digest == reconciliation.get("after_inventory_receipt_sha256"),
            "production schema or protected roots changed since reconciliation")
    return {
        **summary,
        "state_unchanged_since_reconciliation": True,
        "continuity_scope": "one_time_full_inventory_baseline",
        "reconciliation_main_sha": baseline_sha,
        "reconciliation_receipt_sha256": receipt_digest,
        "reconciliation_sql_sha256": reconciliation["reconciliation_sha256"],
    }


def validate_auth_configuration(config: Mapping[str, object], site_origin: str) -> dict[str, object]:
    """Validate only the safe Auth fields needed for the owner-only boundary."""
    required = {
        "disable_signup": True,
        "external_email_enabled": True,
        "jwt_exp": 900,
        "mailer_autoconfirm": False,
        "mailer_allow_unverified_email_sign_ins": False,
        "mailer_otp_exp": 600,
        "mailer_otp_length": 6,
        "mailer_secure_email_change_enabled": True,
        "site_url": site_origin,
        "uri_allow_list": site_origin,
    }
    require(all(config.get(key) == value for key, value in required.items()), "hosted Auth configuration is unsafe")
    email_configuration = validate_email_otp_configuration({
        "mailer_otp_length": config.get("mailer_otp_length"),
        "mailer_templates_magic_link_content": config.get("mailer_templates_magic_link_content"),
        "mailer_templates_recovery_content": config.get("mailer_templates_recovery_content"),
    })
    return {
        "status": "verified",
        "signup_disabled": True,
        "email_enabled": True,
        "email_autoconfirm": False,
        "jwt_expiry_seconds": 900,
        "otp_length": 6,
        "otp_expiry_seconds": 600,
        "email_flow": email_configuration["email_flow"],
        "recovery_flow": email_configuration["recovery_flow"],
        "redirect_origin": site_origin,
    }


AuthRequester = Callable[[str, str, Mapping[str, str]], tuple[int, bytes]]
HttpRequester = Callable[[str, Mapping[str, str]], tuple[int, Mapping[str, str], bytes]]


def _auth_get(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = Request(url, method="GET", headers=dict(headers))
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()
    except (URLError, OSError) as error:
        raise RuntimeError("Supabase Auth inventory request failed") from error


def inspect_single_owner(project_url: str, owner_email: str, service_key: str,
                         *, requester: AuthRequester = _auth_get) -> dict[str, object]:
    """Read and verify the single confirmed owner; never create or update a user."""
    project_url, normalized_email, service_key = validate_auth_admin_configuration(project_url, owner_email, service_key)
    status, body = requester(
        f"{project_url}/auth/v1/admin/users?page=1&per_page=2",
        {"apikey": service_key, "authorization": f"Bearer {service_key}"},
    )
    require(200 <= status < 300, "Supabase Auth owner inventory request failed")
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("Supabase Auth owner inventory is malformed") from error
    users = payload.get("users") if isinstance(payload, Mapping) else None
    require(isinstance(users, list) and len(users) == 1 and isinstance(users[0], Mapping),
            "Auth must contain exactly one owner")
    user = users[0]
    identity = user.get("id")
    confirmed = user.get("email_confirmed_at") or user.get("confirmed_at")
    require(str(user.get("email", "")).strip().lower() == normalized_email
            and isinstance(identity, str) and AUTH_UUID.fullmatch(identity) is not None
            and isinstance(confirmed, str) and bool(confirmed), "Auth must contain exactly one confirmed owner")
    return {
        "status": "verified",
        "auth_user_count": 1,
        "owner_id_digest": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "owner_secret_sha256": hashlib.sha256(identity.encode()).hexdigest(),
        "owner_email_digest": hashlib.sha256(normalized_email.encode()).hexdigest()[:16],
    }


def attest_managed_secret_bindings(adapter: object, site_origin: str, owner_secret_sha256: str,
                                   database_url_sha256: str) -> dict[str, object]:
    """Bind active Edge secret digests without receiving their values."""
    require(_DIGEST.fullmatch(owner_secret_sha256 or "") is not None
            and _DIGEST.fullmatch(database_url_sha256 or "") is not None,
            "protected managed-secret reference digests are invalid")
    rows = adapter.managed_secret_digests()
    require(isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows),
            "managed runtime secret inventory is unavailable")
    digests = {row.get("name"): row.get("digest") for row in rows}
    require(set(digests) == {
        "DASHBOARD_ALLOWED_ORIGINS", "DASHBOARD_DATABASE_URL", "DASHBOARD_OWNER_USER_ID",
    } and all(isinstance(value, str) and _DIGEST.fullmatch(value) is not None for value in digests.values()),
            "managed runtime secret inventory is incomplete")
    require(digests["DASHBOARD_ALLOWED_ORIGINS"] == hashlib.sha256(site_origin.encode()).hexdigest()
            and digests["DASHBOARD_OWNER_USER_ID"] == owner_secret_sha256
            and digests["DASHBOARD_DATABASE_URL"] == database_url_sha256,
            "managed runtime secret binding differs from the verified owner, Site, or preserved database URL")
    return {
        "status": "verified",
        "managed_secret_count": 3,
        "owner_binding": True,
        "origin_binding": True,
        "database_url_unchanged": True,
    }


def _http_get(url: str, headers: Mapping[str, str]) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, method="GET", headers=dict(headers))
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()
    except (URLError, OSError) as error:
        raise RuntimeError("dashboard API boundary request failed") from error


def verify_anonymous_denial(api_url: str, site_origin: str,
                            *, requester: HttpRequester = _http_get) -> dict[str, object]:
    status, headers, body = requester(api_url + "/v1/meta", {"origin": site_origin})
    lowered = {key.lower(): value for key, value in headers.items()}
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("anonymous dashboard denial is malformed") from error
    require(status == 401 and isinstance(payload, Mapping)
            and isinstance(payload.get("error"), Mapping) and payload["error"].get("code") == "unauthorized",
            "anonymous dashboard request was not denied")
    require(lowered.get("access-control-allow-origin") == site_origin
            and lowered.get("cache-control") == "no-store", "anonymous dashboard denial headers are unsafe")
    return {"status": "verified", "method": "GET", "anonymous_status": 401}


def verify_authenticated_owner_read(
    api_url: str,
    site_origin: str,
    owner_email: str,
    service_key: str,
    publishable_key: str,
    *,
    obtain_token=obtain_ephemeral_owner_access_token,
    revoke_token=revoke_ephemeral_owner_session,
    requester: HttpRequester = _http_get,
) -> dict[str, object]:
    """Create one short-lived owner session, perform one GET-backed DB read, and revoke it."""
    project_url = api_url.split("/functions/v1/", 1)[0]
    token = obtain_token(project_url, owner_email, site_origin, service_key, publishable_key)
    revoked: Mapping[str, object] | None = None
    try:
        status, headers, body = requester(
            api_url + "/v1/meta",
            {"origin": site_origin, "authorization": f"Bearer {token}"},
        )
        lowered = {key.lower(): value for key, value in headers.items()}
        require(status == 200 and lowered.get("access-control-allow-origin") == site_origin
                and lowered.get("cache-control") == "no-store", "authenticated owner dashboard read failed")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RuntimeError("authenticated owner dashboard receipt is malformed") from error
        expected_boundaries = {
            "owner_only": True,
            "suggestion_only": True,
            "friend_invitations": "disabled",
            "brokerage_authority": "none",
        }
        require(isinstance(payload, Mapping), "authenticated owner dashboard receipt is malformed")
        data = payload.get("data")
        require(payload.get("contract_version") == 1 and isinstance(payload.get("data_as_of"), str)
                and payload.get("freshness") in {"fresh", "stale", "partial", "unavailable"}
                and isinstance(data, Mapping) and data.get("boundaries") == expected_boundaries,
                "authenticated owner dashboard receipt is malformed")
    finally:
        revoked = revoke_token(project_url, token, publishable_key, scope="local")
    require(isinstance(revoked, Mapping) and revoked.get("status") == "revoked"
            and revoked.get("scope") == "local", "ephemeral owner session was not revoked")
    return {
        "status": "verified",
        "method": "GET",
        "owner_status": 200,
        "database_read": True,
        "ephemeral_session": "current_session_revoked",
    }


def _load_json(path: Path, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is unavailable or malformed") from error
    require(isinstance(value, Mapping), f"{label} is unavailable or malformed")
    return value


def create_attestation(candidate_sha: str, project_ref: str, native_site_receipt: Mapping[str, object],
                       protected_build_receipt: Mapping[str, object],
                       reconciliation_receipt: Mapping[str, object], reconciliation_provenance: Mapping[str, object],
                       *, repo_root: Path = ROOT, management_request=None,
                       adapter: object | None = None) -> dict[str, object]:
    """Collect the complete split-trust attestation through read-only transports."""
    require(SHA.fullmatch(candidate_sha) is not None, "candidate SHA is malformed")
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    owner_email = os.environ.get("DASHBOARD_OWNER_EMAIL", "")
    database_url_sha256 = os.environ.get("DASHBOARD_DATABASE_URL_SHA256", "")
    require(access_token and service_key and publishable_key and owner_email and database_url_sha256,
            "protected attestation configuration is incomplete")
    management = SupabaseManagementApi(access_token, request=management_request)
    site = validate_native_site_receipt(
        native_site_receipt, candidate_sha, project_ref, repo_root,
        protected_build_receipt=protected_build_receipt,
    )
    inventory = inspect_production_schema(management, project_ref, candidate_sha)
    schema = validate_reconciliation_continuity(
        inventory, reconciliation_receipt, project_ref, candidate_sha, repo_root,
    )
    require(isinstance(reconciliation_provenance, Mapping)
            and set(reconciliation_provenance) == {"workflow_run_id", "artifact_id", "artifact_digest"}
            and type(reconciliation_provenance.get("workflow_run_id")) is int
            and reconciliation_provenance["workflow_run_id"] > 0
            and type(reconciliation_provenance.get("artifact_id")) is int
            and reconciliation_provenance["artifact_id"] > 0
            and isinstance(reconciliation_provenance.get("artifact_digest"), str)
            and _SITE_HASH.fullmatch(reconciliation_provenance["artifact_digest"]) is not None,
            "schema reconciliation GitHub provenance is invalid")
    schema["source_workflow"] = dict(reconciliation_provenance)
    function_adapter = adapter or NativeReleaseAdapter(
        {"project_ref": project_ref},
        environment={"SUPABASE_ACCESS_TOKEN": access_token},
        repo_root=repo_root,
    )
    functions = attest_edge_functions(function_adapter, candidate_sha, repo_root)
    auth_raw = management("GET", f"/v1/projects/{project_ref}/config/auth")
    require(isinstance(auth_raw, Mapping), "hosted Auth configuration is unavailable")
    auth = validate_auth_configuration(auth_raw, str(site["live_url"]))
    project_url = f"https://{project_ref}.supabase.co"
    owner = inspect_single_owner(project_url, owner_email, service_key)
    secret_bindings = attest_managed_secret_bindings(
        function_adapter, str(site["live_url"]), str(owner["owner_secret_sha256"]), database_url_sha256,
    )
    owner.pop("owner_secret_sha256")
    api_url = f"{project_url}/functions/v1/owner-dashboard-api"
    anonymous = verify_anonymous_denial(api_url, str(site["live_url"]))
    authenticated = verify_authenticated_owner_read(
        api_url, str(site["live_url"]), owner_email, service_key, publishable_key,
    )
    receipt: dict[str, object] = {
        "format": "stocks-existing-v1-runtime-attestation-v1",
        "attested_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "candidate_sha": candidate_sha,
        "production_binding_sha256": hashlib.sha256(
            ("stocks-existing-v1-runtime-attestation-v1\0" + project_ref).encode()
        ).hexdigest(),
        "trust_domains": ["codex-native-sites-connector", "github-protected-supabase-management"],
        "site": site,
        "database": schema,
        "edge_functions": functions,
        "auth": {**auth, **owner, "managed_secret_bindings": secret_bindings},
        "api_boundary": {"anonymous": anonymous, "owner": authenticated},
        "product_boundaries": {
            "owner_only": True,
            "suggestion_only": True,
            "brokerage_authority": "none",
            "incremental_provider_cost_usd": 0,
        },
        "mutation_counts": {
            "database_writes": 0,
            "function_deployments": 0,
            "site_deployments": 0,
            "secret_rotations": 0,
            "scheduled_runs_started": 0,
            "ephemeral_auth_sessions_created": 1,
            "ephemeral_auth_sessions_revoked": 1,
        },
    }
    receipt["receipt_sha256"] = hashlib.sha256(canonical_json(receipt).encode()).hexdigest()
    require(len(canonical_json(receipt).encode()) <= MAX_RECEIPT_BYTES, "attestation receipt exceeds bounds")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--native-site-receipt", type=Path, required=True)
    parser.add_argument("--protected-build-receipt", type=Path, required=True)
    parser.add_argument("--reconciliation-receipt", type=Path, required=True)
    parser.add_argument("--reconciliation-workflow-run-id", type=int, required=True)
    parser.add_argument("--reconciliation-artifact-id", type=int, required=True)
    parser.add_argument("--reconciliation-artifact-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.parse_args()
    raise SystemExit(
        "the one-time existing-runtime attestation is retired; use the current "
        "protected release and direct native Sites connector sequence"
    )


if __name__ == "__main__":
    raise SystemExit(main())
