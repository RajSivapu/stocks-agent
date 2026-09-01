import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = (
    ROOT / "sql" / "schema.sql",
    ROOT / "sql" / "migrations" / "20260901_reliable_stock_agent.sql",
)


def test_privileged_portfolio_rpcs_are_schema_qualified_and_service_role_only():
    for path in SQL_FILES:
        sql = path.read_text()
        for function in ("apply_portfolio_command", "cancel_portfolio_command"):
            signature = f"public.{function}(UUID, BIGINT, BIGINT)"
            assert f"CREATE OR REPLACE FUNCTION public.{function}(" in sql
            assert f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, anon, authenticated;" in sql
            assert f"GRANT EXECUTE ON FUNCTION {signature} TO service_role;" in sql


def test_live_rpc_verifier_cannot_be_disabled_with_python_optimization():
    path = ROOT / "scripts" / "verify_portfolio_command_rpc.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))
