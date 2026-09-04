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
        if route == "/v1/today": data["portfolio"] = {"data_as_of": None, "market_state": "unknown", "price_sources": []}
        if route == "/v1/runs": data["runs"] = [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "completed"}]
        if route.startswith("/v1/runs/"):
            data = {"run": {"id": route.rsplit("/", 1)[-1], "status": "completed", "finished_at": "2026-09-03T20:00:00.000Z"}, "write_counts": {"suggestions": 0}, "incomplete_stages": []}
        return 200, {"access-control-allow-origin": ORIGIN, "cache-control": "no-store"}, json.dumps(envelope(data, freshness="unavailable", market_state="unknown", data_as_of=None)).encode()

    receipt = verify.run_http_canary(API_URL, ORIGIN, "owner-token", requester=requester)
    assert receipt["unauthenticated_status"] == 401
    assert receipt["owner_route_count"] == 7
    assert {method for method, _url, _headers in calls} == {"GET"}
