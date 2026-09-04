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
from typing import Callable, Sequence
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


def build_static_release(
    project_ref: str,
    site_origin: str,
    repo_root: Path = ROOT,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    _validate(project_ref, site_origin)
    key_result = _run(
        [
            "npx", "--yes", f"supabase@{SUPABASE_CLI_VERSION}", "projects", "api-keys",
            "--project-ref", project_ref, "--output", "json",
        ],
        repo_root=repo_root,
        runner=runner,
    )
    if getattr(key_result, "returncode", 1) != 0:
        raise RuntimeError("Supabase publishable key inventory is unavailable")
    try:
        publishable_key = select_publishable_key(json.loads(str(getattr(key_result, "stdout", ""))))
    except (json.JSONDecodeError, TypeError) as error:
        raise RuntimeError("Supabase publishable key inventory is malformed") from error

    supabase_origin = f"https://{project_ref}.supabase.co"
    api_url = f"{supabase_origin}/functions/v1/owner-dashboard-api"
    build_env = {
        **os.environ,
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
    )
    if getattr(scan_result, "returncode", 1) != 0:
        raise RuntimeError("owner dashboard bundle verification failed")
    try:
        scan = json.loads(str(getattr(scan_result, "stdout", "")))
    except json.JSONDecodeError as error:
        raise RuntimeError("owner dashboard bundle receipt is malformed") from error
    if scan.get("status") != "verified" or not isinstance(scan.get("hashes"), list):
        raise RuntimeError("owner dashboard bundle receipt is incomplete")
    return {
        "status": "verified",
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_ref_digest": hashlib.sha256(project_ref.encode()).hexdigest()[:16],
        "site_origin": site_origin,
        "api_origin": api_url,
        "static_directory": "dist",
        "file_count": scan.get("file_count"),
        "initial_js_gzip_bytes": scan.get("initial_js_gzip_bytes"),
        "asset_hashes": scan["hashes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--site-origin", required=True)
    arguments = parser.parse_args()
    receipt = build_static_release(arguments.project_ref, arguments.site_origin)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
