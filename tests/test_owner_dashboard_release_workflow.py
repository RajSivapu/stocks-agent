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
    writer = Path("scripts/write_protected_release_record.py").read_text()
    for field in ('"candidate_sha"', '"workflow_run_id"', '"deployment_id"', '"dry_run": False', '"migrations"'):
        assert field in writer
    assert "write_protected_release_record.py" in workflow
    assert "candidate SHA/ref mismatch" in workflow


def test_release_workflow_pins_all_third_party_actions():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    references = re.findall(r"uses:\s+([^\s]+)@([^\s]+)", workflow)
    assert references
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for _action, revision in references)


def test_release_workflow_has_all_mutation_preconditions_and_pinned_tools():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "group: protected-owner-dashboard-release-production" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}" in workflow
    assert 'test -n "$SUPABASE_ACCESS_TOKEN"' in workflow
    assert "npm ci --ignore-scripts" in workflow
    assert "requirements-test.txt" in workflow
    assert "supabase@2.116.0" in workflow
    assert 'git/ref/heads/main' in workflow
    assert "actions/runs/$CI_WORKFLOW_RUN_ID" in workflow
    assert "/pulls/$PULL_REQUEST_NUMBER" in workflow
    assert "/reviews" in workflow
    assert '"$CANDIDATE_SHA" = "$MAIN_SHA"' in workflow


def test_release_workflow_retains_and_restores_rollback_source_until_evidence_is_accepted():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "restore_gateway_after_release_failure.py" in workflow
    assert "trap 'restore_after_failure' ERR" in workflow
    assert "--keep-rollback-worktree" in workflow
    assert workflow.index("Upload immutable release record") < workflow.index("Release local rollback worktree")
    assert "rollback_readiness" in Path("scripts/write_protected_release_record.py").read_text()
    assert "dry-run-evidence.json" in workflow


def test_final_release_workflow_keeps_all_ephemera_outside_the_checkout_and_uses_state_journal():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "RUNNER_TEMP" in workflow
    assert "release-state.json" in workflow
    assert "--release-state" in workflow
    assert "--dry-run" in workflow
    assert "npx playwright install --with-deps chromium" in workflow
    assert "cryptography==" in Path("requirements-test.txt").read_text()
    assert '"$PR_HEAD_SHA"' in workflow
    assert "reviewed head does not bind candidate" in workflow


def test_release_workflow_binds_review_times_and_durable_pre_mutation_recovery():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert 'PYTHON_BIN="$RELEASE_VENV/bin/python"' in workflow
    assert 'head_commit_time <= review.submitted_at <= merged_at <= candidate_commit_time' in workflow
    assert "Upload prior gateway source before gateway mutation" in workflow
    assert "rollback-source:$ROLLBACK_SOURCE_ARTIFACT_ID" in workflow
    assert Path(".github/workflows/owner-dashboard-release-recovery.yml").is_file()
    assert workflow.index("Upload prior gateway source before gateway mutation") < workflow.index("Execute protected deployment")


def test_independent_recovery_contract_covers_cancelled_and_lost_release_runners():
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    assert "workflow_run:" in recovery
    assert "conclusion != 'success'" in recovery
    assert "rollback-source-${{ github.event.workflow_run.id }}" in recovery
    assert "--recovery-root" in recovery
    assert "recovery-metadata/release-state.json" in recovery
    assert "recovery-metadata/recovery-metadata" not in recovery


def test_release_exports_candidate_for_every_set_u_dry_run_and_recovery_step():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert 'echo "CANDIDATE_SHA=$CANDIDATE_SHA" >> "$GITHUB_ENV"' in workflow
    assert 'CANDIDATE_SHA: ${{ steps.candidate.outputs.candidate_sha }}' in workflow
    assert 'state=in_progress' in workflow


def test_recovery_uses_exact_candidate_concurrency_and_separate_durable_artifacts():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    assert "rollback-source-" in workflow and "recovery-metadata-" in workflow
    assert "group: protected-owner-dashboard-recovery-${{ github.event.workflow_run.id }}" in recovery
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in recovery
    assert "deployments/$DEPLOYMENT_ID/statuses" in recovery
    assert "--retain-recovery-artifact" in recovery
    assert "conclusion != 'success'" in recovery
    finalizer = Path("scripts/finalize_protected_release.py").read_text()
    assert "finalize_protected_release.py" in workflow and "state=success" in finalizer
    assert workflow.index("Release local rollback worktree") < workflow.index("Mark candidate deployment successful")
    assert "steps.deployment.outputs.required" not in recovery
    assert recovery.index("Restore durable gateway recovery artifact") > recovery.index("Record terminal deployment status")


def test_database_lease_is_the_authoritative_release_recovery_serialization_boundary():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    deployer = Path("scripts/deploy_owner_dashboard_api.py").read_text()
    restorer = Path("scripts/restore_gateway_after_release_failure.py").read_text()
    assert "RELEASE_LEASE_OWNER=release-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" in workflow
    assert "--lease-owner \"$RELEASE_LEASE_OWNER\"" in workflow
    assert "actions/runs?event=workflow_run&status=in_progress" not in workflow
    assert "DurableMutationLease" in deployer and "pg_advisory_lock" in deployer
    assert "recovery_required" in deployer and "lease.resolve()" in restorer
    assert "finalize_protected_release.py" in workflow
