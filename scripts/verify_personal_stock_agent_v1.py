#!/usr/bin/env python3
"""Fail closed unless every Personal Stock Agent V1 release receipt is present."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Mapping


REQUIRED_GATES = (
    "exact_head_ci",
    "independent_review",
    "quota_receipts",
    "dry_run_zero_writes",
    "dry_run_zero_sends",
    "migration_version",
    "gateway_version",
    "dashboard_api_version",
    "site_version",
    "owner_canary",
    "anonymous_denial",
    "non_owner_denial",
    "source_parity",
    "scheduled_receipt",
    "rollback_check",
)


def verify_release(receipt: Mapping[str, object]) -> dict[str, object]:
    for gate in REQUIRED_GATES:
        if not receipt.get(gate):
            raise RuntimeError(f"missing release gate: {gate}")
    if receipt["dry_run_zero_writes"] is not True or receipt["dry_run_zero_sends"] is not True:
        raise RuntimeError("dry-run side-effect gate failed")
    candidate_sha = receipt.get("candidate_sha")
    if not isinstance(candidate_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
        raise RuntimeError("missing release gate: candidate_sha")
    expected_statuses = {
        "exact_head_ci": "passed",
        "independent_review": "passed",
        "quota_receipts": "verified",
        "owner_canary": "verified",
        "anonymous_denial": "verified",
        "non_owner_denial": "verified",
        "source_parity": "verified",
        "scheduled_receipt": "verified",
        "rollback_check": "verified",
    }
    for gate, expected in expected_statuses.items():
        value = receipt[gate]
        if not isinstance(value, Mapping) or value.get("status") != expected:
            raise RuntimeError(f"invalid release gate: {gate}")
    for gate in ("exact_head_ci", "independent_review", "source_parity"):
        if receipt[gate].get("candidate_sha") != candidate_sha:  # type: ignore[union-attr]
            raise RuntimeError(f"release gate SHA mismatch: {gate}")
    if receipt["migration_version"] != "20260907,20260908":
        raise RuntimeError("invalid release gate: migration_version")
    for gate in ("gateway_version", "dashboard_api_version"):
        if isinstance(receipt[gate], bool) or not isinstance(receipt[gate], int) or receipt[gate] <= 0:
            raise RuntimeError(f"invalid release gate: {gate}")
    if not isinstance(receipt["site_version"], str) or not receipt["site_version"].strip():
        raise RuntimeError("invalid release gate: site_version")
    return {"status": "verified", "candidate_sha": candidate_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    arguments = parser.parse_args()
    try:
        value = json.loads(arguments.receipt.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("release receipt is unavailable or malformed") from error
    if not isinstance(value, dict):
        raise SystemExit("release receipt must be a JSON object")
    print(json.dumps(verify_release(value), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
