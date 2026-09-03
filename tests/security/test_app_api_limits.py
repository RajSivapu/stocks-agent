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


def fresh_step_up(connection, owner=OWNER_A):
    connection.execute("RESET ROLE")
    challenge_id = uuid4()
    receipt_id = uuid4()
    session_digest = "f" * 64
    connection.execute(
        """
        INSERT INTO app.account_step_up_challenges(
          id, owner_id, initial_session_digest, expires_at
        ) VALUES (%s, %s, decode(%s, 'hex'), now() + interval '10 minutes')
        """,
        (challenge_id, owner, "e" * 64),
    )
    connection.execute(
        """
        INSERT INTO app.account_step_up_receipts(
          id, owner_id, challenge_id, session_digest, authenticated_at, expires_at
        ) VALUES (%s, %s, %s, decode(%s, 'hex'), now(), now() + interval '5 minutes')
        """,
        (receipt_id, owner, challenge_id, session_digest),
    )
    return str(receipt_id), session_digest


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


def test_connection_routes_use_authenticated_owner_and_require_completed_handshake(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        tenant_database.execute(
            """
            INSERT INTO app.user_consents(owner_id, document_version, source)
            VALUES (%s, 'provider-data-v1', 'web')
            ON CONFLICT (owner_id, document_version) DO NOTHING
            """,
            (OWNER_A,),
        )
        created = dispatch(tenant_database, "POST /connections/create", {
            "provider": "claude",
            "consent_version": "provider-data-v1",
            "inbound_token_digest": "e" * 64,
        })
        assert created["ok"] is True
        connection_id = created["data"]["connection_id"]
        receipt_id, session_digest = fresh_step_up(tenant_database)
        handshake = dispatch(tenant_database, "POST /connections/handshake", {
            "connection_id": connection_id,
            "trigger_url": "https://api.anthropic.com/v1/claude_code/routines/trig_APITEST/fire",
            "trigger_token": "api-route-routine-token-12345",
            "step_up_receipt_id": receipt_id,
            "session_digest": session_digest,
        })
        assert handshake["ok"] is True
        assert handshake["data"]["status"] == "testing"
        premature = dispatch(tenant_database, "POST /connections/activate", {
            "connection_id": connection_id,
        })
        assert premature == {"ok": False, "error": {"code": "NOT_FOUND"}}
        cross_owner = dispatch(
            tenant_database,
            "POST /connections/revoke",
            {"connection_id": connection_id},
            owner=OWNER_B,
        )
        assert cross_owner == {"ok": False, "error": {"code": "NOT_FOUND"}}
