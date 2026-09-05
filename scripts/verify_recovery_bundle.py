#!/usr/bin/env python3
"""Query a guarded isolated restore and reconcile the encrypted production snapshot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.export_recovery_bundle import (
    RecoveryDataSource, _validated_records, decrypt_verified, exact_file, sha256, source_identity,
)


def verify_recovery_bundle(artifact: Path, restored_source: RecoveryDataSource, *, production_source: RecoveryDataSource,
                           decrypt_command: str | None = None) -> dict[str, object]:
    if not decrypt_command:
        raise RuntimeError("decrypt verification command is required")
    production = source_identity(production_source)
    isolated = source_identity(restored_source)
    if (isolated.get("isolated_guard") is not True or isolated["project_ref"] == production["project_ref"]
            or isolated["connection_id"] == production["connection_id"]):
        raise RuntimeError("explicitly guarded isolated restored database identity is required")
    exact_file(artifact)
    receipt_path = artifact.with_suffix(artifact.suffix + ".receipt.json")
    with tempfile.TemporaryDirectory(prefix="stocks-recovery-verify-") as temporary:
        try:
            sidecar = json.loads(exact_file(receipt_path).read_text())
            if not isinstance(sidecar, dict) or set(sidecar) != {"format", "root_hash", "artifact_sha256"} or sidecar.get("artifact_sha256") != sha256(artifact.read_bytes()):
                raise RuntimeError("encrypted recovery artifact hash mismatch")
            manifest, exported = decrypt_verified(artifact, decrypt_command, Path(temporary).resolve())
            if manifest["root_hash"] != sidecar["root_hash"] or manifest["format"] != sidecar["format"]:
                raise RuntimeError("decrypted recovery root hash mismatch")
        except (RuntimeError, ValueError):
            artifact.unlink(missing_ok=True)
            if not receipt_path.is_symlink():
                receipt_path.unlink(missing_ok=True)
            raise
        if manifest["production_identity"] != production:
            raise RuntimeError("recovery production identity mismatch")
        restored = _validated_records(restored_source.read_records())
        if restored != exported or restored_source.counts() != manifest["counts"] or production_source.counts() != manifest["counts"]:
            raise RuntimeError("isolated restored records or production counts do not match the recovery snapshot")
    return {"status": "verified", "isolated": True, "record_set_count": len(exported), "root_hash": manifest["root_hash"]}


def main() -> int:
    from scripts.protected_evidence import PostgresReadOnlySource
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--production-project-ref", required=True)
    parser.add_argument("--restore-project-ref", required=True)
    parser.add_argument("--allow-isolated-restore", action="store_true", required=True)
    parser.add_argument("--decrypt-command", required=True)
    args = parser.parse_args()
    if args.production_project_ref == args.restore_project_ref:
        raise RuntimeError("restore project must differ from production")
    with PostgresReadOnlySource(os.environ.get("RECOVERY_PRODUCTION_DATABASE_URL", ""), args.production_project_ref) as production:
        with PostgresReadOnlySource(os.environ.get("RECOVERY_RESTORE_DATABASE_URL", ""), args.restore_project_ref,
                                   isolated_guard=args.allow_isolated_restore, production_project_ref=args.production_project_ref) as restored:
            print(json.dumps(verify_recovery_bundle(args.artifact, restored, production_source=production, decrypt_command=args.decrypt_command), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
