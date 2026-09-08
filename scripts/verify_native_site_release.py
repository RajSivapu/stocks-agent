#!/usr/bin/env python3
"""Verify one exact-candidate, owner-only native Sites release receipt."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.attest_existing_v1_runtime import (
    MAX_RECEIPT_BYTES,
    validate_native_site_receipt,
)


def verify_native_site_release(
    receipt: Mapping[str, object],
    candidate_sha: str,
    project_ref: str,
    repo_root: Path = ROOT,
    *,
    now: datetime | None = None,
    static_root: Path | None = None,
    live_reader=None,
) -> dict[str, object]:
    """Close the owner-Site class without relabeling backend or scheduled proof."""
    version = receipt.get("active_version") if isinstance(receipt, Mapping) else None
    if not isinstance(version, Mapping) or version.get("source_commit_sha") != candidate_sha:
        raise RuntimeError("native Site release does not bind the exact candidate")
    verified = validate_native_site_receipt(
        receipt, candidate_sha, project_ref, repo_root, now=now,
        static_root=static_root, live_reader=live_reader,
    )
    return {
        "status": "verified",
        "candidate_sha": candidate_sha,
        "project_ref": project_ref,
        "evidence_class": {
            "owner_site": {"status": "verified", "candidate_sha": candidate_sha},
        },
        "native_site": verified,
    }


def _load_receipt(path: Path) -> Mapping[str, object]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_RECEIPT_BYTES:
        raise RuntimeError("native Site receipt is unavailable or unsafe")
    value = json.loads(path.read_text())
    if not isinstance(value, Mapping):
        raise RuntimeError("native Site receipt is malformed")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--static-root", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    result = verify_native_site_release(
        _load_receipt(arguments.receipt), arguments.candidate_sha,
        arguments.project_ref, arguments.repo_root, static_root=arguments.static_root,
    )
    arguments.output.write_text(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
