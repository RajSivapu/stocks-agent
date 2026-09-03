from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from ops.backup.verify_archive import (
    DATASET_SPECS,
    ArchiveValidationError,
    build_archive,
    canonical_json_bytes,
    verify_archive,
)


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
OWNER = "11111111-1111-4111-8111-111111111111"


def _datasets():
    return {name: [] for name in DATASET_SPECS}


def _archive():
    datasets = _datasets()
    datasets["profiles"] = [
        {
            "id": OWNER,
            "display_name": "Synthetic owner",
            "timezone": "America/Chicago",
            "status": "active",
            "onboarding_completed_at": None,
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }
    ]
    return build_archive(
        datasets=datasets,
        identity_rows=[{"owner_id": OWNER, "email": "owner@example.com"}],
        exported_at=NOW,
    )


def test_archive_is_deterministic_and_self_verifying():
    first = _archive()
    second = _archive()
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert verify_archive(first) == first


def test_archive_rejects_missing_dataset_and_schema_version_mismatch():
    missing = _archive()
    del missing["datasets"][next(iter(DATASET_SPECS))]
    with pytest.raises(ArchiveValidationError, match="dataset set"):
        verify_archive(missing)

    wrong_version = _archive()
    wrong_version["schema_version"] = 999
    with pytest.raises(ArchiveValidationError, match="schema version"):
        verify_archive(wrong_version)


def test_archive_rejects_row_and_relationship_digest_tampering():
    changed_row = _archive()
    changed_row["datasets"]["profiles"]["rows"][0]["display_name"] = "Changed"
    with pytest.raises(ArchiveValidationError, match="row digest"):
        verify_archive(changed_row)

    changed_relationship = _archive()
    changed_relationship["relationship_digest"] = "0" * 64
    with pytest.raises(ArchiveValidationError, match="relationship digest"):
        verify_archive(changed_relationship)


def test_archive_rejects_plaintext_credentials_even_under_unknown_keys():
    archive = _archive()
    archive["datasets"]["profiles"]["rows"][0]["api_token"] = "sk-live-plaintext"
    with pytest.raises(ArchiveValidationError, match="unexpected columns|credential"):
        verify_archive(archive)


def test_archive_rejects_deleted_owner_resurrection():
    datasets = _datasets()
    datasets["profiles"] = [
        {
            "id": OWNER,
            "display_name": None,
            "timezone": "America/Chicago",
            "status": "active",
            "onboarding_completed_at": None,
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }
    ]
    datasets["deletion_tombstones"] = [
        {
            "owner_id": OWNER,
            "deletion_request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "deleted_at": "2026-09-02T00:00:00+00:00",
            "archives_expire_after": "2026-10-07T00:00:00+00:00",
        }
    ]
    with pytest.raises(ArchiveValidationError, match="deleted owner"):
        build_archive(
            datasets=datasets,
            identity_rows=[{"owner_id": OWNER, "email": "owner@example.com"}],
            exported_at=NOW,
        )


def test_archive_verification_does_not_mutate_input():
    archive = _archive()
    before = copy.deepcopy(archive)
    verify_archive(archive)
    assert archive == before
