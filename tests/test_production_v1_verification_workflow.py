from pathlib import Path

import yaml


def test_production_v1_verification_is_manual_exact_main_and_read_only():
    path = Path(".github/workflows/production-v1-verification.yml")
    raw = path.read_text()
    workflow = yaml.safe_load(raw)

    assert "workflow_dispatch:" in raw and "schedule:" not in raw
    job = workflow["jobs"]["verify"]
    assert job["environment"] == "owner-dashboard-production"
    assert job["env"]["PGSSLMODE"] == "verify-full"
    assert "refs/heads/main" in raw and "GITHUB_SHA" in raw
    assert "Owner dashboard verification" in raw
    assert ".github/workflows/owner-dashboard-ci.yml" in raw
    assert "verify_personal_stock_agent_v1.py" in raw
    assert "RELEASE_READONLY_DATABASE_URL: ${{ secrets.RELEASE_READONLY_DATABASE_URL }}" in raw
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in raw
    assert "production-v1-verification-${{ github.run_id }}-${{ github.run_attempt }}" in raw
    assert "retention-days: 90" in raw
    for forbidden in (
        "SUPABASE_ACCESS_TOKEN",
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPAVISOR_SESSION_URL",
        "deploy_owner_dashboard_api.py",
        "collect_market_intelligence.py",
        "market_gateway.py",
    ):
        assert forbidden not in raw
    for action in (
        "actions/checkout@",
        "actions/setup-python@",
        "actions/setup-node@",
        "actions/upload-artifact@",
    ):
        pinned = [line for line in raw.splitlines() if action in line]
        assert pinned and all(len(line.rsplit("@", 1)[1].strip()) == 40 for line in pinned)

