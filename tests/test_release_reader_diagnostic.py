from __future__ import annotations

import json
from pathlib import Path

from scripts.release_reader_diagnostic import diagnose_release_reader


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40
PASSWORD = "private-password-that-must-never-leak"
DATABASE_URL = (
    f"postgresql://stock_agent_release_reader_runtime.{PROJECT_REF}:{PASSWORD}"
    "@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)


class Source:
    def __init__(self, database_url, project_ref, *, pre_migration_baseline):
        assert database_url == DATABASE_URL
        assert project_ref == PROJECT_REF
        assert pre_migration_baseline is True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def identity(self):
        return {
            "project_ref": PROJECT_REF,
            "connection_id": "b" * 64,
            "read_only": True,
            "isolated_guard": False,
        }

    def authority_receipt(self):
        return {"status": "verified", "read_table_count": 31}

    def pre_migration_scope(self):
        return {
            "reason": "candidate migrations have not been applied",
            "absent_tables": ["market_reference_manifests"],
            "unreadable_tables": ["market_source_items"],
        }


def environment():
    return {"RELEASE_READONLY_DATABASE_URL": DATABASE_URL}


def test_diagnostic_emits_only_bounded_verified_pre_migration_evidence():
    receipt = diagnose_release_reader(
        environment(), PROJECT_REF, MAIN_SHA, source_factory=Source,
    )

    assert receipt["format"] == "stocks-release-reader-diagnostic-v1"
    assert receipt["credential"] == {"status": "valid"}
    assert receipt["preflight"] == {
        "status": "verified",
        "database_identity": "verified",
        "authority": "verified",
        "read_table_count": 31,
        "baseline_state": "exact_pre_migration",
        "absent_table_count": 1,
        "unreadable_table_count": 1,
    }
    rendered = json.dumps(receipt, sort_keys=True)
    assert PASSWORD not in rendered and DATABASE_URL not in rendered
    assert PROJECT_REF not in rendered
    assert len(receipt["receipt_sha256"]) == 64


def test_diagnostic_suppresses_private_authority_error_and_keeps_reason_code():
    class UnsafeSource(Source):
        def __enter__(self):
            raise RuntimeError("release reader function authority is unsafe")

    receipt = diagnose_release_reader(
        environment(), PROJECT_REF, MAIN_SHA, source_factory=UnsafeSource,
    )

    assert receipt["credential"] == {"status": "valid"}
    assert receipt["preflight"] == {
        "status": "failed", "error_code": "function_authority_mismatch",
    }
    assert PASSWORD not in json.dumps(receipt)


def test_diagnostic_rejects_missing_or_malformed_credentials_without_connecting():
    for env, error_code in (
        ({}, "credential_missing"),
        ({"RELEASE_READONLY_DATABASE_URL": "postgresql://wrong"},
         "credential_url_invalid"),
    ):
        receipt = diagnose_release_reader(
            env, PROJECT_REF, MAIN_SHA,
            source_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("must not connect")
            ),
        )
        assert receipt["credential"] == {
            "status": "failed", "error_code": error_code,
        }
        assert receipt["preflight"] == {"status": "not_run"}


def test_release_reader_diagnostic_workflow_is_manual_exact_main_and_read_only():
    workflow = Path(
        ".github/workflows/production-release-reader-diagnostic.yml"
    ).read_text()

    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "environment: owner-dashboard-production" in workflow
    assert "refs/heads/main" in workflow and "GITHUB_SHA" in workflow
    assert "Owner dashboard verification" in workflow
    assert ".github/workflows/owner-dashboard-ci.yml" in workflow
    assert "RELEASE_READONLY_DATABASE_URL: ${{ secrets.RELEASE_READONLY_DATABASE_URL }}" in workflow
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in workflow
    assert "release_reader_diagnostic.py" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "production-release-reader-diagnostic-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "retention-days: 30" in workflow
    for forbidden in (
        "SUPABASE_ACCESS_TOKEN", "SUPABASE_SERVICE_ROLE_KEY",
        "deploy_owner_dashboard_api.py", "managed_isolated_restore.py",
    ):
        assert forbidden not in workflow
    for action in ("actions/checkout@", "actions/setup-python@", "actions/upload-artifact@"):
        pinned = [line for line in workflow.splitlines() if action in line]
        assert pinned and all(len(line.rsplit("@", 1)[1].strip()) == 40 for line in pinned)
