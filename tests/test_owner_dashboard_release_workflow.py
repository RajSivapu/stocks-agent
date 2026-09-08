from pathlib import Path
import copy
import hashlib
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


def test_protected_release_python_entrypoints_preserve_repository_package_imports():
    release = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    recovery = Path(".github/workflows/owner-dashboard-release-recovery.yml").read_text()
    for module in (
        "release_components",
        "collect_protected_dry_run_evidence",
        "build_owner_dashboard_static",
        "deploy_owner_dashboard_api",
        "write_protected_release_record",
        "finalize_protected_release",
        "restore_gateway_after_release_failure",
    ):
        assert f"-m scripts.{module}" in release
    assert "-m scripts.restore_gateway_after_release_failure" in recovery
    assert '"$PYTHON_BIN -m scripts.deploy_owner_dashboard_api --dry-run' in release


def test_protected_release_pins_the_supabase_database_root_ca():
    workflow = yaml.safe_load(Path(".github/workflows/owner-dashboard-release.yml").read_text())
    release = workflow["jobs"]["release"]
    assert release["env"]["PGSSLROOTCERT"] == (
        "${{ github.workspace }}/config/supabase-prod-ca-2021.crt"
    )
    certificate = Path("config/supabase-prod-ca-2021.crt").read_bytes()
    assert hashlib.sha256(certificate).hexdigest() == (
        "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7"
    )


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
    assert "-m scripts.write_protected_release_record" in workflow
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


def test_release_allows_a_pr_ci_bound_owner_authorization_for_a_solo_repository():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    assert "pr_ci_workflow_run_id" in workflow
    assert "PR_CI_WORKFLOW_RUN_ID" in trust
    assert 'event <<<"$PR_CI")" = pull_request' in trust
    assert 'head_sha <<<"$PR_CI")" = "$PR_HEAD_SHA"' in trust
    assert "/issues/$PULL_REQUEST_NUMBER/comments?per_page=100" in trust
    assert "OWNER_RELEASE_APPROVAL_V1" in trust
    assert '.author_association == "OWNER"' in trust
    assert ".user.id == $owner" in trust
    assert '.created_at >= $pr_ci and .created_at <= .updated_at and .updated_at <= $merged' in trust
    assert "authorization_kind" in trust and "authorization_id" in trust
    assert "SUPABASE_ACCESS_TOKEN" not in trust


def test_owner_comment_authorization_rejects_a_post_merge_edit():
    jq = shutil.which("jq")
    if jq is None:
        raise AssertionError("jq is required to verify the owner authorization filter")
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    filter_line = next(
        line.strip() for line in trust.splitlines()
        if line.strip().startswith('OWNER_APPROVAL="$(jq -c')
    )
    owner_filter = filter_line.split(" '[", 1)[1].rsplit("' <<<", 1)[0]
    owner_filter = "[" + owner_filter
    body = "OWNER_RELEASE_APPROVAL_V1\nreviewed_sha=" + "a" * 40 + "\npr_ci_workflow_run_id=41"

    def accepted(updated_at):
        comment = [{"id": 46, "user": {"id": 7}, "author_association": "OWNER",
            "created_at": "2026-09-05T17:55:00Z", "updated_at": updated_at,
            "body": body}]
        result = subprocess.run([
            jq, "-e", "--arg", "body", body, "--argjson", "owner", "7",
            "--arg", "pr_ci", "2026-09-05T17:50:00Z",
            "--arg", "merged", "2026-09-05T18:00:00Z", owner_filter,
        ], input=json.dumps(comment), text=True, capture_output=True)
        return result.returncode == 0 and bool(result.stdout.strip())

    assert accepted("2026-09-05T17:55:00Z")
    assert not accepted("2026-09-05T18:05:00Z")


def test_release_requires_pr_ci_to_complete_before_merge():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    assert '.updated_at >= $head and .updated_at <= $merged' in trust


def test_release_uses_durable_pr_head_coordinates_when_github_clears_run_pr_array():
    jq = shutil.which("jq")
    if jq is None:
        raise AssertionError("jq is required to verify the PR CI binding filter")
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    assert 'PR_HEAD_REF="$(jq -r .head.ref <<<"$PR")"' in trust
    assert 'PR_HEAD_REPO="$(jq -r .head.repo.full_name <<<"$PR")"' in trust
    assert "(.head.ref | type == \"string\" and length > 0)" in trust
    assert "(.head.repo.full_name | type == \"string\" and length > 0)" in trust
    assert '.head_branch == $head_ref' in trust
    assert '.head_repository.full_name == $head_repo' in trust
    assert 'length == 0 or any(.[]; .number == $number)' in trust
    filter_line = next(
        line.strip() for line in trust.splitlines()
        if line.strip().startswith("PR_CI_BINDING_FILTER='")
    )
    binding_filter = filter_line.removeprefix("PR_CI_BINDING_FILTER='").removesuffix("'")

    def accepted(payload):
        result = subprocess.run([
            jq, "-e", "--argjson", "number", "35",
            "--arg", "head_ref", "candidate",
            "--arg", "head_repo", "owner/stocks-agent", binding_filter,
        ], input=json.dumps(payload), text=True, capture_output=True)
        return result.returncode == 0

    base = {"head_branch": "candidate",
        "head_repository": {"full_name": "owner/stocks-agent"}}
    assert accepted({**base, "pull_requests": []})
    assert accepted({**base, "pull_requests": [{"number": 35}]})
    assert not accepted({**base, "head_branch": "other", "pull_requests": []})
    assert not accepted({**base,
        "head_repository": {"full_name": "other/stocks-agent"}, "pull_requests": []})
    assert not accepted({**base, "pull_requests": [{"number": 36}]})
    assert not accepted({**base, "pull_requests": [{"number": 35}, {}]})
    assert not accepted({**base, "pull_requests": [{"number": 35}, {"number": 1.5}]})
    assert not accepted({**base, "pull_requests": {}})

    coordinate_filter = (
        '(.head.ref | type == "string" and length > 0) and '
        '(.head.repo.full_name | type == "string" and length > 0)'
    )
    for malformed in ({}, {"head": {}},
                      {"head": {"ref": None, "repo": {"full_name": None}}},
                      {"head": {"ref": 7, "repo": {"full_name": 7}}},
                      {"head": {"ref": "", "repo": {"full_name": ""}}}):
        result = subprocess.run(
            [jq, "-e", coordinate_filter], input=json.dumps(malformed),
            text=True, capture_output=True,
        )
        assert result.returncode != 0


def test_recorded_review_authorization_is_a_current_review_decision():
    jq = shutil.which("jq")
    if jq is None:
        raise AssertionError("jq is required to verify review authorization selection")
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    trust = workflow.split(
        "- name: Authenticate exact reviewed main candidate without candidate code", 1
    )[1].split("- uses: actions/checkout", 1)[0]
    selection_line = next(
        line.strip() for line in trust.splitlines()
        if line.strip().startswith('AUTHORIZATION_ID="$(jq -r --arg sha')
    )
    review_filter = selection_line.split(" '[", 1)[1].rsplit("' <<<", 1)[0]
    review_filter = "[" + review_filter
    sha = "a" * 40
    rows = [
        {"id": 1, "state": "APPROVED", "user": {"id": 11}, "commit_id": sha,
            "submitted_at": "2026-09-05T17:45:00Z"},
        {"id": 2, "state": "APPROVED", "user": {"id": 10}, "commit_id": sha,
            "submitted_at": "2026-09-05T17:46:00Z"},
        {"id": 3, "state": "DISMISSED", "user": {"id": 10}, "commit_id": sha,
            "submitted_at": "2026-09-05T17:47:00Z"},
    ]
    result = subprocess.run([
        jq, "-r", "--arg", "sha", sha,
        "--arg", "head", "2026-09-05T17:40:00Z",
        "--arg", "merged", "2026-09-05T18:00:00Z", review_filter,
    ], input=json.dumps(rows), text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "1"


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
            "required_evidence": "current_authenticated_native_connector_observation",
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
    from lib.release_baseline import expected_snapshot_tables, pre_migration_omissions
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
    names = expected_snapshot_tables(pre_migration_omissions()) or ()
    tables = {name: {"count": 0, "rows_sha256": "0" * 64} for name in names}
    dry = {
        "before": {"tables": tables, "pre_migration_omissions": pre_migration_omissions()},
        "after": {"tables": copy.deepcopy(tables), "pre_migration_omissions": pre_migration_omissions()},
        "table_deltas": {name: 0 for name in names},
    }
    receipt_path, dry_path, output = (tmp_path / name for name in ("receipt.json", "dry.json", "record.json"))
    receipt_path.write_text(json.dumps(receipt)); dry_path.write_text(json.dumps(dry))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["writer", "--receipt", str(receipt_path),
        "--dry-run-evidence", str(dry_path), "--output", str(output),
        "--candidate-sha", "a" * 40, "--reviewed-sha", "f" * 40,
        "--repository", "owner/stocks-agent", "--ci-workflow-run-id", "40",
        "--pr-ci-workflow-run-id", "39", "--authorization-kind", "owner_comment",
        "--authorization-id", "38",
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
    assert record["release_authorization"] == {"kind": "owner_comment", "id": 38,
        "pr_ci_workflow_run_id": 39}
    assert record["evidence_classes"]["owner_site"]["status"] == "pending"
    assert all(row["artifact_id"] == 60 for row in record["component_readbacks"])


def test_release_workflow_retains_and_restores_encrypted_backend_state_until_evidence_is_accepted():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    assert "-m scripts.restore_gateway_after_release_failure" in workflow
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
    assert "scripts.release_components --check-backend-transport" in workflow
    assert Path(".github/workflows/owner-dashboard-release-recovery.yml").is_file()
    assert workflow.index("scripts.release_components --check-backend-transport") < workflow.index("Create the candidate-bound GitHub Deployment")


def test_backend_release_does_not_claim_or_require_native_site_deployment():
    workflow = Path(".github/workflows/owner-dashboard-release.yml").read_text()
    writer = Path("scripts/write_protected_release_record.py").read_text()
    assert "--check-transport" not in workflow
    assert "--check-backend-transport" in workflow
    assert "owner-web-site" not in writer.split("component_readbacks", 1)[1].split("]", 1)[0]
    assert '"owner_site"' in writer
    rollout = Path("docs/rollouts/2026-09-06-market-wide-thematic-discovery-v1.md").read_text()
    assert "Fresh direct connector observations" in rollout
    assert "saved JSON copy" in rollout


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
    assert "-m scripts.finalize_protected_release" in workflow and "state=success" in finalizer
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
    assert "-m scripts.finalize_protected_release" in workflow
