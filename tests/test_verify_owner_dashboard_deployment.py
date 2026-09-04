import json

import pytest

from scripts import verify_owner_dashboard_deployment as verify


ORIGIN = "https://stocks.example.com"
API_URL = "https://hlxpxbxhqctwsqizwjjy.supabase.co/functions/v1/owner-dashboard-api"


def envelope(data, *, freshness="fresh", market_state="regular", data_as_of="2026-09-03T20:00:00.000Z"):
    return {
        "contract_version": 1,
        "request_id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22",
        "generated_at": "2026-09-03T20:01:00.000Z",
        "data_as_of": data_as_of,
        "freshness": freshness,
        "market_state": market_state,
        "data": data,
    }


def test_canary_routes_are_get_only_and_bounded():
    assert verify.CANARY_ROUTES == (
        "/v1/today", "/v1/portfolio", "/v1/companion", "/v1/alerts", "/v1/runs", "/v1/system",
    )
    assert verify.CANARY_METHOD == "GET"


def test_api_url_and_origin_must_be_exact_https_boundaries():
    assert verify.validate_api_boundary(API_URL, ORIGIN) == (API_URL, ORIGIN)
    for api_url, origin in [
        (API_URL + "/v1/today", ORIGIN),
        ("http://hlxpxbxhqctwsqizwjjy.supabase.co/functions/v1/owner-dashboard-api", ORIGIN),
        (API_URL, ORIGIN + "/path"),
    ]:
        with pytest.raises(ValueError):
            verify.validate_api_boundary(api_url, origin)


def test_owner_payload_requires_immutable_boundaries_and_receipt_fields():
    payloads = {
        route: envelope({"boundaries": {"owner_only": True, "suggestion_only": True, "friend_invitations": "disabled", "brokerage_authority": "none"}})
        for route in verify.CANARY_ROUTES
    }
    payloads["/v1/today"]["data"].update({"portfolio": {"data_as_of": "2026-09-03T20:00:00.000Z", "market_state": "as_of_close", "price_sources": ["yahoo-chart"]}})
    payloads["/v1/runs"]["data"].update({"runs": [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "completed"}]})
    result = verify.validate_owner_payloads(payloads)
    assert result["route_count"] == 6
    assert result["financial_write_routes"] == 0
    assert result["brokerage_authority"] == "none"


def test_owner_payload_rejects_an_unsupported_send_claim():
    payloads = {route: envelope({}) for route in verify.CANARY_ROUTES}
    payloads["/v1/today"] = envelope({
        "boundaries": verify.BOUNDARIES,
        "portfolio": {"data_as_of": None, "market_state": "unknown", "price_sources": []},
    })
    payloads["/v1/runs"] = envelope({"runs": [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "completed"}]})
    payloads["/v1/alerts"] = envelope({"alerts": [{"state": "delivered", "telegram_message_ids": []}]})
    with pytest.raises(RuntimeError, match="Telegram"):
        verify.validate_owner_payloads(payloads)


def test_http_canary_uses_only_get_and_checks_unauthenticated_denial():
    calls = []

    def requester(method, url, headers):
        calls.append((method, url, headers))
        if "authorization" not in {key.lower() for key in headers}:
            return 401, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "unauthorized"}}).encode()
        route = url.removeprefix(API_URL)
        data = {"boundaries": {"owner_only": True, "suggestion_only": True, "friend_invitations": "disabled", "brokerage_authority": "none"}}
        if route == "/v1/today": data["portfolio"] = {"data_as_of": None, "market_state": "unknown", "price_sources": [], "holdings": []}
        if route == "/v1/portfolio": data["holdings"] = []
        if route == "/v1/alerts": data["alerts"] = []
        if route == "/v1/runs": data["runs"] = [{
            "id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "kind": "on-demand",
            "status": "completed", "finished_at": "2026-09-03T20:00:00.000Z",
            "data_as_of": None, "evaluation_count": 0, "suggestion_count": 0,
            "publication_status": None,
        }]
        if route.startswith("/v1/runs/"):
            data = {
                "run": {
                    "id": route.rsplit("/", 1)[-1], "kind": "on-demand",
                    "status": "completed", "finished_at": "2026-09-03T20:00:00.000Z",
                    "data_as_of": None, "evaluation_count": 0, "suggestion_count": 0,
                    "publication_status": None,
                },
                "request_receipts": [{"request_id": "one"}], "evaluations": [],
                "write_counts": {"suggestions": 0}, "telegram_message_ids": [],
                "incomplete_stages": [],
            }
        return 200, {"access-control-allow-origin": ORIGIN, "cache-control": "no-store"}, json.dumps(envelope(data, freshness="unavailable", market_state="unknown", data_as_of=None)).encode()

    def source_reader(run_id):
        return {
            "database_user": verify.RUNTIME_ROLE,
            "transaction_read_only": "on",
            "run": {
                "id": run_id,
                "kind": "on-demand",
                "status": "completed",
                "finished_at": "2026-09-03T20:00:00.000Z",
                "data_as_of": None,
                "write_counts": {"suggestions": 0},
                "telegram_message_ids": [],
            },
            "gateway_request_count": 1,
            "evaluation_count": 0,
            "suggestion_count": 0,
            "alerts": [],
            "policy_version": None,
            "holdings": [],
            "portfolio_data_as_of": None,
        }

    receipt = verify.run_http_canary(
        API_URL, ORIGIN, "owner-token", requester=requester, source_reader=source_reader,
    )
    assert receipt["unauthenticated_status"] == 401
    assert receipt["owner_route_count"] == 7
    assert receipt["source_reconciliation"] == "verified"
    assert receipt["source_database_role"] == verify.RUNTIME_ROLE
    assert {method for method, _url, _headers in calls} == {"GET"}


def test_source_reconciliation_rejects_unsupported_run_send_policy_and_price_claims():
    run_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    payloads = {route: envelope({}) for route in verify.CANARY_ROUTES}
    holding = {
        "ticker": "VTI", "shares": "2", "average_cost": "100", "price": "110",
        "price_as_of": "2026-09-03T20:00:00.000Z", "price_source": "yahoo-chart",
    }
    payloads["/v1/today"] = envelope({
        "boundaries": verify.BOUNDARIES,
        "portfolio": {
            "data_as_of": "2026-09-03T20:00:00.000Z", "market_state": "as_of_close",
            "price_sources": ["yahoo-chart"], "holdings": [holding],
        },
    })
    payloads["/v1/portfolio"] = envelope({"holdings": [holding]})
    payloads["/v1/runs"] = envelope({"runs": [{
        "id": run_id, "kind": "post-market", "status": "completed",
        "finished_at": "2026-09-03T20:00:00.000Z", "data_as_of": "2026-09-03T20:00:00.000Z",
        "evaluation_count": 1, "suggestion_count": 1, "publication_status": "delivered",
    }]})
    payloads["/v1/alerts"] = envelope({"alerts": [{
        "id": "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "state": "delivered",
        "telegram_message_ids": [123], "rendered_hash": "a" * 64, "template_version": "3",
        "event_status": "triggered",
    }]})
    payloads["/v1/system"] = envelope({"policy_version": 17})
    detail = envelope({
        "run": payloads["/v1/runs"]["data"]["runs"][0],
        "request_receipts": [{"request_id": "one"}],
        "evaluations": [{"id": "1"}],
        "write_counts": {"suggestions": 1},
        "telegram_message_ids": [123],
        "incomplete_stages": [],
    })
    source = {
        "database_user": verify.RUNTIME_ROLE,
        "transaction_read_only": "on",
        "run": {
            "id": run_id, "kind": "post-market", "status": "completed",
            "finished_at": "2026-09-03T20:00:00.000Z", "data_as_of": "2026-09-03T20:00:00.000Z",
            "write_counts": {"suggestions": 1}, "telegram_message_ids": [123],
        },
        "gateway_request_count": 1,
        "evaluation_count": 1,
        "suggestion_count": 1,
        "alerts": [{
            "id": "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "delivered",
            "telegram_message_ids": [123], "rendered_hash": "a" * 64,
            "template_version": "3", "event_status": "triggered",
        }],
        "policy_version": 17,
        "holdings": [{
            "ticker": "VTI", "shares": "2", "average_cost": "100", "price": "110",
            "price_as_of": "2026-09-03T20:00:00.000Z", "price_source": "yahoo-chart",
        }],
        "portfolio_data_as_of": "2026-09-03T20:00:00.000Z",
    }

    receipt = verify.reconcile_source_receipts(payloads, detail, source, run_id)
    assert receipt == {"status": "verified", "database_role": verify.RUNTIME_ROLE, "claims_checked": 5}

    for path, value in [
        (("run", "write_counts"), {"suggestions": 2}),
        (("alerts", 0, "telegram_message_ids"), [999]),
        (("policy_version",), 18),
        (("holdings", 0, "price"), "109"),
    ]:
        changed = json.loads(json.dumps(source))
        target = changed
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        with pytest.raises(RuntimeError, match="source receipt"):
            verify.reconcile_source_receipts(payloads, detail, changed, run_id)


def test_source_reconciliation_requires_scoped_read_only_database_role():
    source = {
        "database_user": "postgres", "transaction_read_only": "off", "run": {},
        "gateway_request_count": 0, "evaluation_count": 0, "suggestion_count": 0,
        "alerts": [], "policy_version": None, "holdings": [], "portfolio_data_as_of": None,
    }
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts({}, {}, source, "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22")


def test_source_database_url_must_use_the_scoped_session_pooler_login():
    valid = (
        "postgresql://stock_agent_dashboard_runtime.hlxpxbxhqctwsqizwjjy:"
        "dashboard-password-longer-than-24@aws-1-us-west-2.pooler.supabase.com:5432/postgres"
    )
    assert verify.validate_source_database_url(valid, API_URL) == valid
    for invalid in [
        valid.replace("stock_agent_dashboard_runtime", "postgres"),
        valid.replace(":5432/", ":6543/"),
        valid.replace("pooler.supabase.com", "example.com"),
    ]:
        with pytest.raises(ValueError, match="source database"):
            verify.validate_source_database_url(invalid, API_URL)


def test_source_timestamp_normalization_fails_closed():
    assert verify.normalize_receipt_timestamp("2026-09-03T20:00:00.123456+00:00") == "2026-09-03T20:00:00.123Z"
    assert verify.normalize_receipt_timestamp("not-a-date") is None
    assert verify.normalize_receipt_timestamp(None) is None


def test_ephemeral_owner_session_uses_admin_link_without_sending_email_or_returning_secrets():
    service_key = "sb_secret_" + "s" * 40
    public_key = "sb_publishable_" + "p" * 32
    access_token = "eyJ" + "t" * 80
    calls = []

    def requester(method, url, headers, body):
        calls.append((method, url, headers, body))
        if url.endswith("/auth/v1/admin/generate_link"):
            return 200, json.dumps({"properties": {"hashed_token": "hash-token-value"}}).encode()
        return 200, json.dumps({"access_token": access_token}).encode()

    token = verify.obtain_ephemeral_owner_access_token(
        "https://hlxpxbxhqctwsqizwjjy.supabase.co",
        "owner@example.com",
        ORIGIN,
        service_key,
        public_key,
        requester=requester,
    )
    assert token == access_token
    assert [call[0] for call in calls] == ["POST", "POST"]
    assert json.loads(calls[0][3]) == {
        "type": "magiclink", "email": "owner@example.com", "options": {"redirect_to": ORIGIN},
    }
    assert json.loads(calls[1][3]) == {"type": "magiclink", "token_hash": "hash-token-value"}
    assert service_key not in calls[0][3].decode()
    assert public_key not in calls[1][3].decode()


def test_ephemeral_owner_session_errors_are_bounded():
    def requester(_method, _url, _headers, _body):
        return 500, b'{"message":"private auth detail"}'

    with pytest.raises(RuntimeError, match="ephemeral owner session") as error:
        verify.obtain_ephemeral_owner_access_token(
            "https://hlxpxbxhqctwsqizwjjy.supabase.co", "owner@example.com", ORIGIN,
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32, requester=requester,
        )
    assert "private auth detail" not in str(error.value)
