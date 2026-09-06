from pathlib import Path
import re
import yaml


def test_protected_release_and_recovery_are_valid_workflow_yaml():
    for name in (
        "owner-dashboard-ci.yml",
        "owner-dashboard-release.yml",
        "owner-dashboard-release-recovery.yml",
    ):
        workflow = yaml.safe_load((Path(".github/workflows") / name).read_text())
        assert workflow["jobs"]


def test_ci_fetches_the_audited_baseline_history():
    workflow = yaml.safe_load(Path(".github/workflows/owner-dashboard-ci.yml").read_text())
    checkout = next(
        step
        for step in workflow["jobs"]["verify"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0


def test_protected_release_workflow_is_manual_only_and_binds_immutable_evidence():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "workflow_run:" not in workflow
    assert "github.event.workflow_run" not in workflow
    assert "git merge-base --is-ancestor \"$CANDIDATE_SHA\" origin/main" in workflow
    assert "scripts/deploy_owner_dashboard_api.py" in workflow
    assert "repos/$GITHUB_REPOSITORY/deployments" in workflow
    assert "release-record.json" in workflow
    assert "actions/upload-artifact@" in workflow
    assert "start_run" not in workflow
    assert "collect_market_intelligence.py" not in workflow
    assert '"${GITHUB_REF:-}" = "refs/heads/main"' in workflow
    assert '"${GITHUB_SHA:-}" = "$CANDIDATE_SHA"' in workflow


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
    assert "--require-hashes --only-binary=:all: -r requirements.lock" in workflow
    assert "supabase@2.116.0" in workflow
    assert 'git/ref/heads/main' in workflow
    assert "actions/runs/$CI_WORKFLOW_RUN_ID" in workflow
    assert "/pulls/$PULL_REQUEST_NUMBER" in workflow
    assert "/reviews" in workflow
    assert '"$CANDIDATE_SHA" = "$MAIN_SHA"' in workflow
    assert 'export CANDIDATE_SHA' in workflow
    assert "${{ vars.SUPABASE_PROJECT_REF }}" not in workflow
    assert "${{ secrets.SUPABASE_PROJECT_REF }}" in workflow


def test_release_workflow_retains_and_restores_rollback_source_until_evidence_is_accepted():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "restore_gateway_after_release_failure.py" in workflow
    assert "trap 'restore_after_failure' ERR" in workflow
    assert "RELEASE_RECOVERY_KEY" in workflow
    assert "Release local rollback worktree" not in workflow
    assert "rollback_readiness" in Path("scripts/write_protected_release_record.py").read_text()
    assert "dry-run-evidence.json" in workflow


def test_release_failure_recovery_is_safe_when_preflight_never_created_state_directory():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery_step = workflow.split(
        "- name: Restore changed components if any post-deploy evidence step failed",
        maxsplit=1,
    )[1].split("- name:", maxsplit=1)[0]
    assert 'test -n "${RELEASE_STATE_DIR:-}" || exit 0' in recovery_step
    assert recovery_step.index('${RELEASE_STATE_DIR:-}') < recovery_step.index(
        '$RELEASE_STATE_DIR/release-state.json'
    )


def test_final_release_workflow_keeps_all_ephemera_outside_the_checkout_and_uses_state_journal():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "RUNNER_TEMP" in workflow
    assert "release-state.json" in workflow
    assert "--release-state" in workflow
    assert "--dry-run" in workflow
    assert "npx playwright install --with-deps chromium" in workflow
    assert "cryptography==" in Path("requirements.lock").read_text()
    assert '"$PR_HEAD_SHA"' in workflow
    assert "reviewed head does not bind candidate" in workflow


def test_release_workflow_binds_review_times_and_durable_pre_mutation_recovery():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert 'PYTHON_BIN="$RELEASE_VENV/bin/python"' in workflow
    assert 'head_commit_time <= review.submitted_at <= merged_at <= candidate_commit_time' in workflow
    assert "component-recovery-run:$GITHUB_RUN_ID" in workflow
    assert "release_components.py --check-transport" in workflow
    assert Path(".github/workflows/owner-dashboard-release-recovery.yml").is_file()
    assert workflow.index("release_components.py --check-transport") < workflow.index("Create the candidate-bound GitHub Deployment")


def test_independent_recovery_contract_covers_cancelled_and_lost_release_runners():
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    assert "workflow_run:" in recovery
    assert "conclusion != 'success'" in recovery
    assert '--release-run-id "${{ github.event.workflow_run.id }}"' in recovery
    assert "RELEASE_RECOVERY_KEY" in recovery
    assert "recovery/release.enc" in recovery
    assert "recovery-metadata/recovery-metadata" not in recovery
    assert "${{ vars.SUPABASE_PROJECT_REF }}" not in recovery
    assert "${{ secrets.SUPABASE_PROJECT_REF }}" in recovery
    assert "Verify exact failed-release deployment trust marker" in recovery
    assert "component-recovery-run:$RUN_ID" in recovery
    assert "RUN_ATTEMPT=\"${{ github.event.workflow_run.run_attempt }}\"" in recovery
    assert "component-recovery-run:$RUN_ID:$RUN_ATTEMPT" in recovery
    assert "release_workflow_run_attempt" in recovery
    assert "release_workflow_run_id" in recovery and "candidate_sha" in recovery
    assert "steps.trust.outputs.trusted == 'true'" in recovery
    trust = recovery.split("- name: Verify exact failed-release deployment trust marker", 1)[1].split("- name:", 1)[0]
    assert "GH_TOKEN: ${{ github.token }}" in trust
    assert "SUPABASE_ACCESS_TOKEN" not in trust and "RELEASE_RECOVERY_KEY" not in trust and "POSTGRES_URL" not in trust


def test_release_deployment_and_recovery_marker_bind_the_original_run_attempt():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    assert 'release_workflow_run_attempt "$GITHUB_RUN_ATTEMPT"' in workflow
    assert 'component-recovery-run:$GITHUB_RUN_ID:$GITHUB_RUN_ATTEMPT' in workflow
    assert "release_workflow_run_attempt" in recovery
    assert "component-recovery-run:$RUN_ID:$RUN_ATTEMPT" in recovery


def test_recovery_trust_rejects_feature_or_unreviewed_run_before_checkout():
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    trust = recovery.split("- name: Verify exact failed-release deployment trust marker", 1)[1].split("- uses: actions/checkout", 1)[0]
    assert '.path == ".github/workflows/owner-dashboard-release.yml"' in trust
    assert '.name == "Protected owner dashboard release"' in trust
    assert '.head_branch == "main"' in trust and '.event == "workflow_dispatch"' in trust
    assert "Owner dashboard verification" in trust
    assert ".github/workflows/owner-dashboard-ci.yml" in trust
    assert "head_sha=$HEAD_SHA" in trust


def test_protected_release_and_recovery_install_only_the_complete_hashed_lock():
    required = "--require-hashes --only-binary=:all: -r requirements.lock"
    for path in (
        ".github/workflows/owner-dashboard-release.yml",
        ".github/workflows/owner-dashboard-release-recovery.yml",
    ):
        workflow = Path(path).read_text()
        assert required in workflow
        assert "requirements-test.txt" not in workflow


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
    assert "--release-run-id" in recovery
    assert "--retain-recovery-artifact" in recovery
    assert "conclusion != 'success'" in recovery
    finalizer = Path("scripts/finalize_protected_release.py").read_text()
    assert "finalize_protected_release.py" in workflow and "state=success" in finalizer
    assert workflow.index("Upload immutable release record") < workflow.index("Mark candidate deployment successful")
    assert "steps.deployment.outputs.required" not in recovery
    assert "Restore encrypted changed-component journal" in recovery


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
