#!/usr/bin/env python3
"""Build a private-data-free Gate A-G release acceptance report."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_EVIDENCE_BYTES = 256 * 1024
CRITERION_GATES = {
    "migration_parity": "A",
    "anonymous_private_denial": "A",
    "two_owner_isolation": "A",
    "runtime_secret_boundary": "A",
    "preview_atomic_receipts": "B",
    "financial_input_validation": "B",
    "ledger_projection": "B",
    "run_slot_exclusivity": "B",
    "model_authority_boundary": "B",
    "fresh_intraday_evidence": "B",
    "telegram_security": "B",
    "claude_real_handshake": "C",
    "phone_otp_and_step_up": "D",
    "consent_disclosures": "D",
    "encrypted_recovery": "E",
    "deployment_rollback_drill": "F",
    "no_brokerage_surface": "G",
    "friend_onboarding_cycle": "G",
}
REQUIRED_CRITERIA = tuple(CRITERION_GATES)
MANUAL_CRITERIA = {
    "claude_real_handshake",
    "phone_otp_and_step_up",
    "encrypted_recovery",
    "deployment_rollback_drill",
    "friend_onboarding_cycle",
}
MAX_AGE = {
    "encrypted_recovery": timedelta(days=30),
    "deployment_rollback_drill": timedelta(days=7),
    "friend_onboarding_cycle": timedelta(days=7),
    "phone_otp_and_step_up": timedelta(days=7),
}


class AcceptanceRejected(RuntimeError):
    """Acceptance evidence is incomplete, stale, or unsafe to publish."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise AcceptanceRejected("acceptance evidence is not canonical") from error


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise AcceptanceRejected(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AcceptanceRejected(f"{label} is invalid") from error
    if parsed.tzinfo is None:
        raise AcceptanceRejected(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def build_acceptance_report(
    source: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    if set(source) != {"version", "commit", "evidence"} or source.get("version") != 1:
        raise AcceptanceRejected("acceptance source fields are invalid")
    commit = source.get("commit")
    entries = source.get("evidence")
    if not isinstance(commit, str) or not SHA_RE.fullmatch(commit):
        raise AcceptanceRejected("acceptance commit is invalid")
    if not isinstance(entries, list) or len(entries) != len(REQUIRED_CRITERIA):
        raise AcceptanceRejected("acceptance evidence is incomplete")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    accepted: dict[str, dict[str, str]] = {}
    expected_fields = {"criterion", "status", "checked_at", "evidence_hash", "method"}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != expected_fields:
            raise AcceptanceRejected("acceptance evidence fields are invalid")
        criterion = entry.get("criterion")
        if not isinstance(criterion, str) or criterion not in CRITERION_GATES:
            raise AcceptanceRejected("acceptance criterion is invalid")
        if criterion in accepted:
            raise AcceptanceRejected("acceptance criterion is duplicated")
        if entry.get("status") != "passed":
            raise AcceptanceRejected("acceptance criterion did not pass")
        evidence_hash = entry.get("evidence_hash")
        if not isinstance(evidence_hash, str) or not DIGEST_RE.fullmatch(evidence_hash):
            raise AcceptanceRejected("acceptance evidence hash is invalid")
        expected_method = "signed_manual" if criterion in MANUAL_CRITERIA else "automated"
        if entry.get("method") != expected_method:
            raise AcceptanceRejected("acceptance evidence method is invalid")
        checked_at = _timestamp(entry.get("checked_at"), "acceptance evidence time")
        if checked_at > observed_at + timedelta(hours=1):
            raise AcceptanceRejected("acceptance evidence is future-dated")
        max_age = MAX_AGE.get(criterion, timedelta(hours=24))
        if checked_at < observed_at - max_age:
            raise AcceptanceRejected("acceptance evidence is stale")
        accepted[criterion] = {
            "criterion": criterion,
            "gate": CRITERION_GATES[criterion],
            "evidence_hash": evidence_hash,
        }
    if set(accepted) != set(REQUIRED_CRITERIA):
        raise AcceptanceRejected("acceptance evidence is incomplete")

    report: dict[str, Any] = {
        "version": 1,
        "status": "passed",
        "commit": commit,
        "generated_at": observed_at.isoformat().replace("+00:00", "Z"),
        "private_data": False,
        "gates": {gate: "passed" for gate in "ABCDEFG"},
        "criteria": [accepted[name] for name in REQUIRED_CRITERIA],
    }
    report["evidence_digest"] = hashlib.sha256(_canonical(report)).hexdigest()
    return report


def validate_acceptance_report(
    report: Mapping[str, Any], *, expected_commit: str, now: datetime | None = None
) -> dict[str, Any]:
    expected_fields = {
        "version", "status", "commit", "generated_at", "private_data", "gates",
        "criteria", "evidence_digest",
    }
    if set(report) != expected_fields or report.get("version") != 1 or report.get("status") != "passed":
        raise AcceptanceRejected("acceptance report is invalid")
    if report.get("commit") != expected_commit or report.get("private_data") is not False:
        raise AcceptanceRejected("acceptance report identity is invalid")
    if report.get("gates") != {gate: "passed" for gate in "ABCDEFG"}:
        raise AcceptanceRejected("acceptance report gates are incomplete")
    criteria = report.get("criteria")
    expected_criteria = [
        {"criterion": name, "gate": CRITERION_GATES[name], "evidence_hash": None}
        for name in REQUIRED_CRITERIA
    ]
    if not isinstance(criteria, list) or len(criteria) != len(expected_criteria):
        raise AcceptanceRejected("acceptance report criteria are incomplete")
    for actual, expected in zip(criteria, expected_criteria, strict=True):
        if not isinstance(actual, Mapping) or set(actual) != set(expected):
            raise AcceptanceRejected("acceptance report criterion is invalid")
        if actual.get("criterion") != expected["criterion"] or actual.get("gate") != expected["gate"]:
            raise AcceptanceRejected("acceptance report criterion is invalid")
        if not isinstance(actual.get("evidence_hash"), str) or not DIGEST_RE.fullmatch(actual["evidence_hash"]):
            raise AcceptanceRejected("acceptance report evidence hash is invalid")
    generated = _timestamp(report.get("generated_at"), "acceptance report time")
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if generated > observed_at + timedelta(hours=1) or generated < observed_at - timedelta(hours=24):
        raise AcceptanceRejected("acceptance report is stale")
    digest = report.get("evidence_digest")
    unsigned = {key: report[key] for key in report if key != "evidence_digest"}
    expected_digest = hashlib.sha256(_canonical(unsigned)).hexdigest()
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest) or not hmac.compare_digest(
        digest, expected_digest
    ):
        raise AcceptanceRejected("acceptance report digest is invalid")
    return dict(report)


def write_private_report(path: Path, report: Mapping[str, Any]) -> None:
    validated = validate_acceptance_report(
        report, expected_commit=str(report.get("commit"))
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(validated, output, sort_keys=True, separators=(",", ":"))
        output.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = args.evidence.read_bytes()
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise AcceptanceRejected("acceptance evidence is too large")
        source = json.loads(raw)
        if not isinstance(source, dict):
            raise AcceptanceRejected("acceptance evidence is malformed")
        report = build_acceptance_report(source)
        write_private_report(args.report, report)
        print(json.dumps({"status": "passed", "evidence_digest": report["evidence_digest"]}))
        return 0
    except (AcceptanceRejected, FileExistsError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("release acceptance rejected", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
