import json

import pytest

from scripts.export_recovery_bundle import REQUIRED_RECOVERY_RECORDS, export_recovery_bundle
from scripts.verify_recovery_bundle import verify_recovery_bundle


def recovery_records():
    return {name: [{"id": f"{name}-1", "safe": True}] for name in REQUIRED_RECOVERY_RECORDS}


def test_recovery_export_is_canonical_complete_and_manifest_redacted(tmp_path):
    records = recovery_records()
    records["holdings"] = [{"ticker": "VTI", "shares": "2", "average_cost": "100"}]
    bundle = export_recovery_bundle(records, tmp_path / "bundle")
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert set(manifest["files"]) == set(REQUIRED_RECOVERY_RECORDS)
    assert "VTI" not in json.dumps(manifest)
    assert "password" not in json.dumps(manifest).lower()
    assert verify_recovery_bundle(bundle, records)["status"] == "verified"


def test_recovery_export_rejects_secrets_and_verifier_detects_non_isolated_or_tampered_restore(tmp_path):
    unsafe = recovery_records()
    unsafe["roles"] = [{"name": "dashboard", "password": "secret"}]
    with pytest.raises(ValueError, match="secret"):
        export_recovery_bundle(unsafe, tmp_path / "unsafe")

    records = recovery_records()
    bundle = export_recovery_bundle(records, tmp_path / "bundle")
    changed = recovery_records()
    changed["reports"] = [{"id": "reports-1", "safe": False}]
    with pytest.raises(RuntimeError, match="restored"):
        verify_recovery_bundle(bundle, changed)
