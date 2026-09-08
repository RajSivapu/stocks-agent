from pathlib import Path
import json
import re
import shutil
import subprocess
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


def test_release_binds_the_approved_pr_head_and_exact_main_ci_metadata():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "Exact approved PR head SHA" in workflow
    assert 'test "$INPUT_REVIEWED_SHA" = "$PR_HEAD_SHA"' in workflow
    assert "required CI belongs to another repository" in workflow
    assert "required CI workflow name mismatch" in workflow
    assert "required CI was not an exact-main push" in workflow
    assert "reviewed_sha=$PR_HEAD_SHA" in workflow


def test_release_authenticates_candidate_before_checkout_dependencies_or_production_secrets():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.index("Authenticate exact reviewed main candidate without candidate code")
    checkout = workflow.index("uses: actions/checkout@")
    install = workflow.index("npm ci --ignore-scripts")
    production_secret = workflow.index("SUPABASE_ACCESS_TOKEN: ${{ secrets.SUPABASE_ACCESS_TOKEN }}")
    assert trust < checkout < install < production_secret
    pre_checkout = workflow[trust:checkout]
    assert "SUPABASE_ACCESS_TOKEN" not in pre_checkout
    assert "RELEASE_RECOVERY_KEY" not in pre_checkout
    assert "POSTGRES_URL" not in pre_checkout
    assert "git/commits/$PR_HEAD_SHA" in pre_checkout
    assert "git/commits/$CANDIDATE_SHA" in pre_checkout
    assert "reviewed and candidate Git trees differ" in pre_checkout


def test_release_uses_latest_review_per_reviewer_before_exposing_production_secrets():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    assert "group_by(.user.id)" in trust
    assert 'max_by([.submitted_at // "", .id])' in trust
    assert 'all(.state != "CHANGES_REQUESTED")' in trust
    assert '.state == "APPROVED"' in trust
    assert "SUPABASE_ACCESS_TOKEN" not in trust


def test_release_executes_exact_review_filter_for_latest_decisions_and_ties():
    jq = shutil.which("jq")
    if jq is None:
        raise AssertionError("jq is required to verify the protected review filter")
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    filter_line = next(
        line.strip() for line in trust.splitlines()
        if line.strip().startswith("'[.[] | select(")
    )
    review_filter = filter_line.removesuffix(" \\").removeprefix("'").removesuffix("'")
    sha = "a" * 40
    base = {
        "commit_id": sha, "submitted_at": "2026-09-05T17:45:00Z",
    }

    def accepted(rows):
        result = subprocess.run([
            jq, "-e", "--arg", "sha", sha,
            "--arg", "head", "2026-09-05T17:40:00Z",
            "--arg", "merged", "2026-09-05T18:00:00Z",
            "--arg", "candidate", "2026-09-05T18:00:00Z", review_filter,
        ], input=json.dumps(rows), text=True, capture_output=True)
        return result.returncode == 0

    assert accepted([{**base, "id": 1, "state": "APPROVED", "user": {"id": 10}}])
    assert not accepted([
        {**base, "id": 1, "state": "APPROVED", "user": {"id": 10}},
        {**base, "id": 2, "state": "CHANGES_REQUESTED", "user": {"id": 10}},
    ])
    assert accepted([
        {**base, "id": 1, "state": "CHANGES_REQUESTED", "user": {"id": 10}},
        {**base, "id": 2, "state": "DISMISSED", "user": {"id": 10}},
        {**base, "id": 3, "state": "APPROVED", "user": {"id": 11}},
    ])
    assert not accepted([
        {**base, "id": 1, "state": "APPROVED", "user": {"id": 10}},
        {**base, "id": 0, "state": "DISMISSED", "user": {"id": 11}},
    ])


def test_candidate_dry_run_and_site_build_steps_do_not_receive_privileged_secrets():
    workflow = yaml.safe_load(Path(".github/workflows/owner-dashboard-release.yml").read_text())
    steps = {row.get("name"): row for row in workflow["jobs"]["release"]["steps"]}
    privileged = {"SUPABASE_ACCESS_TOKEN", "RELEASE_RECOVERY_KEY", "POSTGRES_URL",
        "SUPABASE_SERVICE_ROLE_KEY", "DASHBOARD_PRIOR_MANAGED_SECRETS_JSON"}
    dry = steps["Derive protected no-side-effect evidence around the real candidate dry-run"]
    build = steps["Build candidate Site assets without privileged production credentials"]

    assert privileged.isdisjoint(dry.get("env", {}))
    assert privileged.isdisjoint(build.get("env", {}))
    assert "--static-build-receipt" in steps[
        "Execute protected deployment with encrypted component recovery"
    ]["run"]


def test_protected_workflows_use_the_same_exact_main_ci_trust_contract():
    required = (
        "required CI belongs to another repository",
        "Owner dashboard verification",
        ".github/workflows/owner-dashboard-ci.yml",
        "required CI was not an exact-main push",
    )
    for path in (
        ".github/workflows/owner-dashboard-release.yml",
        ".github/workflows/owner-dashboard-release-recovery.yml",
        ".github/workflows/managed-isolated-restore.yml",
        ".github/workflows/existing-v1-runtime-attestation.yml",
    ):
        workflow = Path(path).read_text()
        assert all(value in workflow for value in required), path


def test_release_record_keeps_release_and_later_scheduled_receipts_distinct():
    from scripts import write_protected_release_record as writer

    classify = getattr(writer, "release_evidence_classes", None)
    assert callable(classify)
    result = classify({
        "candidate_sha": "a" * 40,
        "canary": {
            "status": "verified",
            "source_reconciliation": "verified",
            "financial_write_routes": 0,
            "brokerage_authority": "none",
            "friend_invitations": "disabled",
        },
    })
    assert result == {
        "protected_backend": {"status": "verified", "candidate_sha": "a" * 40},
        "owner_site": {
            "status": "pending",
            "required_evidence": "exact_candidate_owner_only_native_site_receipt",
        },
        "operational_scheduled": {
            "status": "pending",
            "required_evidence": "normal_post_release_scheduled_receipt",
        },
        "discovery_capability": {
            "status": "pending",
            "checkpoint": "V1-C3",
            "required_evidence": "normal_post_release_scheduled_capability_receipt",
        },
    }


def test_release_record_rejects_a_receipt_from_another_candidate():
    from scripts import write_protected_release_record as writer

    validate = getattr(writer, "validate_release_identity", None)
    assert callable(validate)
    assert validate({"candidate_sha": "a" * 40}, "a" * 40, "b" * 40) == (
        "a" * 40,
        "b" * 40,
    )
    for receipt, candidate, reviewed in (
        ({"candidate_sha": "b" * 40}, "a" * 40, "b" * 40),
        ({"candidate_sha": "a" * 40}, "not-a-sha", "b" * 40),
        ({"candidate_sha": "a" * 40}, "a" * 40, "NOT-A-SHA"),
    ):
        try:
            validate(receipt, candidate, reviewed)
        except RuntimeError as error:
            assert "release identity" in str(error)
        else:
            raise AssertionError("invalid release identity was accepted")


def test_release_record_writer_emits_backend_only_evidence_contract(tmp_path, monkeypatch):
    import json
    import sys
    from scripts import write_protected_release_record as writer

    (tmp_path / "apps/web").mkdir(parents=True)
    (tmp_path / "apps/web/main.tsx").write_text("web")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/index.html").write_text("site")
    recovery = {"sequence": 1, "run_id": 50, "run_attempt": 2,
        "captured_at": "2026-09-08T12:00:00Z", "ciphertext_sha256": "d" * 64}
    receipt = {"candidate_sha": "a" * 40, "deployment_outcome": "succeeded",
        "migrations": [], "migration_application": {}, "functions": [],
        "static_assets": {"status": "verified", "candidate_sha": "a" * 40,
            "files": {"index.html": "f" * 64}},
        "component_readbacks": [{"component": name, "prior": {"exists": True}}
            for name in ("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio")],
        "backend_evidence": {"manifest_sha256": "b" * 64,
            "recovery_metadata_sha256": "c" * 64},
        "recovery_journal": recovery,
        "canary": {"status": "verified", "source_reconciliation": "verified",
            "financial_write_routes": 0, "brokerage_authority": "none",
            "friend_invitations": "disabled"}}
    dry = {"table_deltas": {}}
    receipt_path, dry_path, output = (tmp_path / name for name in ("receipt.json", "dry.json", "record.json"))
    receipt_path.write_text(json.dumps(receipt)); dry_path.write_text(json.dumps(dry))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["writer", "--receipt", str(receipt_path),
        "--dry-run-evidence", str(dry_path), "--output", str(output),
        "--candidate-sha", "a" * 40, "--reviewed-sha", "f" * 40,
        "--repository", "owner/stocks-agent", "--ci-workflow-run-id", "40",
        "--release-workflow-run-id", "50", "--release-workflow-run-attempt", "2",
        "--pull-request-number", "44", "--deployment-id", "42",
        "--backend-evidence-artifact-id", "60",
        "--backend-evidence-artifact-name", "backend-component-evidence-50-2",
        "--backend-evidence-artifact-digest", "sha256:" + "e" * 64,
        "--project-ref", "p" * 20])

    assert writer.main() == 0
    record = json.loads(output.read_text())
    assert record["backend_evidence_artifact"] == {"artifact_id": 60,
        "name": "backend-component-evidence-50-2", "digest": "sha256:" + "e" * 64,
        "manifest_sha256": "b" * 64, "recovery_metadata_sha256": "c" * 64}
    assert record["recovery_journal"] == recovery
    assert record["evidence_classes"]["owner_site"]["status"] == "pending"
    assert all(row["artifact_id"] == 60 for row in record["component_readbacks"])


def test_release_workflow_retains_and_restores_encrypted_backend_state_until_evidence_is_accepted():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "restore_gateway_after_release_failure.py" in workflow
    assert "trap 'restore_after_failure' ERR" in workflow
    assert "RELEASE_RECOVERY_KEY" in workflow
    assert "Release local rollback worktree" not in workflow
    writer = Path("scripts/write_protected_release_record.py").read_text()
    assert '"recovery_journal"' in writer
    assert '"backend_evidence_artifact"' in writer
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
    assert "reviewed and candidate Git trees differ" in workflow


def test_release_workflow_binds_review_times_and_durable_pre_mutation_recovery():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert 'PYTHON_BIN="$RELEASE_VENV/bin/python"' in workflow
    assert 'head_commit_time <= latest review.submitted_at <= merged_at <= candidate_commit_time' in workflow
    assert "component-recovery-run:$GITHUB_RUN_ID" in workflow
    assert "release_components.py --check-backend-transport" in workflow
    assert Path(".github/workflows/owner-dashboard-release-recovery.yml").is_file()
    assert workflow.index("release_components.py --check-backend-transport") < workflow.index("Create the candidate-bound GitHub Deployment")


def test_backend_release_does_not_claim_or_require_native_site_deployment():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    writer = Path("scripts/write_protected_release_record.py").read_text()
    assert "--check-transport" not in workflow
    assert "--check-backend-transport" in workflow
    assert "owner-web-site" not in writer.split("component_readbacks", 1)[1].split("]", 1)[0]
    assert '"owner_site"' in writer
    assert "native Sites receipt" in Path("docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md").read_text()


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


def test_recovery_journal_invocation_binds_the_exact_release_run_attempt():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    deployer = Path("scripts/deploy_owner_dashboard_api.py").read_text()
    restorer = Path("scripts/restore_gateway_after_release_failure.py").read_text()
    assert '"release_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT")' in deployer
    assert 'parser.add_argument("--release-run-attempt", type=int, required=True)' in restorer
    assert '--release-run-id "$GITHUB_RUN_ID"' in workflow
    assert '--release-run-attempt "$GITHUB_RUN_ATTEMPT"' in workflow
    assert '--release-run-attempt "${{ github.event.workflow_run.run_attempt }}"' in recovery


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
    assert 'echo "CANDIDATE_SHA=${{ steps.candidate.outputs.candidate_sha }}" >> "$GITHUB_ENV"' in workflow
    assert 'CANDIDATE_SHA: ${{ steps.candidate.outputs.candidate_sha }}' in workflow
    assert 'state=in_progress' in workflow


def test_release_and_recovery_use_separate_safe_actions_concurrency_boundaries():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    assert "backend-component-evidence-" in workflow
    assert "recovery-metadata.json" in Path("scripts/configured_native_release_adapter.py").read_text()
    assert "group: protected-owner-dashboard-release-production" in workflow
    assert "group: protected-owner-dashboard-release-production-${{" not in workflow
    assert "group: protected-owner-dashboard-release-recovery-${{ github.event.workflow_run.id }}-${{ github.event.workflow_run.run_attempt }}" in recovery
    assert "protected-owner-dashboard-release-production-${{" not in recovery
    assert "cancel-in-progress: false" in recovery
    trust = recovery.split("- name: Verify exact failed-release deployment trust marker", 1)[1].split("- name:", 1)[0]
    assert 'jq -r .run_attempt' not in trust
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in recovery
    assert "--release-run-id" in recovery
    assert "--retain-recovery-artifact" in recovery
    assert "conclusion != 'success'" in recovery
    finalizer = Path("scripts/finalize_protected_release.py").read_text()
    assert "finalize_protected_release.py" in workflow and "state=success" in finalizer
    assert workflow.index("Upload immutable release record") < workflow.index("Mark candidate deployment successful")
    assert workflow.index("Mark candidate deployment successful") < workflow.index("Restore changed components if any post-deploy evidence step failed")
    assert workflow.index("Restore changed components if any post-deploy evidence step failed") < workflow.index("Mark candidate deployment failed after protected restoration")
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
