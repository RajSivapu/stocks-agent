#!/usr/bin/env python3
"""Export a read-only database snapshot through authenticated local encryption."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from typing import Mapping, Protocol, runtime_checkable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REQUIRED_RECOVERY_RECORDS = ("holdings", "transactions", "commands", "runs", "packets", "reports", "publications", "roles", "schema_version")
NULLABLE_TEXT = (str, type(None))
DATASET_FIELDS = {
    "holdings": {"ticker": str, "shares": str, "average_cost": str,
                 **{key: NULLABLE_TEXT for key in ("bucket", "opened_at", "notes", "stop", "target", "high_water_price", "hold_override_until")},
                 **{key: (bool, type(None)) for key in ("stop_alert_active", "stop_near_alert_active", "target_near_alert_active", "target_alert_active")}},
    "transactions": {"id": str, "ticker": str, "quantity": str, "price": str, "ts": str, "side": str, "source": NULLABLE_TEXT, "executed_on": NULLABLE_TEXT},
    "commands": {**{key: str for key in ("id", "status", "telegram_update_id", "chat_id", "user_id", "operation", "ticker", "expected_shares", "expires_at", "created_at", "updated_at")},
                 **{key: NULLABLE_TEXT for key in ("qty", "price", "executed_on", "bucket", "stop", "confirmation_message_id", "applied_at", "realized_pnl", "error")},
                 "preview": dict, "result": (dict, type(None))},
    "runs": {"id": str, "status": str, "phase": str, "started_at": str, "finished_at": NULLABLE_TEXT, "scheduled_phase": NULLABLE_TEXT,
             "scheduled_market_date": NULLABLE_TEXT, "gateway_request_id": NULLABLE_TEXT, "telegram_message_ids": list},
    "packets": {"id": str, "run_id": str, "packet_hash": str, "packet": dict},
    "reports": {"id": str, "run_id": str, "packet_id": str, "report_hash": str, "rendered_hash": str, "report": dict, "rendered_text": str},
    "publications": {"report_id": str, "idempotency_key": str, "status": str, "telegram_message_ids": list,
                     "telegram_accepted_at": (str, type(None)), "suppression_reason": (str, type(None))},
    "roles": {"role": str, "login": bool, "superuser": bool, "bypass_rls": bool, "memberships": list, "grants": list},
    "schema_version": {"version": str, "sha256": str},
}
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@runtime_checkable
class RecoveryDataSource(Protocol):
    def identity(self) -> Mapping[str, object]: ...
    def read_records(self) -> Mapping[str, object]: ...
    def counts(self) -> Mapping[str, int]: ...


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_identity(source: RecoveryDataSource) -> dict[str, object]:
    if isinstance(source, Mapping) or not isinstance(source, RecoveryDataSource):
        raise RuntimeError("a queried read-only database source is required")
    identity = dict(source.identity())
    if (not re.fullmatch(r"[a-z0-9]{20}", str(identity.get("project_ref", "")))
            or not isinstance(identity.get("connection_id"), str) or not identity["connection_id"].strip()
            or identity.get("read_only") is not True):
        raise RuntimeError("database source identity is unavailable or unsafe")
    return identity


def _no_secrets(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {"password", "secret", "token", "database_url", "access_token", "service_key", "telegram_token", "rolpassword"}:
                raise ValueError("recovery data contains a forbidden secret field")
            _no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _no_secrets(child)


def _validated_records(records: Mapping[str, object]) -> dict[str, list[dict[str, object]]]:
    if not isinstance(records, Mapping) or set(records) != set(REQUIRED_RECOVERY_RECORDS):
        raise ValueError("recovery records require exact allowlisted datasets")
    _no_secrets(records)
    result = {}
    for name, fields in DATASET_FIELDS.items():
        rows = records[name]
        if not isinstance(rows, list) or (name in {"runs", "packets", "reports", "roles", "schema_version"} and not rows):
            raise ValueError(f"recovery dataset {name} requires meaningful rows")
        clean = []
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != set(fields) or any(not isinstance(row[key], kind) for key, kind in fields.items()):
                raise ValueError(f"recovery dataset {name} has unknown or invalid fields")
            clean.append(dict(row))
        identity = {"holdings": "ticker", "publications": "report_id", "roles": "role", "schema_version": "version"}.get(name, "id")
        if any(not row[identity] for row in clean) or len({row[identity] for row in clean}) != len(clean):
            raise ValueError(f"recovery dataset {name} has duplicate or missing identities")
        result[name] = sorted(clean, key=canonical_json)
    runs = {row["id"] for row in result["runs"]}
    packets = {row["id"]: row for row in result["packets"]}
    reports = {row["id"]: row for row in result["reports"]}
    for name in ("commands", "runs", "packets", "reports"):
        if any(not UUID.fullmatch(row["id"]) for row in result[name]):
            raise ValueError(f"recovery dataset {name} has malformed UUIDs")
    for row in packets.values():
        if row["run_id"] not in runs or sha256(canonical_json(row["packet"]).encode()) != row["packet_hash"]:
            raise ValueError("packet content or run relationship mismatch")
    for row in reports.values():
        packet = packets.get(row["packet_id"])
        if (packet is None or row["run_id"] not in runs or packet["run_id"] != row["run_id"]
                or sha256(canonical_json(row["report"]).encode()) != row["report_hash"]
                or sha256(row["rendered_text"].encode()) != row["rendered_hash"]
                or ("packet_hash" in row["report"] and row["report"]["packet_hash"] != packet["packet_hash"])):
            raise ValueError("report content or packet/run relationship mismatch")
    for row in result["publications"]:
        if row["report_id"] not in reports or not HASH.fullmatch(row["idempotency_key"]):
            raise ValueError("publication report relationship mismatch")
        status, ids, accepted, reason = (row[k] for k in ("status", "telegram_message_ids", "telegram_accepted_at", "suppression_reason"))
        if status not in {"pending", "delivered", "failed", "uncertain", "suppressed"}:
            raise ValueError("invalid publication state")
        if status == "delivered":
            if not ids or any(type(value) is not int or value <= 0 for value in ids) or not accepted or reason is not None:
                raise ValueError("original Telegram delivery receipt is incomplete")
        elif ids != [] or accepted is not None or (status == "suppressed" and (not reason or not reason.strip())) or (status != "suppressed" and reason is not None):
            raise ValueError("publication suppression receipt is incomplete")
    if {row["report_id"] for row in result["publications"]} != set(reports):
        raise ValueError("recovery reports require exactly one publication receipt each")
    if any(not HASH.fullmatch(row["sha256"]) for row in result["schema_version"]):
        raise ValueError("schema version hash is invalid")
    return result


def relationships(records: Mapping[str, list]) -> dict[str, list]:
    return {
        "packet_run": sorted([[row["id"], row["run_id"]] for row in records["packets"]]),
        "report_packet_run": sorted([[row["id"], row["packet_id"], row["run_id"]] for row in records["reports"]]),
        "publication_report": sorted([[row["idempotency_key"], row["report_id"]] for row in records["publications"]]),
    }


def _command(template: str, input_path: Path, output_path: Path, *, allow_failure: bool = False) -> bool:
    if not template or "{input}" not in template or "{output}" not in template:
        raise ValueError("encryption and decryption commands require {input} and {output}")
    try:
        args = [part.replace("{input}", str(input_path)).replace("{output}", str(output_path)) for part in shlex.split(template)]
        result = subprocess.run(args, check=False, capture_output=True, timeout=60)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("local encryption/decryption command failed") from error
    if result.returncode and not allow_failure:
        raise RuntimeError("local encryption/decryption command failed")
    return result.returncode == 0


def exact_file(path: Path, *, exists: bool = True) -> Path:
    if not path.is_absolute() or ".." in path.parts or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("recovery paths must be exact absolute paths without symlinks or traversal")
    if exists and (not path.is_file() or path.stat().st_size > MAX_PAYLOAD_BYTES):
        raise RuntimeError("recovery artifact is unavailable or exceeds the size limit")
    return path


def reject_plaintext(path: Path) -> None:
    exact_file(path)
    raw = path.read_bytes()
    if not raw or tarfile.is_tarfile(path):
        raise RuntimeError("encryption output is readable as plaintext/archive")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    raise RuntimeError("encryption output is readable as plaintext")


def read_payload(path: Path) -> tuple[dict, dict]:
    expected = {"payload/manifest.json", *(f"payload/data/{name}.ndjson" for name in REQUIRED_RECOVERY_RECORDS)}
    try:
        exact_file(path)
        with tarfile.open(path) as archive:
            members = archive.getmembers()
            if len(members) != len(expected) or {member.name for member in members} != expected or any(not member.isfile() for member in members):
                raise ValueError("unsafe archive paths or members")
            if sum(member.size for member in members) > MAX_PAYLOAD_BYTES:
                raise ValueError("oversized archive")
            # Never extract archive paths to disk.
            files = {member.name: archive.extractfile(member).read() for member in members}
        manifest = json.loads(files["payload/manifest.json"])
        core = {key: value for key, value in manifest.items() if key != "root_hash"}
        if (set(core) != {"format", "record_sets", "files", "production_identity", "counts", "relationships", "secrets_included"}
                or core["format"] != "stocks-agent-recovery-v3" or core["record_sets"] != list(REQUIRED_RECOVERY_RECORDS)
                or core["secrets_included"] is not False or manifest["root_hash"] != sha256(canonical_json(core).encode())
                or set(core["files"]) != set(REQUIRED_RECOVERY_RECORDS)):
            raise ValueError("manifest root hash or fields invalid")
        records = {}
        for name in REQUIRED_RECOVERY_RECORDS:
            entry = core["files"][name]
            raw = files[f"payload/data/{name}.ndjson"]
            if set(entry) != {"path", "sha256", "records"} or entry["path"] != f"data/{name}.ndjson" or sha256(raw) != entry["sha256"]:
                raise ValueError("manifest file hash or path invalid")
            records[name] = [json.loads(line) for line in raw.splitlines()]
        normalized = _validated_records(records)
        counts = {name: len(rows) for name, rows in normalized.items()}
        if core["counts"] != counts or core["relationships"] != relationships(normalized):
            raise ValueError("manifest counts or relationships invalid")
        for name, rows in normalized.items():
            raw = "".join(canonical_json(row) + "\n" for row in rows).encode()
            if files[f"payload/data/{name}.ndjson"] != raw or core["files"][name]["records"] != len(rows):
                raise ValueError("noncanonical recovery dataset")
        return manifest, normalized
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError) as error:
        raise RuntimeError("encrypted recovery payload or root hash is invalid") from error


def decrypt_verified(artifact: Path, command: str, temporary: Path) -> tuple[dict, dict]:
    reject_plaintext(artifact)
    plain = temporary / "verified.tar"
    _command(command, artifact, plain)
    payload = read_payload(plain)
    altered = bytearray(artifact.read_bytes())
    altered[len(altered) // 2] ^= 1
    tampered = temporary / "tampered.enc"
    tampered.write_bytes(altered)
    if _command(command, tampered, temporary / "tampered.tar", allow_failure=True):
        raise RuntimeError("authenticated decryption self-test accepted modified ciphertext")
    return payload


def export_recovery_bundle(source: RecoveryDataSource, destination: Path, *, encrypt_command: str | None = None, decrypt_command: str | None = None) -> Path:
    if not encrypt_command or not decrypt_command:
        raise ValueError("encryption and decryption commands are required")
    identity = source_identity(source)
    destination = exact_file(destination, exists=False)
    receipt_path = destination.with_suffix(destination.suffix + ".receipt.json")
    if destination.exists() or receipt_path.exists():
        raise ValueError("recovery artifact and receipt destinations must not already exist")
    normalized = _validated_records(source.read_records())
    counts = {name: len(rows) for name, rows in normalized.items()}
    if source.counts() != counts:
        raise RuntimeError("production counts do not match the complete recovery snapshot")
    try:
        with tempfile.TemporaryDirectory(prefix="stocks-recovery-") as temporary:
            directory = Path(temporary).resolve()
            files = {name: "".join(canonical_json(row) + "\n" for row in rows).encode() for name, rows in normalized.items()}
            if sum(map(len, files.values())) > MAX_PAYLOAD_BYTES - 1024 * 1024:
                raise RuntimeError("complete recovery snapshot exceeds the payload limit")
            core = {"format": "stocks-agent-recovery-v3", "record_sets": list(REQUIRED_RECOVERY_RECORDS),
                    "files": {name: {"path": f"data/{name}.ndjson", "sha256": sha256(raw), "records": counts[name]} for name, raw in files.items()},
                    "production_identity": identity, "counts": counts, "relationships": relationships(normalized), "secrets_included": False}
            manifest = {**core, "root_hash": sha256(canonical_json(core).encode())}
            archive = directory / "payload.tar"
            with tarfile.open(archive, "w") as tar:
                members = {"payload/manifest.json": canonical_json(manifest).encode(), **{f"payload/data/{name}.ndjson": raw for name, raw in files.items()}}
                for name, raw in sorted(members.items()):
                    info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o600
                    tar.addfile(info, io.BytesIO(raw))
            _command(encrypt_command, archive, destination)
            verified, _ = decrypt_verified(destination, decrypt_command, directory)
            if verified["root_hash"] != manifest["root_hash"]:
                raise RuntimeError("encrypted recovery root hash differs from the exported snapshot")
            os.chmod(destination, 0o600)
            receipt_path.write_text(canonical_json({"format": core["format"], "root_hash": manifest["root_hash"], "artifact_sha256": sha256(destination.read_bytes())}) + "\n")
            os.chmod(receipt_path, 0o600)
        return destination
    except BaseException:
        # Exact newly created destinations only; a failed export must never leave financial plaintext behind.
        destination.unlink(missing_ok=True)
        receipt_path.unlink(missing_ok=True)
        raise


def main() -> int:
    from scripts.protected_evidence import PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--encrypt-command", required=True)
    parser.add_argument("--decrypt-command", required=True)
    args = parser.parse_args()
    with PostgresReadOnlySource(os.environ.get("RECOVERY_PRODUCTION_DATABASE_URL", ""), args.production_project_ref) as source:
        export_recovery_bundle(source, args.destination, encrypt_command=args.encrypt_command, decrypt_command=args.decrypt_command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
