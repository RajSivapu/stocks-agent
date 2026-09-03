from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "ops/backup/private-workflow.yml"
WORKER = ROOT / "ops/backup/r2-age-monitor/src/index.ts"
WRANGLER = ROOT / "ops/backup/r2-age-monitor/wrangler.jsonc"
LIFECYCLE = ROOT / "ops/backup/r2-lifecycle.json"


def test_private_workflow_has_no_artifact_or_unpinned_action_dependency():
    text = WORKFLOW.read_text()
    assert "actions/upload-artifact" not in text
    assert "permissions:\n  contents: read" in text
    assert "secrets.BACKUP_DATABASE_URL" in text
    assert "secrets.BACKUP_KEY_MATERIAL_JSON" in text
    assert "R2_SECRET_ACCESS_KEY" in text
    action_uses = re.findall(r"uses:\s*([^\s#]+)", text)
    assert action_uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in action_uses)
    assert "age-v1.3.2-linux-amd64.tar.gz" in text
    assert "cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10" in text


def test_lifecycle_caps_every_stock_agent_ciphertext_at_35_days():
    rules = json.loads(LIFECYCLE.read_text())["Rules"]
    assert rules == [{
        "ID": "expire-stock-agent-recovery-after-35-days",
        "Status": "Enabled",
        "Filter": {"Prefix": "stock-agent/"},
        "Expiration": {"Days": 35},
    }]


def test_monitor_has_no_supabase_or_portfolio_credential_and_only_lists_prefix():
    source = WORKER.read_text()
    config = WRANGLER.read_text()
    combined = source + config
    for forbidden in (
        "SUPABASE_URL",
        "DATABASE_URL",
        "SERVICE_ROLE",
        "EVIDENCE_SIGNING_KEY",
        "BACKUP_AGE_RECIPIENT",
    ):
        assert forbidden not in combined
    assert "BACKUP_PREFIX" in source
    assert ".list({ prefix" in source
    assert ".get(" not in source
    assert ".put(" not in source
    assert "36" in config
