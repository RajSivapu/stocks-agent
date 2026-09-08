#!/usr/bin/env python3
"""Compare one exact-candidate Site package; never certify connector provenance."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.attest_existing_v1_runtime import (
    MAX_RECEIPT_BYTES, PROJECT_REF, _static_build_files,
)
from scripts.verify_personal_stock_agent_v1 import SHA, path_is_safe, tree_sha256


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _git(repo: Path, *arguments: str) -> bytes:
    result = __import__("subprocess").run(
        ["git", *arguments], cwd=repo, capture_output=True, check=False,
    )
    require(result.returncode == 0, "candidate Git object is unavailable")
    return result.stdout


def _package_files(raw: object) -> dict[str, bytes]:
    require(isinstance(raw, bytes), "native Site package requires archive bytes")
    require(0 < len(raw) <= 100_000_000, "native Site package archive is unavailable")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            names = [member.name.rstrip("/") for member in members]
            require(
                members and len(members) <= 10_000
                and len(names) == len(set(names))
                and all(
                    path_is_safe(name)
                    and (member.isdir() or member.isfile())
                    and member.size <= 10_000_000
                    for name, member in zip(names, members, strict=True)
                )
                and sum(member.size for member in members) <= 100_000_000,
                "native Site package archive contains unsafe members",
            )
            files = {
                member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
                for member in members if member.isfile()
            }
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError("native Site package archive is unreadable") from error
    require(files, "native Site package archive is empty")
    return files


def compare_native_site_release(
    archive: bytes,
    candidate_sha: str,
    project_ref: str,
    repo_root: Path = ROOT,
    *,
    static_root: Path | None = None,
    protected_build_receipt: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Bind local package bytes to protected build bytes and leave Site proof pending."""
    require(SHA.fullmatch(candidate_sha) is not None, "exact candidate SHA is malformed")
    require(PROJECT_REF.fullmatch(project_ref) is not None,
            "exact production project identity is required")
    try:
        candidate_hosting = json.loads(
            _git(repo_root, "show", f"{candidate_sha}:.openai/hosting.json")
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("candidate Sites manifest is malformed") from error
    require(
        isinstance(candidate_hosting, Mapping)
        and set(candidate_hosting) == {"project_id", "static"}
        and isinstance(candidate_hosting.get("project_id"), str)
        and bool(candidate_hosting["project_id"])
        and candidate_hosting.get("static") == {
            "directory": "dist",
            "not_found_handling": "single-page-application",
        },
        "candidate Sites manifest is unsupported",
    )

    build_files = _static_build_files(static_root or repo_root / "dist")
    build_hashes = {
        path: hashlib.sha256(raw).hexdigest() for path, raw in build_files.items()
    }
    require(
        isinstance(protected_build_receipt, Mapping)
        and protected_build_receipt.get("status") == "verified"
        and protected_build_receipt.get("candidate_sha") == candidate_sha
        and protected_build_receipt.get("files") == build_hashes
        and protected_build_receipt.get("build_sha256") == tree_sha256(build_files),
        "native Site package differs from the protected build receipt",
    )

    files = _package_files(archive)
    expected_paths = {"dist/.openai/hosting.json"} | {
        f"dist/{path}" for path in build_files
    }
    require(set(files) == expected_paths, "native Site package archive coverage differs from build")
    try:
        archived_hosting = json.loads(files["dist/.openai/hosting.json"])
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("native Site package manifest is malformed") from error
    require(archived_hosting == candidate_hosting,
            "native Site package manifest differs from candidate")
    require(
        all(files[f"dist/{path}"] == raw for path, raw in build_files.items()),
        "native Site package bytes differ from protected build",
    )

    return {
        "status": "content_consistent",
        "candidate_sha": candidate_sha,
        "project_ref": project_ref,
        "site_project_id": candidate_hosting["project_id"],
        "package_content_hash": "sha256:" + hashlib.sha256(archive).hexdigest(),
        "package_file_count": len(files),
        "package_size_bytes": len(archive),
        "protected_build_sha256": tree_sha256(build_files),
        "evidence_class": {
            "owner_site": {
                "status": "pending",
                "required_evidence": "current_authenticated_native_connector_observation",
            },
        },
        "connector_requirements": {
            "exact_active_version_and_candidate": "pending",
            "owner_only_access": "pending",
            "successful_publish_deployment": "pending",
            "authenticated_live_byte_parity": "pending",
            "provider_retained_rollback_version": "pending",
        },
    }


def _load_bytes(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RuntimeError("native Site package is unavailable or unsafe")
    return path.read_bytes()


def _load_receipt(path: Path) -> Mapping[str, object]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RuntimeError("protected build receipt is unavailable or unsafe")
    value = json.loads(path.read_text())
    if not isinstance(value, Mapping):
        raise RuntimeError("protected build receipt is malformed")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--static-root", type=Path, default=ROOT / "dist")
    parser.add_argument("--protected-build-receipt", required=True, type=Path)
    arguments = parser.parse_args()
    result = compare_native_site_release(
        _load_bytes(arguments.archive), arguments.candidate_sha,
        arguments.project_ref, arguments.repo_root, static_root=arguments.static_root,
        protected_build_receipt=_load_receipt(arguments.protected_build_receipt),
    )
    arguments.output.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
