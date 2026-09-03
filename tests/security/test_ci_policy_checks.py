from pathlib import Path

import pytest

from scripts import ci_policy_checks as policy


def test_secret_scanner_rejects_real_credentials_without_returning_the_value():
    credential = "sb_secret_" + "A" * 32
    with pytest.raises(policy.PolicyFailure) as error:
        policy.scan_text(Path("src/config.ts"), f'const key = "{credential}";')
    assert credential not in str(error.value)
    assert "src/config.ts" in str(error.value)


def test_secret_scanner_allows_documented_placeholders_and_publishable_keys():
    policy.scan_text(Path("config/example.txt"), "SUPABASE_SERVICE_ROLE_KEY=replace-me")
    policy.scan_text(Path("src/config.ts"), "sb_publishable_example_only")


def test_secret_scanner_rejects_a_literal_sensitive_assignment():
    with pytest.raises(policy.PolicyFailure, match="literal secret assignment"):
        policy.scan_text(
            Path("config/runtime.txt"),
            'TELEGRAM_' + 'BOT_TOKEN="real-token-value"',
        )


def test_secret_scanner_rejects_private_key_material_and_tracked_env_files():
    with pytest.raises(policy.PolicyFailure):
        policy.scan_text(Path("config/.env.production"), "SAFE=value")
    with pytest.raises(policy.PolicyFailure):
        policy.scan_text(Path("notes.txt"), "-----BEGIN " + "PRIVATE KEY-----")


def test_sql_lint_parses_every_supplied_file(tmp_path):
    valid = tmp_path / "valid.sql"
    invalid = tmp_path / "invalid.sql"
    valid.write_text("SELECT 1;", encoding="utf-8")
    invalid.write_text("SELECT FROM ;", encoding="utf-8")
    assert policy.lint_sql_files([valid]) == 1
    with pytest.raises(policy.PolicyFailure, match="invalid.sql"):
        policy.lint_sql_files([invalid])


def test_repository_sql_selection_includes_canonical_and_supabase_migrations():
    selected = {path.relative_to(policy.ROOT).as_posix() for path in policy.repository_sql_files()}
    assert "sql/schema.sql" in selected
    assert "supabase/migrations/20260831_legacy_baseline.sql" in selected
    assert "supabase/migrations/20260910000000_retention_recovery.sql" in selected
