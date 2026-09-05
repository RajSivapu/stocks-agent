#!/usr/bin/env python3
"""Verify a recovery bundle against records restored in an isolated database."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from scripts.export_recovery_bundle import REQUIRED_RECOVERY_RECORDS, _validated_records, canonical_json


def _load_manifest(bundle: Path) -> Mapping[str, object]:
    try:
        value = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("recovery manifest is unavailable or malformed") from error
    if not isinstance(value, Mapping) or value.get("format") != "stocks-agent-recovery-v1":
        raise RuntimeError("recovery manifest is unavailable or malformed")
    return value


def verify_recovery_bundle(
    bundle: Path, isolated_restored_records: Mapping[str, object], *, isolated: bool = True,
) -> dict[str, object]:
    """Check bundle bytes and canonical restored rows; live databases are never accepted."""
    if not isolated:
        raise RuntimeError("recovery verification requires an isolated restored database")
    manifest = _load_manifest(bundle)
    files = manifest.get("files")
    if (manifest.get("record_sets") != list(REQUIRED_RECOVERY_RECORDS)
            or manifest.get("secrets_included") is not False
            or manifest.get("portfolio_values_in_manifest") is not False
            or not isinstance(files, Mapping)
            or set(files) != set(REQUIRED_RECOVERY_RECORDS)):
        raise RuntimeError("recovery manifest is incomplete or unsafe")
    restored = _validated_records(isolated_restored_records)
    for name in REQUIRED_RECOVERY_RECORDS:
        entry = files[name]
        if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
            raise RuntimeError("recovery manifest is incomplete or unsafe")
        path = bundle / entry["path"]
        try:
            data = path.read_bytes()
        except OSError as error:
            raise RuntimeError("recovery bundle data is unavailable") from error
        expected = entry.get("sha256")
        if not isinstance(expected, str) or hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError("recovery bundle file hash mismatch")
        canonical_restored = "".join(f"{canonical_json(row)}\n" for row in restored[name]).encode()
        if data != canonical_restored or entry.get("records") != len(restored[name]):
            raise RuntimeError("isolated restored records do not match the recovery bundle")
    return {"status": "verified", "record_set_count": len(REQUIRED_RECOVERY_RECORDS), "isolated": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--isolated-restored-records-json", type=Path, required=True)
    args = parser.parse_args()
    try:
        restored = json.loads(args.isolated_restored_records_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("isolated restored records are unavailable or malformed") from error
    if not isinstance(restored, Mapping):
        raise SystemExit("isolated restored records must be a JSON object")
    print(json.dumps(verify_recovery_bundle(args.bundle, restored), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
