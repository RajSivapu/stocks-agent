from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from scripts import deploy_and_verify as deploy


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.body))}
        self.stream = BytesIO(self.body)

    def read(self, amount):
        return self.stream.read(amount)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class RawResponse(Response):
    def __init__(self, payload: bytes):
        self.body = payload
        self.headers = {"Content-Length": str(len(self.body))}
        self.stream = BytesIO(self.body)


def health(*, backup_hours: int = 1, restore_days: int = 1) -> dict:
    return {
        "status": "ok",
        "component_status": {
            "database": "ok", "scheduler": "ok", "provider_adapter": "ok",
            "backup": "ok", "restore": "ok", "projections": "ok",
        },
        "deployed_versions": {"database_schema": 20260910, "provider_contract": 2},
        "provider_adapter": {"provider": "claude", "active": 1, "unavailable": 0},
        "missed_runs": {"last_24_hours": 0, "last_7_days": 0},
        "quota_pressure": {"month_invocations": 3, "configured_limit": 1000},
        "backup": {
            "age_hours": backup_hours,
            "last_success_at": (NOW - timedelta(hours=backup_hours)).isoformat(),
        },
        "restore": {
            "age_days": restore_days,
            "last_verified_at": (NOW - timedelta(days=restore_days)).isoformat(),
        },
        "projection": {"checked": 2, "failed": 0, "paused": 0},
    }


def test_production_preflight_requires_fresh_clean_evidence_and_exact_confirmations():
    staging_report = deploy.build_release_report(
        {
            "environment": "staging",
            "commit": "a" * 40,
            "checks": {name: "passed" for name in deploy.STAGING_CHECKS},
            "restore_age_days": 1,
        },
        now=NOW,
    )
    result = deploy.validate_production_evidence(
        health(),
        expected_commit="a" * 40,
        staging_report=staging_report,
        backup_max_hours=36,
        restore_max_days=30,
        deployment_confirmation="DEPLOY PRODUCTION",
        pause_confirmation="TRIGGERS PAUSED",
        rollback_ref="c" * 40,
        now=NOW,
    )
    assert result == {"status": "ready", "rollback_ref": "c" * 40}

    for changed in (
        {"backup_hours": 37},
        {"restore_days": 31},
    ):
        changed_report = deploy.build_release_report(
            {
                "environment": "staging",
                "commit": "a" * 40,
                "checks": {name: "passed" for name in deploy.STAGING_CHECKS},
                "restore_age_days": changed.get("restore_days", 1),
            },
            now=NOW,
        )
        with pytest.raises(deploy.DeploymentRejected):
            deploy.validate_production_evidence(
                health(
                    backup_hours=changed.get("backup_hours", 1),
                    restore_days=changed.get("restore_days", 1),
                ),
                expected_commit="a" * 40,
                staging_report=changed_report,
                backup_max_hours=36,
                restore_max_days=30,
                deployment_confirmation="DEPLOY PRODUCTION",
                pause_confirmation="TRIGGERS PAUSED",
                rollback_ref="c" * 40,
                now=NOW,
            )


def test_owner_cutover_preflight_requires_staging_backup_pause_and_distinct_rollback():
    staging_report = deploy.build_release_report(
        {
            "environment": "staging",
            "commit": "a" * 40,
            "checks": {name: "passed" for name in deploy.STAGING_CHECKS},
            "restore_age_days": 1,
        },
        now=NOW,
    )
    result = deploy.validate_owner_cutover_evidence(
        expected_commit="a" * 40,
        staging_report=staging_report,
        backup_checked_at=(NOW - timedelta(hours=1)).isoformat(),
        backup_evidence_hash="b" * 64,
        deployment_confirmation="CUTOVER OWNER",
        pause_confirmation="TRIGGERS PAUSED",
        rollback_ref="c" * 40,
        now=NOW,
    )
    assert result == {"status": "ready", "rollback_ref": "c" * 40}

    rejected = (
        {"backup_checked_at": (NOW - timedelta(hours=37)).isoformat()},
        {"backup_evidence_hash": "not-a-digest"},
        {"deployment_confirmation": "DEPLOY PRODUCTION"},
        {"pause_confirmation": "not paused"},
        {"rollback_ref": "a" * 40},
    )
    defaults = {
        "expected_commit": "a" * 40,
        "staging_report": staging_report,
        "backup_checked_at": (NOW - timedelta(hours=1)).isoformat(),
        "backup_evidence_hash": "b" * 64,
        "deployment_confirmation": "CUTOVER OWNER",
        "pause_confirmation": "TRIGGERS PAUSED",
        "rollback_ref": "c" * 40,
        "now": NOW,
    }
    for change in rejected:
        with pytest.raises(deploy.DeploymentRejected):
            deploy.validate_owner_cutover_evidence(**(defaults | change))


def test_release_report_is_exact_hash_only_and_rejects_private_or_skipped_results(tmp_path):
    source = {
        "environment": "staging",
        "commit": "a" * 40,
        "checks": {
            "migration": "passed", "edge": "passed", "web": "passed",
            "tenant_isolation": "passed", "provider_handshake": "passed",
            "recovery": "passed",
        },
        "restore_age_days": 1,
    }
    report = deploy.build_release_report(source, now=NOW)
    assert set(report) == {
        "status", "environment", "commit", "generated_at", "private_data", "checks",
        "recovery", "evidence_digest",
    }
    assert report["private_data"] is False
    deploy.write_report(tmp_path / "report.json", report, now=NOW)
    assert json.loads((tmp_path / "report.json").read_text()) == report

    source["checks"]["provider_handshake"] = "skipped"
    with pytest.raises(deploy.DeploymentRejected):
        deploy.build_release_report(source)


def test_build_output_scan_rejects_secrets_source_maps_and_service_workers(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<script src='/assets/app.js'></script>")
    (dist / "assets").mkdir()
    (dist / "assets/app.js").write_text("const publishable='sb_publishable_example';")
    assert deploy.scan_build_output(dist)["files"] == 2

    for payload in (
        "sb_secret_example_should_never_ship",
        "navigator.serviceWorker.register('/sw.js')",
    ):
        (dist / "assets/app.js").write_text(payload)
        with pytest.raises(deploy.DeploymentRejected):
            deploy.scan_build_output(dist)
    (dist / "assets/app.js.map").write_text("{}")
    with pytest.raises(deploy.DeploymentRejected):
        deploy.scan_build_output(dist)


def test_staging_postgrest_attack_proves_both_owner_boundaries():
    requests = []
    responses = iter(
        [
            Response([{"id": "11111111-1111-4111-8111-111111111111"}]),
            Response([]),
            Response([{"id": "22222222-2222-4222-8222-222222222222"}]),
            Response([]),
        ]
    )

    def opener(request, timeout):
        requests.append((request, timeout))
        return next(responses)

    result = deploy.verify_postgrest_tenant_isolation(
        "https://example.supabase.co",
        "sb_publishable_test_only_0000000000000000",
        {
            "version": 1,
            "supabase_url": "https://example.supabase.co",
            "owner_a": {
                "id": "11111111-1111-4111-8111-111111111111",
                "access_token": "header.payload.signature-a",
            },
            "owner_b": {
                "id": "22222222-2222-4222-8222-222222222222",
                "access_token": "header.payload.signature-b",
            },
        },
        opener=opener,
    )

    assert result == "passed"
    assert len(requests) == 4
    assert all(request.get_header("Accept-profile") == "api" for request, _ in requests)
    assert requests[0][0].get_header("Authorization") == "Bearer header.payload.signature-a"
    assert "22222222-2222-4222-8222-222222222222" in requests[1][0].full_url


def test_staging_postgrest_attack_rejects_a_cross_owner_row():
    responses = iter(
        [
            Response([{"id": "11111111-1111-4111-8111-111111111111"}]),
            Response([{"id": "22222222-2222-4222-8222-222222222222"}]),
        ]
    )

    with pytest.raises(deploy.DeploymentRejected, match="tenant isolation"):
        deploy.verify_postgrest_tenant_isolation(
            "https://example.supabase.co",
            "sb_publishable_test_only_0000000000000000",
            {
                "version": 1,
                "supabase_url": "https://example.supabase.co",
                "owner_a": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "access_token": "header.payload.signature-a",
                },
                "owner_b": {
                    "id": "22222222-2222-4222-8222-222222222222",
                    "access_token": "header.payload.signature-b",
                },
            },
            opener=lambda *_args, **_kwargs: next(responses),
        )


def test_web_health_binds_the_live_static_bundle_to_the_expected_commit():
    commit = "a" * 40
    responses = iter([
        RawResponse(b'<main><div id="root"></div></main>'),
        Response({"environment": "staging", "commit": commit}),
    ])
    deploy._web_health(
        "https://stocks-staging.example.com",
        expected_commit=commit,
        environment="staging",
        opener=lambda *_args, **_kwargs: next(responses),
    )

    wrong = iter([
        RawResponse(b'<div id="root"></div>'),
        Response({"environment": "staging", "commit": "b" * 40}),
    ])
    with pytest.raises(deploy.DeploymentRejected, match="release marker"):
        deploy._web_health(
            "https://stocks-staging.example.com",
            expected_commit=commit,
            environment="staging",
            opener=lambda *_args, **_kwargs: next(wrong),
        )


def test_staging_handshake_evidence_must_be_recent_and_not_future_dated():
    deploy.validate_handshake_freshness(
        {
            "last_handshake_at": NOW - timedelta(minutes=2),
            "slot_updated_at": (NOW - timedelta(minutes=1)).isoformat(),
        },
        now=NOW,
    )
    for observed in (NOW - timedelta(hours=25), NOW + timedelta(hours=2)):
        with pytest.raises(deploy.DeploymentRejected, match="handshake evidence"):
            deploy.validate_handshake_freshness(
                {"last_handshake_at": observed, "slot_updated_at": observed},
                now=NOW,
            )
