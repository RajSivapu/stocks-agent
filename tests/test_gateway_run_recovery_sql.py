from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261022_durable_run_gateway_recovery.sql"
SCHEMA = ROOT / "sql/schema.sql"


def test_gateway_run_recovery_keeps_the_hourly_limit_without_a_lifetime_run_lockout():
    migration = MIGRATION.read_text()
    final_claim = SCHEMA.read_text().rsplit(
        "CREATE OR REPLACE FUNCTION public.claim_market_gateway_request(", 1
    )[1]

    for sql in (migration, final_claim):
        assert "created_at>=now()-interval '1 hour')>=100" in sql
        assert "WHERE run_id=v_stored_run)>=20" not in sql
        assert "IF v_request.status='failed' AND p_operation='record_report' THEN" in sql
        assert (
            "GRANT EXECUTE ON FUNCTION public.claim_market_gateway_request(UUID, TEXT, UUID) "
            "TO service_role;"
        ) in sql
