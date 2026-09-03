from __future__ import annotations

import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
PINNED_USE = re.compile(r"^\s*-?\s*uses:\s*[^\s@]+@[0-9a-f]{40}\s*$", re.MULTILINE)
ANY_USE = re.compile(r"^\s*-?\s*uses:\s*\S+\s*$", re.MULTILINE)
MODEL_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "XAI_API_KEY")


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _yaml(name: str) -> dict:
    return yaml.load(_text(name), Loader=yaml.BaseLoader)


def test_all_actions_are_immutable_and_workflows_are_least_privilege():
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert {path.name for path in files} == {
        "ci.yml", "deploy-production.yml", "deploy-staging.yml",
    }
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert len(ANY_USE.findall(text)) == len(PINNED_USE.findall(text))
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        assert parsed["permissions"] == {"contents": "read"}
        assert "persist-credentials: false" in text
        assert not any(key in text for key in MODEL_KEYS)
        assert "upload-artifact" not in text
        assert "BACKUP_DATABASE_URL" not in text
        assert "R2_SECRET_ACCESS_KEY" not in text


def test_workflow_shell_never_interpolates_untrusted_context_or_secrets_directly():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job in parsed["jobs"].values():
            for step in job.get("steps", []):
                command = step.get("run", "")
                assert "${{ inputs." not in command
                assert "${{ github.event.inputs" not in command
                assert "${{ secrets." not in command
                assert "${{ needs." not in command


def test_deployment_secrets_are_scoped_to_the_step_that_uses_them():
    for name in ("deploy-staging.yml", "deploy-production.yml"):
        parsed = _yaml(name)
        for job in parsed["jobs"].values():
            for value in job.get("env", {}).values():
                assert "secrets." not in value


def test_pull_requests_run_tests_without_any_secret_context():
    text = _text("ci.yml")
    parsed = _yaml("ci.yml")
    assert "pull_request" in parsed["on"]
    assert "secrets." not in text
    for required in (
        "scan-secrets", "dependency-audit", "sql-lint", "migration-current",
        "fresh-schema", "surface-allowlist", "build-output-scan", "npm run test:all",
        "playwright install --with-deps chromium", "verify_schema_parity.py --verify",
    ):
        assert required in text
    assert "npm --workspace apps/web run lint" in (ROOT / "scripts/test_all.sh").read_text(encoding="utf-8")


def test_staging_is_after_ci_and_deploys_only_the_four_release_functions():
    text = _text("deploy-staging.yml")
    parsed = _yaml("deploy-staging.yml")
    assert "pull_request" not in parsed["on"]
    assert "workflow_run" in parsed["on"]
    assert "environment: staging" in text
    assert "conclusion == 'success'" in text
    for function in ("app-api", "telegram-portfolio", "agent-gateway", "run-scheduler"):
        assert f"functions deploy {function}" in text
    assert "functions deploy market-briefing-gateway" not in text
    assert "deploy_and_verify.py staging-preflight" in text
    assert "deploy_and_verify.py staging-verify" in text
    assert "create_security_test_users.py" in text
    assert "provision_operator.py" in text
    assert "write_release_marker.py" in text
    assert "E2E_LIVE: 1" in text
    assert "npm --workspace apps/web run test:e2e" in text
    assert text.index("provision-staging-operator") < text.index("create-security-test-users")
    assert text.index("create-security-test-users") < text.index("live-browser-security-acceptance")
    assert text.index("live-browser-security-acceptance") < text.index("staging-verify")
    assert "supabase/migrations" in (ROOT / "scripts/verify_schema_parity.py").read_text(encoding="utf-8")
    assert not (ROOT / "sql/migrations").exists()


def test_production_is_manual_protected_and_cannot_enable_invitations():
    text = _text("deploy-production.yml")
    parsed = _yaml("deploy-production.yml")
    assert set(parsed["on"]) == {"workflow_dispatch"}
    assert "environment: production" in text
    assert "DEPLOY PRODUCTION" in text
    assert "TRIGGERS PAUSED" in text
    assert "deploy_and_verify.py production-preflight" in text
    assert "deploy_and_verify.py production-verify" in text
    assert "--max-backup-hours 36" in text
    assert "--max-restore-days 30" in text
    assert "invite_user.py" not in text
    assert "enable_signup" not in text
    assert "rollback-ref" in text
    assert "operation=verify" in text
    assert "production-health" in text
    assert "write_release_marker.py" in text
    assert '--commit "$REQUESTED_COMMIT"' in text
    assert "if: inputs.operation == 'verify'" in text
    assert "owner-cutover" in text
    assert "CUTOVER OWNER" in text
    assert "migrate_single_owner_to_tenant.py" in text
    assert "migration repair 20260905500000 --status applied" in text
    assert text.index("owner-cutover-preflight") < text.index("apply-owner-foundation")
    assert text.index("apply-owner-foundation") < text.index("backfill-owner-data")
    assert text.index("backfill-owner-data") < text.index("apply-remaining-owner-migrations")


def test_static_hosting_is_spa_only_and_has_no_server_binding():
    config = json.loads((ROOT / "apps/web/wrangler.jsonc").read_text(encoding="utf-8"))
    assert config["workers_dev"] is False
    assert config["assets"] == {
        "directory": "./dist",
        "not_found_handling": "single-page-application",
        "run_worker_first": False,
    }
    assert "main" not in config
    assert "vars" not in config


def test_deployment_docs_name_pause_backup_restore_and_rollback_gates():
    deployment = (ROOT / "docs/runbooks/deployment.md").read_text(encoding="utf-8").lower()
    rollback = (ROOT / "docs/runbooks/rollback.md").read_text(encoding="utf-8").lower()
    for phrase in ("36 hours", "30 days", "triggers paused", "owner-only smoke"):
        assert phrase in deployment
    for phrase in ("never destructively down-migrate", "triggers remain paused", "rollback reference"):
        assert phrase in rollback
