#!/usr/bin/env python3
"""Advance an owner-only cutover through immutable, hash-only receipts."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID, uuid4


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
GATES = ("0", "A", "B", "C", "D", "E")
MAX_INPUT_BYTES = 256 * 1024
EVIDENCE_MAX_AGE = timedelta(hours=24)
BACKUP_MAX_AGE = timedelta(hours=36)
RESTORE_MAX_AGE = timedelta(days=30)

MANIFEST_FIELDS = frozenset({
    "version", "candidate_commit", "rollback_commit", "checked_at", "gates",
    "gate_evidence_hashes", "backup", "restore", "legacy_routines_paused",
    "legacy_webhook_mutations_paused", "owner_identity", "parity",
})

PAUSED_PATH_NAMES = (
    "web_recordkeeping",
    "telegram_recordkeeping",
    "pre_market",
    "intraday",
    "post_market",
    "maintenance",
    "friend_invitations",
)

CUTOVER_STEPS = (
    "confirm_legacy_paths_paused",
    "verify_fresh_encrypted_backup",
    "migrate_and_backfill_owner",
    "provision_scoped_runtime_roles",
    "deploy_candidate_bundle",
    "register_replacement_telegram_webhook",
    "connect_claude_api_credential",
    "complete_no_write_handshake",
    "verify_private_views_and_projection",
    "confirm_web_recordkeeping_mutation",
    "confirm_telegram_recordkeeping_mutation",
    "revoke_legacy_runtime_authority",
    "resume_web_recordkeeping",
    "resume_telegram_recordkeeping",
    "resume_pre_market",
    "resume_intraday",
    "resume_post_market",
    "resume_maintenance",
    "observe_pre_market",
    "observe_quiet_intraday",
    "observe_active_intraday",
    "observe_post_market",
    "observe_maintenance",
    "observe_backup",
    "observe_operational_alert",
    "verify_final_counts_and_digests",
)

RESUME_STEP_PATH = {
    "resume_web_recordkeeping": "web_recordkeeping",
    "resume_telegram_recordkeeping": "telegram_recordkeeping",
    "resume_pre_market": "pre_market",
    "resume_intraday": "intraday",
    "resume_post_market": "post_market",
    "resume_maintenance": "maintenance",
}

STATE_FIELDS = frozenset({
    "version",
    "cutover_id",
    "candidate_commit",
    "rollback_commit",
    "created_at",
    "updated_at",
    "status",
    "next_step",
    "completed_steps",
    "paused_paths",
    "additive_schema_retained",
    "rollback_required",
    "failed_step",
    "failure_evidence_hash",
    "previous_bundle_restored",
    "rollback_evidence_hash",
    "precondition_digest",
    "state_digest",
})

RECEIPT_FIELDS = frozenset({
    "version",
    "cutover_id",
    "status",
    "candidate_commit",
    "rollback_commit",
    "completed_at",
    "precondition_digest",
    "completion_digest",
    "private_data",
    "invitations_enabled",
    "evidence_digest",
})


class CutoverRejected(RuntimeError):
    """The cutover cannot safely advance."""


def _canonical(value: Mapping[str, Any] | list[Any]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise CutoverRejected("cutover input is not canonical") from error


def _hash(value: Mapping[str, Any] | list[Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise CutoverRejected("cutover time is invalid")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _time(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise CutoverRejected(f"{label} time is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CutoverRejected(f"{label} time is invalid") from error
    if parsed.tzinfo is None:
        raise CutoverRejected(f"{label} time is invalid")
    return parsed.astimezone(timezone.utc)


def _fresh(
    value: Any,
    *,
    label: str,
    now: datetime,
    maximum_age: timedelta,
) -> datetime:
    parsed = _time(value, label)
    if parsed > now + timedelta(hours=1):
        raise CutoverRejected(f"{label} evidence is future-dated")
    if parsed < now - maximum_age:
        raise CutoverRejected(f"{label} evidence is stale")
    return parsed


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise CutoverRejected(f"{label} digest is invalid")
    return value


def _commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise CutoverRejected(f"{label} version is invalid")
    return value


def _validate_recovery(
    value: Any, *, label: str, now: datetime, maximum_age: timedelta
) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "status", "checked_at", "evidence_hash"
    }:
        raise CutoverRejected(f"{label} evidence fields are invalid")
    if value.get("status") != "passed":
        raise CutoverRejected(f"{label} evidence did not pass")
    _fresh(value.get("checked_at"), label=label, now=now, maximum_age=maximum_age)
    _digest(value.get("evidence_hash"), label)


def _validate_manifest(source: Mapping[str, Any], *, now: datetime) -> str:
    if set(source) != MANIFEST_FIELDS or source.get("version") != 1:
        raise CutoverRejected("cutover manifest fields are invalid")
    candidate = _commit(source.get("candidate_commit"), "candidate")
    rollback = _commit(source.get("rollback_commit"), "rollback")
    if candidate == rollback:
        raise CutoverRejected("rollback version must differ from the candidate")
    _fresh(
        source.get("checked_at"), label="gate", now=now,
        maximum_age=EVIDENCE_MAX_AGE,
    )

    gates = source.get("gates")
    hashes = source.get("gate_evidence_hashes")
    if not isinstance(gates, Mapping) or set(gates) != set(GATES):
        raise CutoverRejected("gate status is incomplete")
    if any(gates.get(gate) != "passed" for gate in GATES):
        raise CutoverRejected("gate did not pass")
    if not isinstance(hashes, Mapping) or set(hashes) != set(GATES):
        raise CutoverRejected("gate evidence is incomplete")
    for gate in GATES:
        _digest(hashes.get(gate), f"gate {gate}")

    _validate_recovery(
        source.get("backup"), label="backup", now=now, maximum_age=BACKUP_MAX_AGE
    )
    _validate_recovery(
        source.get("restore"), label="restore", now=now, maximum_age=RESTORE_MAX_AGE
    )
    if source.get("legacy_routines_paused") is not True:
        raise CutoverRejected("legacy routine triggers are not paused")
    if source.get("legacy_webhook_mutations_paused") is not True:
        raise CutoverRejected("legacy webhook mutation path is not paused")

    identity = source.get("owner_identity")
    if not isinstance(identity, Mapping) or set(identity) != {"verified", "evidence_hash"}:
        raise CutoverRejected("owner identity evidence fields are invalid")
    if identity.get("verified") is not True:
        raise CutoverRejected("owner identity is not verified")
    _digest(identity.get("evidence_hash"), "owner identity")

    parity = source.get("parity")
    if not isinstance(parity, Mapping) or set(parity) != {
        "before_count", "after_count", "before_digest", "after_digest"
    }:
        raise CutoverRejected("parity evidence fields are invalid")
    before_count = parity.get("before_count")
    after_count = parity.get("after_count")
    if (
        isinstance(before_count, bool)
        or not isinstance(before_count, int)
        or before_count < 0
        or isinstance(after_count, bool)
        or not isinstance(after_count, int)
        or after_count < 0
        or before_count != after_count
    ):
        raise CutoverRejected("parity count mismatch")
    before_digest = _digest(parity.get("before_digest"), "parity before")
    after_digest = _digest(parity.get("after_digest"), "parity after")
    if not hmac.compare_digest(before_digest, after_digest):
        raise CutoverRejected("parity digest mismatch")
    return _hash(source)


def _seal(state: dict[str, Any]) -> dict[str, Any]:
    unsigned = {key: value for key, value in state.items() if key != "state_digest"}
    state["state_digest"] = _hash(unsigned)
    return state


def prepare_cutover(
    source: Mapping[str, Any],
    *,
    now: datetime | None = None,
    cutover_id: str | None = None,
) -> dict[str, Any]:
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    precondition_digest = _validate_manifest(source, now=observed)
    identifier = cutover_id or str(uuid4())
    try:
        if str(UUID(identifier)) != identifier:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise CutoverRejected("cutover id is invalid") from error
    timestamp = _utc_text(observed)
    return _seal({
        "version": 1,
        "cutover_id": identifier,
        "candidate_commit": source["candidate_commit"],
        "rollback_commit": source["rollback_commit"],
        "created_at": timestamp,
        "updated_at": timestamp,
        "status": "prepared",
        "next_step": CUTOVER_STEPS[0],
        "completed_steps": [],
        "paused_paths": {name: True for name in PAUSED_PATH_NAMES},
        "additive_schema_retained": True,
        "rollback_required": False,
        "failed_step": None,
        "failure_evidence_hash": None,
        "previous_bundle_restored": False,
        "rollback_evidence_hash": None,
        "precondition_digest": precondition_digest,
    })


def validate_state(source: Mapping[str, Any]) -> dict[str, Any]:
    if set(source) != STATE_FIELDS or source.get("version") != 1:
        raise CutoverRejected("cutover state fields are invalid")
    digest = _digest(source.get("state_digest"), "state")
    unsigned = {key: value for key, value in source.items() if key != "state_digest"}
    if not hmac.compare_digest(digest, _hash(unsigned)):
        raise CutoverRejected("cutover state digest does not match")
    try:
        identifier = source.get("cutover_id")
        if not isinstance(identifier, str) or str(UUID(identifier)) != identifier:
            raise ValueError
    except (ValueError, TypeError) as error:
        raise CutoverRejected("cutover state id is invalid") from error
    _commit(source.get("candidate_commit"), "candidate")
    rollback = _commit(source.get("rollback_commit"), "rollback")
    if rollback == source.get("candidate_commit"):
        raise CutoverRejected("rollback version is invalid")
    created_at = _time(source.get("created_at"), "created")
    updated_at = _time(source.get("updated_at"), "updated")
    if updated_at < created_at:
        raise CutoverRejected("cutover update time is invalid")
    _digest(source.get("precondition_digest"), "precondition")
    if source.get("additive_schema_retained") is not True:
        raise CutoverRejected("additive schema retention is invalid")

    paused = source.get("paused_paths")
    if not isinstance(paused, Mapping) or set(paused) != set(PAUSED_PATH_NAMES):
        raise CutoverRejected("paused path state is invalid")
    if any(not isinstance(value, bool) for value in paused.values()):
        raise CutoverRejected("paused path state is invalid")
    if paused.get("friend_invitations") is not True:
        raise CutoverRejected("friend invitations must remain paused")

    completed = source.get("completed_steps")
    if not isinstance(completed, list) or len(completed) > len(CUTOVER_STEPS):
        raise CutoverRejected("completed cutover steps are invalid")
    for index, item in enumerate(completed):
        if not isinstance(item, Mapping) or set(item) != {
            "step", "checked_at", "evidence_hash"
        }:
            raise CutoverRejected("completed cutover step fields are invalid")
        if item.get("step") != CUTOVER_STEPS[index]:
            raise CutoverRejected("completed cutover step order is invalid")
        _time(item.get("checked_at"), "step")
        _digest(item.get("evidence_hash"), "step")

    status = source.get("status")
    if status not in {"prepared", "in_progress", "rollback_required", "rolled_back", "owner_soak_complete"}:
        raise CutoverRejected("cutover state status is invalid")
    expected_next = CUTOVER_STEPS[len(completed)] if len(completed) < len(CUTOVER_STEPS) else None
    if status in {"prepared", "in_progress"}:
        if source.get("next_step") != expected_next or source.get("rollback_required") is not False:
            raise CutoverRejected("cutover next step is invalid")
        if status == "prepared" and completed:
            raise CutoverRejected("prepared cutover has completed steps")
        if status == "in_progress" and not completed:
            raise CutoverRejected("cutover progress is invalid")
    elif source.get("next_step") is not None:
        raise CutoverRejected("cutover next step is invalid")
    if status == "owner_soak_complete":
        if len(completed) != len(CUTOVER_STEPS) or source.get("rollback_required") is not False:
            raise CutoverRejected("owner soak is incomplete")
    if status in {"rollback_required", "rolled_back"}:
        failed_step = source.get("failed_step")
        if failed_step not in CUTOVER_STEPS or failed_step != expected_next:
            raise CutoverRejected("rollback failed-step state is invalid")
        _digest(source.get("failure_evidence_hash"), "failure evidence")
    elif source.get("failed_step") is not None or source.get("failure_evidence_hash") is not None:
        raise CutoverRejected("failure evidence is invalid")
    if status == "rollback_required":
        if source.get("rollback_required") is not True or source.get("previous_bundle_restored") is not False:
            raise CutoverRejected("rollback state is invalid")
        if source.get("rollback_evidence_hash") is not None:
            raise CutoverRejected("rollback evidence is invalid")
    elif status == "rolled_back":
        if source.get("rollback_required") is not False or source.get("previous_bundle_restored") is not True:
            raise CutoverRejected("rollback receipt is incomplete")
        _digest(source.get("rollback_evidence_hash"), "rollback evidence")
    elif (
        source.get("previous_bundle_restored") is not False
        or source.get("rollback_evidence_hash") is not None
    ):
        raise CutoverRejected("rollback evidence is invalid")

    expected_paused = {name: True for name in PAUSED_PATH_NAMES}
    if status not in {"rollback_required", "rolled_back"}:
        for item in completed:
            resumed_path = RESUME_STEP_PATH.get(item["step"])
            if resumed_path is not None:
                expected_paused[resumed_path] = False
    if dict(paused) != expected_paused:
        raise CutoverRejected("paused path state is inconsistent with completed steps")
    return deepcopy(dict(source))


def _step_evidence(
    evidence: Mapping[str, Any], *, expected_step: str, now: datetime
) -> tuple[str, str, str]:
    if set(evidence) != {"step", "status", "checked_at", "evidence_hash"}:
        raise CutoverRejected("step evidence fields are invalid")
    if evidence.get("step") != expected_step:
        raise CutoverRejected("cutover step order is invalid")
    status = evidence.get("status")
    if status not in {"passed", "failed"}:
        raise CutoverRejected("step evidence status is invalid")
    checked = _fresh(
        evidence.get("checked_at"), label="step", now=now,
        maximum_age=EVIDENCE_MAX_AGE,
    )
    digest = _digest(evidence.get("evidence_hash"), "step")
    return status, _utc_text(checked), digest


def advance_cutover(
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    state = validate_state(source)
    if state["status"] in {"rollback_required", "rolled_back"}:
        raise CutoverRejected("rollback must be completed before any further action")
    if state["status"] == "owner_soak_complete" or state["next_step"] is None:
        raise CutoverRejected("owner soak is already complete")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    step = state["next_step"]
    status, checked_at, evidence_hash = _step_evidence(
        evidence, expected_step=step, now=observed
    )
    state["updated_at"] = _utc_text(observed)
    if status == "failed":
        state.update({
            "status": "rollback_required",
            "next_step": None,
            "rollback_required": True,
            "failed_step": step,
            "failure_evidence_hash": evidence_hash,
            "paused_paths": {name: True for name in PAUSED_PATH_NAMES},
        })
        return _seal(state)

    state["completed_steps"].append({
        "step": step,
        "checked_at": checked_at,
        "evidence_hash": evidence_hash,
    })
    resumed_path = RESUME_STEP_PATH.get(step)
    if resumed_path is not None:
        state["paused_paths"][resumed_path] = False
    state["paused_paths"]["friend_invitations"] = True
    index = len(state["completed_steps"])
    if index == len(CUTOVER_STEPS):
        state["status"] = "owner_soak_complete"
        state["next_step"] = None
    else:
        state["status"] = "in_progress"
        state["next_step"] = CUTOVER_STEPS[index]
    return _seal(state)


def record_rollback(
    source: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    state = validate_state(source)
    if state["status"] != "rollback_required":
        raise CutoverRejected("rollback was not requested")
    if set(evidence) != {"status", "checked_at", "evidence_hash"}:
        raise CutoverRejected("rollback evidence fields are invalid")
    if evidence.get("status") != "passed":
        raise CutoverRejected("rollback evidence did not pass")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _fresh(
        evidence.get("checked_at"), label="rollback", now=observed,
        maximum_age=EVIDENCE_MAX_AGE,
    )
    state.update({
        "updated_at": _utc_text(observed),
        "status": "rolled_back",
        "next_step": None,
        "rollback_required": False,
        "previous_bundle_restored": True,
        "rollback_evidence_hash": _digest(evidence.get("evidence_hash"), "rollback"),
        "paused_paths": {name: True for name in PAUSED_PATH_NAMES},
    })
    return _seal(state)


def finalize_receipt(
    source: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    state = validate_state(source)
    if state["status"] != "owner_soak_complete" or len(state["completed_steps"]) != len(CUTOVER_STEPS):
        raise CutoverRejected("owner soak is not complete")
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    receipt: dict[str, Any] = {
        "version": 1,
        "cutover_id": state["cutover_id"],
        "status": "owner_soak_complete",
        "candidate_commit": state["candidate_commit"],
        "rollback_commit": state["rollback_commit"],
        "completed_at": _utc_text(observed),
        "precondition_digest": state["precondition_digest"],
        "completion_digest": _hash(state["completed_steps"]),
        "private_data": False,
        "invitations_enabled": False,
    }
    receipt["evidence_digest"] = _hash(receipt)
    return receipt


def write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(encoded)


def _load(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise CutoverRejected("cutover input is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise CutoverRejected("cutover input must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CutoverRejected("cutover input permissions must be owner-only")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) & 0o077
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            os.close(descriptor)
            raise CutoverRejected("cutover input must remain a private regular file")
        with os.fdopen(descriptor, "rb") as source:
            raw = source.read(MAX_INPUT_BYTES + 1)
    except OSError as error:
        raise CutoverRejected("cutover input must be a regular file") from error
    if len(raw) > MAX_INPUT_BYTES:
        raise CutoverRejected("cutover input is too large")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CutoverRejected("cutover input is malformed") from error
    if not isinstance(value, dict):
        raise CutoverRejected("cutover input is malformed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--state", type=Path, required=True)
    advance = commands.add_parser("advance")
    advance.add_argument("--state", type=Path, required=True)
    advance.add_argument("--evidence", type=Path, required=True)
    advance.add_argument("--output", type=Path, required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--state", type=Path, required=True)
    rollback.add_argument("--evidence", type=Path, required=True)
    rollback.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--state", type=Path, required=True)
    finalize.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            value = prepare_cutover(_load(args.manifest))
            destination = args.state
        elif args.command == "advance":
            value = advance_cutover(_load(args.state), _load(args.evidence))
            destination = args.output
        elif args.command == "rollback":
            value = record_rollback(_load(args.state), _load(args.evidence))
            destination = args.output
        else:
            value = finalize_receipt(_load(args.state))
            destination = args.receipt
        write_private_json(destination, value)
        print(json.dumps({
            "status": value["status"],
            "evidence_digest": value.get("evidence_digest", value.get("state_digest")),
        }))
        return 0
    except (
        CutoverRejected, FileExistsError, OSError, UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        print("owner cutover rejected; all mutation paths must remain paused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
