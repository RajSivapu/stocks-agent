from pathlib import Path
import re


def test_protected_release_workflow_binds_a_successful_main_candidate_to_immutable_evidence():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "workflow_run:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "git merge-base --is-ancestor \"$CANDIDATE_SHA\" origin/main" in workflow
    assert "scripts/deploy_owner_dashboard_api.py" in workflow
    assert "repos/$GITHUB_REPOSITORY/deployments" in workflow
    assert "release-record.json" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "start_run" not in workflow
    assert "collect_market_intelligence.py" not in workflow


def test_release_workflow_writes_authoritative_non_dry_run_and_candidate_bound_record_fields():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    for field in ("'candidate_sha'", "'workflow_run_id'", "'deployment_id'", "'dry_run':False", "'migrations'"):
        assert field in workflow
    assert "candidate SHA/ref mismatch" in workflow


def test_release_workflow_pins_all_third_party_actions():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    references = re.findall(r"uses:\s+([^\s]+)@([^\s]+)", workflow)
    assert references
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _action, revision in references)
