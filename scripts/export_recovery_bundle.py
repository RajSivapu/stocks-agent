#!/usr/bin/env python3
"""Create a deterministic local recovery bundle without transmitting portfolio data."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


REQUIRED_RECOVERY_RECORDS = (
    "holdings", "transactions", "commands", "runs", "packets", "reports",
    "publications", "roles", "schema_version",
)
_FORBIDDEN = frozenset({
    "secret", "password", "token", "access_token", "refresh_token", "service_key",
    "database_url", "telegram_token", "private_key", "api_key",
})


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _contains_secret(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in _FORBIDDEN
            or any(marker in str(key).lower() for marker in ("secret", "password", "token", "api_key", "private_key"))
            or _contains_secret(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return False


def _validated_records(records: Mapping[str, object]) -> dict[str, list[Mapping[str, object]]]:
    if set(records) != set(REQUIRED_RECOVERY_RECORDS):
        raise ValueError("recovery records must contain the exact required record sets")
    normalized: dict[str, list[Mapping[str, object]]] = {}
    for name in REQUIRED_RECOVERY_RECORDS:
        rows = records[name]
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise ValueError(f"recovery record set {name} must be a list of objects")
        if _contains_secret(rows):
            raise ValueError("recovery records must not contain secret values")
        normalized[name] = sorted((dict(row) for row in rows), key=canonical_json)
    return normalized


def _run_encrypt(command_template: str, bundle: Path) -> Path:
    if "{input}" not in command_template or "{output}" not in command_template:
        raise ValueError("encrypt command must contain {input} and {output} placeholders")
    output = bundle.with_suffix(".encrypted")
    command = [part.format(input=str(bundle), output=str(output)) for part in shlex.split(command_template)]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("caller-supplied local encryption command failed")
    return output


def export_recovery_bundle(
    records: Mapping[str, object], destination: Path, *, encrypt_command: str | None = None,
) -> Path:
    """Write canonical NDJSON and a redacted hash manifest to a local directory.

    Encryption is deliberately delegated to an explicit local command supplied by the caller;
    this tool never contacts cloud storage or adds a paid backup dependency.
    """
    normalized = _validated_records(records)
    if destination.exists():
        raise ValueError("recovery bundle destination must not already exist")
    data_directory = destination / "data"
    data_directory.mkdir(parents=True)
    files: dict[str, dict[str, object]] = {}
    for name, rows in normalized.items():
        payload = "".join(f"{canonical_json(row)}\n" for row in rows)
        relative = f"data/{name}.ndjson"
        (destination / relative).write_text(payload, encoding="utf-8")
        files[name] = {
            "path": relative,
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "records": len(rows),
        }
    manifest = {
        "format": "stocks-agent-recovery-v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "files": files,
        "record_sets": list(REQUIRED_RECOVERY_RECORDS),
        "secrets_included": False,
        "portfolio_values_in_manifest": False,
    }
    (destination / "manifest.json").write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    if encrypt_command:
        _run_encrypt(encrypt_command, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records-json", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--encrypt-command")
    args = parser.parse_args()
    try:
        records = json.loads(args.records_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("recovery records are unavailable or malformed") from error
    if not isinstance(records, Mapping):
        raise SystemExit("recovery records must be a JSON object")
    export_recovery_bundle(records, args.destination, encrypt_command=args.encrypt_command)
    print(json.dumps({"status": "exported", "destination": str(args.destination)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
