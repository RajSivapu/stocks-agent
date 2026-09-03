from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from uuid import UUID

import pytest

from scripts import reset_owner_ledger


OWNER = UUID("11111111-1111-4111-8111-111111111111")
RECEIPT = UUID("22222222-2222-4222-8222-222222222222")


class FakeCursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakeConnection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=()):
        self.calls.append((query, params))
        if "operator_preview_ledger_reset" in query:
            return FakeCursor({"holdings": 1, "transactions": 2, "commands": 1, "plans": 1, "dry_powder": 1})
        if "export_owner_account" in query:
            return FakeCursor({"format": "json", "body": '{"portfolio":{"holdings":[]}}\n'})
        if "operator_apply_ledger_reset" in query:
            return FakeCursor({"status": "reset", "reset_receipt_id": "33333333-3333-4333-8333-333333333333"})
        if "count(*) FROM app.holdings" in query:
            return FakeCursor(0)
        raise AssertionError(query)


def test_reset_requires_exact_owner_bound_confirmation():
    expected = f"RESET {OWNER}"
    assert reset_owner_ledger.validate_confirmation(OWNER, expected) == expected
    for value in ("reset " + str(OWNER), "RESET", f"RESET {RECEIPT}"):
        with pytest.raises(ValueError, match="confirmation"):
            reset_owner_ledger.validate_confirmation(OWNER, value)


def test_reset_encrypts_before_mutation_and_records_ciphertext_digest(tmp_path):
    connection = FakeConnection()
    output = tmp_path / "ledger-export.json.age"
    events = []

    def encryptor(plaintext: bytes, destination: Path, recipient: str):
        events.append("encrypted")
        assert b"holdings" in plaintext
        destination.write_bytes(b"age-encrypted-fixture")
        destination.chmod(0o600)

    result = reset_owner_ledger.reset_connection(
        connection,
        owner_id=OWNER,
        step_up_receipt_id=RECEIPT,
        output=output,
        age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs",
        confirmation=f"RESET {OWNER}",
        encryptor=encryptor,
    )

    assert events == ["encrypted"]
    assert output.read_bytes() == b"age-encrypted-fixture"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    apply = next((params for query, params in connection.calls if "operator_apply_ledger_reset" in query), None)
    assert apply is not None
    assert apply[2] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert result["status"] == "reset"
    assert not list(tmp_path.glob("*.json"))


def test_reset_fails_closed_if_encryption_does_not_create_ciphertext(tmp_path):
    connection = FakeConnection()
    with pytest.raises(RuntimeError, match="encrypted export"):
        reset_owner_ledger.reset_connection(
            connection,
            owner_id=OWNER,
            step_up_receipt_id=RECEIPT,
            output=tmp_path / "missing.age",
            age_recipient="age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqs",
            confirmation=f"RESET {OWNER}",
            encryptor=lambda *_args: None,
        )
    assert not any("operator_apply_ledger_reset" in query for query, _ in connection.calls)
