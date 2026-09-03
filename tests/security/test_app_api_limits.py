from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from psycopg.types.json import Jsonb


OWNER_A = "11111111-1111-4111-8111-111111111111"
OWNER_B = "22222222-2222-4222-8222-222222222222"
IP_DIGEST = "a" * 64
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def reset_app_api_test_windows(tenant_database):
    tenant_database.execute("DELETE FROM app.app_api_rate_limits")
    tenant_database.execute("DELETE FROM app.app_api_audit_events")
    yield


@contextmanager
def as_owner(connection, owner=OWNER_A):
    with connection.transaction():
        connection.execute("SET LOCAL ROLE authenticated")
        connection.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner,))
        yield connection


def dispatch(connection, route, body, *, owner=OWNER_A, ip_digest=IP_DIGEST):
    with as_owner(connection, owner) as owner_connection:
        return owner_connection.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s::jsonb)",
            (route, uuid4(), ip_digest, Jsonb(body)),
        ).fetchone()[0]


def buy_request(ticker, key=None):
    return {
        "idempotency_key": str(key or uuid4()),
        "command": {
            "operation": "buy",
            "ticker": ticker,
            "quantity": "1",
            "fill_price": "10",
            "fees": "0",
            "cash_total": "10",
            "executed_on": "2026-09-02",
            "bucket": "unclassified",
        },
    }


def test_dispatch_rate_limits_previews_and_confirms_without_direct_rpc_bypass(tenant_database):
    preview = dispatch(tenant_database, "POST /portfolio/preview", buy_request("APIONE"))
    assert preview["ok"] is True
    receipt = preview["data"]
    confirmed = dispatch(tenant_database, "POST /portfolio/confirm", {
        "command_id": receipt["command_id"],
        "preview_digest": receipt["preview_digest"],
    })
    assert confirmed["ok"] is True
    assert confirmed["data"]["status"] == "applied"

    with as_owner(tenant_database) as owner_connection:
        with pytest.raises(Exception, match="permission denied"):
            owner_connection.execute(
                "SELECT api.preview_portfolio_command(%s::jsonb)",
                (Jsonb(buy_request("BYPASS")),),
            )


def test_rate_limit_is_database_backed_and_owner_and_client_scoped(tenant_database):
    for index in range(30):
        result = dispatch(
            tenant_database,
            "POST /portfolio/preview",
            buy_request(f"RL{index:02d}"),
        )
        assert result["ok"] is True
    limited = dispatch(tenant_database, "POST /portfolio/preview", buy_request("RLX"))
    assert limited["ok"] is False
    assert limited["error"]["code"] == "RATE_LIMITED"
    assert 1 <= limited["error"]["retry_after_seconds"] <= 60

    other_owner = dispatch(
        tenant_database,
        "POST /portfolio/preview",
        buy_request("RLB"),
        owner=OWNER_B,
    )
    assert other_owner["ok"] is True


def test_audit_receipts_store_no_body_financial_value_or_other_owner(tenant_database):
    request_id = uuid4()
    with as_owner(tenant_database) as owner_connection:
        result = owner_connection.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s::jsonb)",
            (
                "POST /portfolio/preview",
                request_id,
                IP_DIGEST,
                Jsonb(buy_request("AUDIT")),
            ),
        ).fetchone()[0]
    assert result["ok"] is True
    row = tenant_database.execute(
        "SELECT owner_id, route, result_code, request_id FROM app.app_api_audit_events WHERE request_id = %s",
        (request_id,),
    ).fetchone()
    assert row == (UUID(OWNER_A), "POST /portfolio/preview", "PREVIEWED", request_id)

    columns = {
        row[0]
        for row in tenant_database.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'app' AND table_name = 'app_api_audit_events'
            """
        ).fetchall()
    }
    assert not columns.intersection({"body", "payload", "financial_value", "ticker"})


def test_unknown_routes_bad_client_digest_and_caller_authority_fail_closed(tenant_database):
    unknown = dispatch(tenant_database, "POST /not-real", {})
    assert unknown == {"ok": False, "error": {"code": "ROUTE_NOT_ALLOWED"}}
    invalid_digest = dispatch(
        tenant_database,
        "POST /portfolio/preview",
        buy_request("BADIP"),
        ip_digest="not-a-digest",
    )
    assert invalid_digest == {"ok": False, "error": {"code": "INVALID_REQUEST"}}
    authority = dispatch(
        tenant_database,
        "POST /portfolio/preview",
        {**buy_request("AUTHX"), "owner_id": OWNER_B},
    )
    assert authority == {"ok": False, "error": {"code": "INVALID_REQUEST"}}


def test_app_api_runtime_has_no_privileged_database_credential_dependency():
    source = "\n".join(
        path.read_text()
        for path in (ROOT / "supabase/functions/app-api").glob("*.ts")
    )
    assert "SUPABASE_SERVICE_ROLE_KEY" not in source
    assert "DATABASE_URL" not in source
    assert "SUPABASE_ANON_KEY" in source
