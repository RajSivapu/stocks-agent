from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ops.backup.restore_backup import (
    RestoreRejected,
    assert_staging_restore,
    build_identity_remap,
    harden_restored_rows,
    remap_archive_owners,
)
from ops.backup.verify_archive import DATASET_SPECS, build_archive


OWNER = "11111111-1111-4111-8111-111111111111"
NEW_OWNER = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def _archive():
    datasets = {name: [] for name in DATASET_SPECS}
    datasets["profiles"] = [{
        "id": OWNER,
        "display_name": "Owner",
        "timezone": "America/Chicago",
        "status": "active",
        "onboarding_completed_at": None,
        "created_at": "2026-09-01T00:00:00+00:00",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }]
    return build_archive(
        datasets=datasets,
        identity_rows=[{"owner_id": OWNER, "email": "owner@example.com"}],
        exported_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
    )


def test_restore_requires_staging_paused_triggers_and_exact_confirmation():
    assert_staging_restore(
        environment="staging",
        production_triggers_paused=True,
        project_ref="stock-agent-staging",
        confirmation="RESTORE STAGING stock-agent-staging",
    )
    for values in (
        ("production", True, "stock-agent-staging", "RESTORE STAGING stock-agent-staging"),
        ("staging", False, "stock-agent-staging", "RESTORE STAGING stock-agent-staging"),
        ("staging", True, "stock-agent-staging", "wrong"),
        ("staging", True, "stock-agent-prod", "RESTORE STAGING stock-agent-prod"),
    ):
        with pytest.raises(RestoreRejected):
            assert_staging_restore(
                environment=values[0],
                production_triggers_paused=values[1],
                project_ref=values[2],
                confirmation=values[3],
            )


def test_identity_recreation_builds_complete_old_to_new_uuid_map():
    calls = []

    def creator(email):
        calls.append(email)
        return NEW_OWNER

    mapping = build_identity_remap(_archive(), creator)
    assert mapping == {OWNER: NEW_OWNER}
    assert calls == ["owner@example.com"]


def test_owner_ids_are_remapped_without_rewriting_tombstones():
    archive = _archive()
    remapped = remap_archive_owners(archive, {OWNER: NEW_OWNER})
    assert remapped["datasets"]["profiles"]["rows"][0]["id"] == NEW_OWNER
    assert remapped["identity_recovery"][0]["owner_id"] == NEW_OWNER


def test_restore_hardening_disables_side_effect_capable_state():
    datasets = {name: [] for name in DATASET_SPECS}
    datasets["agent_connections"] = [{"status": "active", "last_handshake_at": "now"}]
    datasets["portfolio_commands"] = [{"status": "submitted"}]
    datasets["market_publications"] = [{"status": "ready"}]
    datasets["operational_alerts"] = [{"status": "sending"}]
    hardened = harden_restored_rows(datasets)
    assert hardened["agent_connections"][0]["status"] == "disabled"
    assert hardened["agent_connections"][0]["last_handshake_at"] is None
    assert hardened["portfolio_commands"][0]["status"] == "cancelled"
    assert hardened["market_publications"][0]["status"] == "suppressed"
    assert hardened["operational_alerts"][0]["status"] == "suppressed"
