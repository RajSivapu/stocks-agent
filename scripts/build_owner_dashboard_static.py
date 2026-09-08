#!/usr/bin/env python3
"""Build a verified owner-dashboard static release without logging API keys."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Mapping, Sequence
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SUPABASE_CLI_VERSION = "2.116.0"
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")


def select_publishable_key(keys: Sequence[dict[str, object]]) -> str:
    candidates = [
        row.get("api_key") for row in keys
        if row.get("type") == "publishable" and isinstance(row.get("api_key"), str)
    ]
    if len(candidates) != 1 or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{24,128}", candidates[0]):
        raise RuntimeError("exactly one valid publishable Supabase key is required")
    return candidates[0]


def _validate(project_ref: str, site_origin: str) -> None:
    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise ValueError("project reference is not canonical")
    parsed = urlparse(site_origin)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.params
        or parsed.query or parsed.fragment or site_origin != f"https://{parsed.netloc}"
    ):
        raise ValueError("site origin must be exact HTTPS")


def _run(command: list[str], *, repo_root: Path, runner: Callable[..., object], **options):
    return runner(command, cwd=repo_root, capture_output=True, text=True, check=False, **options)


def _child_environment(values: Mapping[str, str], *extra: str) -> dict[str, str]:
    """Expose only process/runtime settings plus explicitly named public values."""
    allowed = ("PATH", "HOME", "TMPDIR", "CI", "NPM_CONFIG_CACHE", "NO_COLOR", *extra)
    return {key: values[key] for key in allowed if isinstance(values.get(key), str)}


def _file_manifest(root: Path) -> tuple[dict[str, str], str]:
    files: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        files[relative] = hashlib.sha256(raw).hexdigest()
        digest.update(relative.encode() + b"\0" + raw + b"\0")
    return files, digest.hexdigest()


def build_static_release(
    project_ref: str,
    site_origin: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
    *,
    publishable_key: str | None = None,
    candidate_sha: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    _validate(project_ref, site_origin)
    process_environment = dict(os.environ if environment is None else environment)
    if publishable_key is None:
        key_result = _run(
            [
                "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "projects", "api-keys",
                "--project-ref", project_ref, "--output", "json",
            ],
            repo_root=repo_root,
            runner=runner,
            env=_child_environment(process_environment, "SUPABASE_ACCESS_TOKEN"),
        )
        if getattr(key_result, "returncode", 1) != 0:
            raise RuntimeError("Supabase publishable key inventory is unavailable")
        try:
            publishable_key = select_publishable_key(json.loads(str(getattr(key_result, "stdout", ""))))
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("Supabase publishable key inventory is malformed") from error
    elif not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{24,128}", publishable_key):
        raise RuntimeError("valid publishable Supabase key is required")
    if candidate_sha is not None and re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is None:
        raise RuntimeError("candidate SHA is malformed")

    supabase_origin = f"https://{project_ref}.supabase.co"
    api_url = f"{supabase_origin}/functions/v1/owner-dashboard-api"
    build_env = {
        **_child_environment(process_environment),
        "VITE_SUPABASE_URL": supabase_origin,
        "VITE_DASHBOARD_API_URL": api_url,
        "VITE_SUPABASE_PUBLISHABLE_KEY": publishable_key,
    }
    build_result = _run(
        ["npm", "run", "build", "--workspace", "@stocks-agent/web"],
        repo_root=repo_root,
        runner=runner,
        env=build_env,
    )
    if getattr(build_result, "returncode", 1) != 0:
        raise RuntimeError("owner dashboard production build failed")
    application_output = repo_root / "apps/web/dist"
    static_output = repo_root / "dist"
    if any(path.is_symlink() for path in application_output.rglob("*")):
        raise RuntimeError("owner dashboard static output contains a symlink")
    if not (application_output / "index.html").is_file():
        raise RuntimeError("owner dashboard production output is incomplete")
    if static_output.is_symlink():
        raise RuntimeError("owner dashboard hosting output cannot be a symlink")
    if static_output.exists():
        shutil.rmtree(static_output)
    shutil.copytree(application_output, static_output)
    scan_result = _run(
        ["node", "scripts/check_dashboard_bundle.mjs", "dist"],
        repo_root=repo_root,
        runner=runner,
        env=_child_environment(process_environment),
    )
    if getattr(scan_result, "returncode", 1) != 0:
        raise RuntimeError("owner dashboard bundle verification failed")
    try:
        scan = json.loads(str(getattr(scan_result, "stdout", "")))
    except json.JSONDecodeError as error:
        raise RuntimeError("owner dashboard bundle receipt is malformed") from error
    if scan.get("status") != "verified" or not isinstance(scan.get("hashes"), list):
        raise RuntimeError("owner dashboard bundle receipt is incomplete")
    files, build_sha256 = _file_manifest(static_output)
    receipt = {
        "status": "verified",
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_ref_digest": hashlib.sha256(project_ref.encode()).hexdigest()[:16],
        "site_origin": site_origin,
        "api_origin": api_url,
        "static_directory": "dist",
        "file_count": scan.get("file_count"),
        "initial_js_gzip_bytes": scan.get("initial_js_gzip_bytes"),
        "asset_hashes": scan["hashes"],
        "files": files,
        "build_sha256": build_sha256,
    }
    if candidate_sha is not None:
        receipt["candidate_sha"] = candidate_sha
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--site-origin", required=True)
    parser.add_argument("--publishable-key")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    receipt = build_static_release(
        arguments.project_ref, arguments.site_origin,
        publishable_key=arguments.publishable_key, candidate_sha=arguments.candidate_sha,
    )
    rendered = json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
