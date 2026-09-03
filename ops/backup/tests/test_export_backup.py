from __future__ import annotations

import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops.backup import export_backup
from ops.backup.verify_archive import DATASET_SPECS


class FakeConnection:
    def execute(self, query, parameters):
        request = json.loads(parameters[0])
        if "backup_export_catalog" in query:
            value = {
                "schema_version": 1,
                "tables": [
                    {"name": name, "disposition": "include"}
                    for name in DATASET_SPECS
                ],
            }
        else:
            dataset = request["dataset"]
            value = {
                "dataset": dataset,
                "columns": ["email", "owner_id"] if dataset == "identity_recovery" else [],
                "rows": [],
            }

        class Cursor:
            def fetchone(self):
                return (value,)

        return Cursor()


def test_collect_archive_calls_only_enumerated_rpcs():
    archive = export_backup.collect_archive(
        FakeConnection(),
        exported_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert archive["schema_version"] == 1
    assert set(archive["datasets"]) == set(DATASET_SPECS)


def test_backup_success_receipt_contains_only_ciphertext_metadata():
    observed = {}

    class ReceiptConnection:
        def execute(self, query, parameters):
            observed["query"] = query
            observed["request"] = json.loads(parameters[0])

            class Cursor:
                def fetchone(self):
                    return ({"status": "recorded", "last_success_at": "2026-09-03T12:00:00Z"},)

            return Cursor()

    export_backup.record_backup_success(
        ReceiptConnection(),
        exported_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        metadata={"bytes": 123, "ciphertext_sha256": "a" * 64},
    )
    assert "backup_record_success" in observed["query"]
    assert observed["request"] == {
        "schema_version": 1,
        "exported_at": "2026-09-03T12:00:00Z",
        "ciphertext_bytes": 123,
        "ciphertext_digest": "a" * 64,
    }


def test_encrypt_payload_uses_private_temp_dir_and_removes_plaintext(tmp_path):
    observed = {}

    def runner(command, **options):
        source = Path(command[-1])
        destination = Path(command[command.index("--output") + 1])
        observed["dir_mode"] = stat.S_IMODE(source.parent.stat().st_mode)
        observed["file_mode"] = stat.S_IMODE(source.stat().st_mode)
        observed["plaintext_path"] = source
        observed["command"] = command
        destination.write_bytes(b"age-encryption.org/v1\nsynthetic ciphertext")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    destination = tmp_path / "backup.age"
    metadata = export_backup.encrypt_payload(
        b'{"private":"portfolio"}',
        recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqd3f3",
        destination=destination,
        runner=runner,
    )
    assert observed["dir_mode"] == 0o700
    assert observed["file_mode"] == 0o600
    assert "portfolio" not in destination.read_text()
    assert not observed["plaintext_path"].exists()
    assert metadata["ciphertext_sha256"]
    assert metadata["bytes"] == destination.stat().st_size


def test_encrypt_failure_still_removes_plaintext(tmp_path):
    observed = {}

    def runner(command, **_options):
        observed["plaintext_path"] = Path(command[-1])

        class Result:
            returncode = 1
            stderr = "sensitive provider error"

        return Result()

    with pytest.raises(RuntimeError, match="age encryption failed"):
        export_backup.encrypt_payload(
            b"private",
            recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqd3f3",
            destination=tmp_path / "backup.age",
            runner=runner,
        )
    assert not observed["plaintext_path"].exists()


def test_key_material_is_exact_bounded_and_explicitly_marks_rotation():
    raw = json.dumps({name: "x" * 40 for name in export_backup.KEY_MATERIAL_NAMES})
    value = export_backup.validated_key_material(
        raw,
        generated_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert value["version"] == 1
    assert set(value["continuity_values"]) == export_backup.KEY_MATERIAL_NAMES
    assert "TELEGRAM_BOT_TOKEN" in value["rotate_after_restore"]

    missing = json.loads(raw)
    missing.pop("APP_STEP_UP_PEPPER")
    with pytest.raises(export_backup.BackupFailed, match="set does not match"):
        export_backup.validated_key_material(
            json.dumps(missing),
            generated_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
        )
