import json
import hashlib
import sys

import pytest

from scripts import verify_owner_dashboard_deployment as verify


ORIGIN = "https://stocks.example.com"
API_URL = "https://hlxpxbxhqctwsqizwjjy.supabase.co/functions/v1/owner-dashboard-api"
EVIDENCE_AUTHORITY = {
    "status": "verified",
    "connection_id": "a" * 64,
    "read_only": True,
    "isolated_guard": False,
}


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


def v1_chain(run_id):
    event_id = "22222222-2222-4222-8222-222222222222"
    packet_id = "33333333-3333-4333-8333-333333333333"
    report_id = "44444444-4444-4444-8444-444444444444"
    event_canonical = {"title": "event"}
    ranking_canonical = {"event_id": event_id, "rank": 1}
    packet_canonical = {"packet": "evidence"}
    report_canonical = {"title": "Market research", "summary": "research"}
    rendered_text = "Suggestion only."
    publication_canonical = {"report_id": report_id, "status": "delivered", "telegram_message_ids": [7]}
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "evidence_database_user": verify.EVIDENCE_ROLE,
        "evidence_transaction_read_only": "on",
        "evidence_authority": dict(EVIDENCE_AUTHORITY),
        "intelligence_runs": [{"id": run_id}],
        "intelligence_events": [{"id": event_id, "run_id": run_id, "canonical": event_canonical, "content_hash": digest(event_canonical)}],
        "intelligence_rankings": [{"id": "55555555-5555-4555-8555-555555555555", "run_id": run_id, "event_id": event_id, "canonical": ranking_canonical, "content_hash": digest(ranking_canonical)}],
        "intelligence_packets": [{"id": packet_id, "run_id": run_id, "canonical": packet_canonical, "packet_hash": digest(packet_canonical), "candidate_count": 1, "evidence_count": 1}],
        "reports": [{
            "id": report_id, "run_id": run_id, "packet_id": packet_id,
            "market_date": "2026-09-03", "kind": "morning",
            "canonical": report_canonical, "report_hash": digest(report_canonical),
            "rendered_text": rendered_text,
            "rendered_hash": hashlib.sha256(rendered_text.encode()).hexdigest(),
            "created_at": "2026-09-03T20:00:00.000Z",
        }],
        "report_publications": [{"report_id": report_id, "run_id": run_id, "status": "delivered", "telegram_message_ids": [7], "telegram_accepted_at": "2026-09-03T20:00:00.000Z", "canonical": publication_canonical}],
        "canonical_records": [],
    }


def v1_report_view(run_id):
    return verify.report_summary_source_view(v1_chain(run_id)["reports"][0])


def v1_public_report_source(run_id):
    row = v1_chain(run_id)["reports"][0]
    return {field: row[field] for field in verify.REPORT_PUBLIC_SOURCE_FIELDS}


def test_canary_routes_are_get_only_and_bounded():
    assert verify.CANARY_ROUTES == (
        "/v1/today", "/v1/portfolio", "/v1/ideas", "/v1/companion", "/v1/alerts",
        "/v1/runs", "/v1/system", "/v1/intelligence", "/v1/reports",
    )
    assert verify.CANARY_METHOD == "GET"


def test_intelligence_projection_receipt_distinguishes_a_real_null_identity():
    base = {
        "projection_present": True,
        "projection_type": "object",
        "run_id_present": True,
        "run_id_type": "null",
        "intelligence_run_id": None,
    }
    assert verify.validate_intelligence_projection_receipt(base) is None

    run_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    available = dict(base, run_id_type="string", intelligence_run_id=run_id)
    assert verify.validate_intelligence_projection_receipt(available) == run_id

    for changed in (
        dict(base, projection_present=False, projection_type=None),
        dict(base, projection_type="array"),
        dict(base, run_id_present=False),
        dict(base, run_id_type="number", intelligence_run_id="1"),
        dict(base, run_id_type="null", intelligence_run_id=run_id),
        dict(base, run_id_type="string", intelligence_run_id=None),
        dict(base, run_id_type="string", intelligence_run_id="not-a-uuid"),
        {**base, "unexpected": True},
    ):
        with pytest.raises(RuntimeError, match="projection receipt"):
            verify.validate_intelligence_projection_receipt(changed)


def test_deployment_auth_configuration_requires_link_templates():
    assert verify.validate_deployment_auth_configuration({
        "mailer_otp_length": 6,
        "mailer_templates_magic_link_content": "{{ .ConfirmationURL }}",
        "mailer_templates_recovery_content": "{{ .ConfirmationURL }}",
    }) == {"status": "verified", "otp_length": 6, "email_flow": "link", "recovery_flow": "link"}

    with pytest.raises(RuntimeError, match="exactly"):
        verify.validate_deployment_auth_configuration({
            "mailer_otp_length": 6,
            "mailer_templates_magic_link_content": "{{ .ConfirmationURL }}",
            "mailer_templates_recovery_content": "{{ .ConfirmationURL }}",
            "secret": "must-not-be-accepted",
        })


@pytest.mark.parametrize("receipt_contents", [None, "not JSON"])
def test_deployment_main_rejects_absent_or_malformed_auth_receipt_before_network_or_canary(
    tmp_path, monkeypatch, receipt_contents,
):
    receipt_path = tmp_path / "auth-email-otp.json"
    if receipt_contents is not None:
        receipt_path.write_text(receipt_contents)
    network_calls = []
    canary_calls = []

    def no_network(*_args, **_kwargs):
        network_calls.append(True)
        raise AssertionError("network must not be reached")

    def no_canary(*_args, **_kwargs):
        canary_calls.append(True)
        raise AssertionError("canary must not be reached")

    monkeypatch.setattr(verify, "urlopen", no_network)
    monkeypatch.setattr(verify, "run_http_canary", no_canary)
    monkeypatch.setattr(sys, "argv", [
        "verify_owner_dashboard_deployment.py",
        "--api-url", API_URL,
        "--origin", ORIGIN,
        "--auth-config-receipt", str(receipt_path),
    ])

    with pytest.raises(RuntimeError, match="Auth configuration receipt"):
        verify.main()

    assert network_calls == []
    assert canary_calls == []


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
        route: envelope({})
        for route in verify.CANARY_ROUTES
    }
    payloads["/v1/today"]["data"]["boundaries"] = dict(verify.BOUNDARIES)
    payloads["/v1/system"]["data"]["boundaries"] = dict(verify.BOUNDARIES)
    payloads["/v1/intelligence"]["data"]["boundaries"] = {
        "research_only": True,
        "execution_disabled": True,
        "valuation_unavailable": True,
    }
    payloads["/v1/today"]["data"].update({"portfolio": {"data_as_of": "2026-09-03T20:00:00.000Z", "market_state": "as_of_close", "price_sources": ["yahoo-chart"]}})
    payloads["/v1/runs"]["data"].update({"runs": [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "completed"}]})
    result = verify.validate_owner_payloads(payloads)
    assert result["route_count"] == 9
    assert result["financial_write_routes"] == 0
    assert result["brokerage_authority"] == "none"


def test_owner_payload_requires_each_route_specific_boundary_receipt():
    payloads = {
        route: envelope({})
        for route in verify.CANARY_ROUTES
    }
    payloads["/v1/today"]["data"].update({
        "boundaries": dict(verify.BOUNDARIES),
        "portfolio": {
            "data_as_of": None, "market_state": "unknown", "price_sources": [],
        },
    })
    payloads["/v1/system"]["data"]["boundaries"] = dict(verify.BOUNDARIES)
    payloads["/v1/intelligence"]["data"]["boundaries"] = {
        "research_only": True,
        "execution_disabled": True,
        "valuation_unavailable": True,
    }
    payloads["/v1/runs"]["data"]["runs"] = [{"status": "completed"}]

    for route in ("/v1/today", "/v1/system", "/v1/intelligence"):
        missing = json.loads(json.dumps(payloads))
        del missing[route]["data"]["boundaries"]
        with pytest.raises(RuntimeError, match="boundary"):
            verify.validate_owner_payloads(missing)


@pytest.mark.parametrize(
    ("route", "field", "numeric_value"),
    tuple(
        (route, field, numeric_value)
        for route, expected in verify.ROUTE_BOUNDARIES.items()
        for field, value in expected.items()
        if isinstance(value, bool)
        for numeric_value in (1, 1.0)
    ),
)
def test_owner_payload_boundary_booleans_are_type_exact(
    route,
    field,
    numeric_value,
):
    payloads = {
        route_name: envelope({})
        for route_name in verify.CANARY_ROUTES
    }
    payloads["/v1/today"]["data"].update({
        "boundaries": dict(verify.BOUNDARIES),
        "portfolio": {
            "data_as_of": None, "market_state": "unknown", "price_sources": [],
        },
    })
    payloads["/v1/system"]["data"]["boundaries"] = dict(verify.BOUNDARIES)
    payloads["/v1/intelligence"]["data"]["boundaries"] = dict(
        verify.INTELLIGENCE_BOUNDARIES
    )
    payloads["/v1/runs"]["data"]["runs"] = [{"status": "completed"}]
    payloads[route]["data"]["boundaries"][field] = numeric_value

    with pytest.raises(RuntimeError, match="boundary"):
        verify.validate_owner_payloads(payloads)


@pytest.mark.parametrize(
    "route",
    tuple(
        route
        for route in verify.CANARY_ROUTES
        if route not in verify.ROUTE_BOUNDARIES
    ),
)
def test_owner_payload_rejects_even_null_boundary_on_an_unrelated_route(route):
    payloads = {
        route: envelope({})
        for route in verify.CANARY_ROUTES
    }
    payloads["/v1/today"]["data"].update({
        "boundaries": dict(verify.BOUNDARIES),
        "portfolio": {
            "data_as_of": None, "market_state": "unknown", "price_sources": [],
        },
    })
    payloads["/v1/system"]["data"]["boundaries"] = dict(verify.BOUNDARIES)
    payloads["/v1/intelligence"]["data"]["boundaries"] = dict(
        verify.INTELLIGENCE_BOUNDARIES
    )
    payloads[route]["data"]["boundaries"] = None
    payloads["/v1/runs"]["data"]["runs"] = [{"status": "completed"}]

    with pytest.raises(RuntimeError, match="boundary"):
        verify.validate_owner_payloads(payloads)


def test_owner_payload_rejects_an_unsupported_send_claim():
    payloads = {route: envelope({}) for route in verify.CANARY_ROUTES}
    payloads["/v1/today"] = envelope({
        "boundaries": verify.BOUNDARIES,
        "portfolio": {"data_as_of": None, "market_state": "unknown", "price_sources": []},
    })
    payloads["/v1/system"] = envelope({"boundaries": verify.BOUNDARIES})
    payloads["/v1/intelligence"] = envelope({
        "boundaries": verify.INTELLIGENCE_BOUNDARIES,
    })
    payloads["/v1/runs"] = envelope({"runs": [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "status": "completed"}]})
    payloads["/v1/alerts"] = envelope({"alerts": [{"state": "delivered", "telegram_message_ids": []}]})
    with pytest.raises(RuntimeError, match="Telegram"):
        verify.validate_owner_payloads(payloads)


def test_http_canary_uses_only_get_and_checks_anonymous_and_non_owner_denial():
    calls = []

    def requester(method, url, headers):
        calls.append((method, url, headers))
        authorization = headers.get("authorization", "")
        if not authorization:
            return 401, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "unauthorized"}}).encode()
        if authorization == "Bearer non-owner-token":
            return 403, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "owner_only"}}).encode()
        route = url.removeprefix(API_URL)
        data = {}
        if route in {"/v1/today", "/v1/system"}:
            data["boundaries"] = dict(verify.BOUNDARIES)
        if route == "/v1/intelligence":
            data["boundaries"] = dict(verify.INTELLIGENCE_BOUNDARIES)
        if route == "/v1/today": data["portfolio"] = {"data_as_of": None, "market_state": "unknown", "price_sources": [], "holdings": []}
        if route == "/v1/portfolio": data["holdings"] = []
        if route == "/v1/alerts": data["alerts"] = []
        if route == "/v1/intelligence": data["run_id"] = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
        if route == "/v1/reports": data["reports"] = [v1_report_view("6903b3cc-05b7-4f90-bbc2-7e80a3a59e22")]
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
            "dashboard": {
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
                "intelligence_run_id": run_id,
                "overdue_scheduled_phases": [],
                "reports": [v1_public_report_source(run_id)],
            },
            "evidence": v1_chain(run_id),
        }

    receipt = verify.run_http_canary(
        API_URL, ORIGIN, "owner-token", "non-owner-token", requester=requester, source_reader=source_reader,
    )
    assert receipt["unauthenticated_status"] == 401
    assert receipt["non_owner_status"] == 403
    assert receipt["owner_route_count"] == 10
    assert receipt["source_reconciliation"] == "verified"
    assert receipt["source_database_role"] == verify.RUNTIME_ROLE
    assert receipt["evidence_database_role"] == verify.EVIDENCE_ROLE
    assert receipt["evidence_reader_authority"] == EVIDENCE_AUTHORITY
    assert receipt["scheduled_readiness"] == {
        "status": "ready",
        "overdue_phase_count": 0,
        "oldest_deadline_at": None,
        "latest_deadline_at": None,
        "phases": [],
        "receipt_sha256": verify.canonical_sha256([]),
        "overdue_scheduled_phases": [],
    }
    assert {method for method, _url, _headers in calls} == {"GET"}

    def duplicate_report_requester(method, url, headers):
        status, response_headers, body = requester(method, url, headers)
        if url.endswith("/v1/reports") and headers.get("authorization") == "Bearer owner-token":
            payload = json.loads(body)
            payload["data"]["reports"].append(dict(payload["data"]["reports"][0]))
            body = json.dumps(payload).encode()
        return status, response_headers, body

    with pytest.raises(RuntimeError, match="visible report identifiers"):
        verify.run_http_canary(
            API_URL, ORIGIN, "owner-token", "non-owner-token",
            requester=duplicate_report_requester,
            source_reader=lambda *_args: pytest.fail(
                "malformed report IDs must fail before source reads"
            ),
        )

    run_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    newer_report = json.loads(json.dumps(v1_chain(run_id)["reports"][0]))
    newer_report["id"] = "66666666-6666-4666-8666-666666666666"
    newer_report["canonical"] = {
        "title": "New scheduled report", "summary": "new research",
    }
    newer_report["report_hash"] = verify.canonical_sha256(
        newer_report["canonical"]
    )
    newer_report["created_at"] = "2026-09-03T20:01:00.000Z"
    report_reads = 0

    def raced_requester(method, url, headers):
        nonlocal report_reads
        status, response_headers, body = requester(method, url, headers)
        if url.endswith("/v1/reports") and headers.get("authorization") == "Bearer owner-token":
            report_reads += 1
            if report_reads > 1:
                payload = json.loads(body)
                payload["data"]["reports"] = [
                    verify.report_summary_source_view(newer_report),
                    v1_report_view(run_id),
                ]
                body = json.dumps(payload).encode()
        return status, response_headers, body

    def source_after_concurrent_insert(selected_run_id):
        source = source_reader(selected_run_id)
        source["dashboard"]["reports"] = [
            {field: newer_report[field] for field in verify.REPORT_PUBLIC_SOURCE_FIELDS},
            v1_public_report_source(run_id),
        ]
        source["evidence"]["reports"] = [
            newer_report, v1_chain(run_id)["reports"][0],
        ]
        return source

    raced_receipt = verify.run_http_canary(
        API_URL, ORIGIN, "owner-token", "non-owner-token",
        requester=raced_requester,
        source_reader=source_after_concurrent_insert,
    )
    assert raced_receipt["status"] == "verified"
    assert report_reads == 2

    source_reads = 0

    def omitting_requester(method, url, headers):
        status, response_headers, body = requester(method, url, headers)
        if url.endswith("/v1/reports") and headers.get("authorization") == "Bearer owner-token":
            payload = json.loads(body)
            payload["data"]["reports"] = []
            body = json.dumps(payload).encode()
        return status, response_headers, body

    def authoritative_source(selected_run_id):
        nonlocal source_reads
        source_reads += 1
        return source_reader(selected_run_id)

    with pytest.raises(RuntimeError, match="source receipt"):
        verify.run_http_canary(
            API_URL, ORIGIN, "owner-token", "non-owner-token",
            requester=omitting_requester,
            source_reader=authoritative_source,
        )
    assert source_reads == 2


def test_http_canary_reports_only_bounded_owner_failure_status_and_code():
    def requester(_method, _url, headers):
        authorization = headers.get("authorization", "")
        if not authorization:
            return 401, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "unauthorized"}}).encode()
        if authorization == "Bearer non-owner-token":
            return 403, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "owner_only"}}).encode()
        return 503, {"access-control-allow-origin": ORIGIN}, json.dumps({
            "error": {"code": "database_unavailable", "message": "private database detail"},
        }).encode()

    with pytest.raises(
        RuntimeError,
        match=r"^owner GET failed for /v1/today \(status 503, code database_unavailable\)$",
    ) as failure:
        verify.run_http_canary(
            API_URL, ORIGIN, "owner-token", "non-owner-token",
            requester=requester, source_reader=lambda _run_id: {},
        )

    assert "private database detail" not in str(failure.value)


@pytest.mark.parametrize("body", [b"not-json-private-sentinel", b"x" * 4097])
def test_http_canary_does_not_echo_untrusted_or_oversized_owner_failure_body(body):
    def requester(_method, _url, headers):
        authorization = headers.get("authorization", "")
        if not authorization:
            return 401, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "unauthorized"}}).encode()
        if authorization == "Bearer non-owner-token":
            return 403, {"access-control-allow-origin": ORIGIN}, json.dumps({"error": {"code": "owner_only"}}).encode()
        return 503, {"access-control-allow-origin": ORIGIN}, body

    with pytest.raises(RuntimeError, match=r"status 503, code unknown") as failure:
        verify.run_http_canary(
            API_URL, ORIGIN, "owner-token", "non-owner-token",
            requester=requester, source_reader=lambda _run_id: {},
        )

    assert "sentinel" not in str(failure.value)


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
    payloads["/v1/intelligence"] = envelope({"run_id": run_id})
    payloads["/v1/reports"] = envelope({"reports": [v1_report_view(run_id)]})
    detail = envelope({
        "run": payloads["/v1/runs"]["data"]["runs"][0],
        "request_receipts": [{"request_id": "one"}],
        "evaluations": [{"id": "1"}],
        "write_counts": {"suggestions": 1},
        "telegram_message_ids": [123],
        "incomplete_stages": [],
    })
    source = {
        "dashboard": {
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
                "template_version": 3, "event_status": "triggered",
            }],
            "policy_version": 17,
            "holdings": [{
                "ticker": "VTI", "shares": "2", "average_cost": "100", "price": "110",
                "price_as_of": "2026-09-03T20:00:00.000Z", "price_source": "yahoo-chart",
            }],
            "portfolio_data_as_of": "2026-09-03T20:00:00.000Z",
            "intelligence_run_id": run_id,
            "overdue_scheduled_phases": [],
            "reports": [v1_public_report_source(run_id)],
        },
        "evidence": v1_chain(run_id),
    }

    receipt = verify.reconcile_source_receipts(payloads, detail, source, run_id)
    assert receipt["status"] == "verified"
    assert receipt["claims_checked"] == 11
    assert receipt["relationships_verified"] is True
    assert "scheduled_chain" not in receipt
    assert receipt["scheduled_readiness"]["status"] == "ready"

    # The dashboard run audit feed and the protected intelligence/report
    # history are separate timelines.  A newer completed generic run must not
    # be required to own the retained report chain.
    generic_run_id = "11111111-1111-4111-8111-111111111111"
    generic_payloads = json.loads(json.dumps(payloads))
    generic_payloads["/v1/runs"]["data"]["runs"][0]["id"] = generic_run_id
    generic_detail = json.loads(json.dumps(detail))
    generic_detail["data"]["run"]["id"] = generic_run_id
    generic_source = json.loads(json.dumps(source))
    generic_source["dashboard"]["run"]["id"] = generic_run_id
    distinct_receipt = verify.reconcile_source_receipts(
        generic_payloads, generic_detail, generic_source, generic_run_id,
    )
    assert distinct_receipt["status"] == "verified"
    assert "scheduled_chain" not in distinct_receipt

    pre_v2_payloads = json.loads(json.dumps(payloads))
    pre_v2_payloads["/v1/intelligence"]["data"]["run_id"] = None
    pre_v2_source = json.loads(json.dumps(source))
    pre_v2_source["dashboard"]["intelligence_run_id"] = None
    assert verify.reconcile_source_receipts(
        pre_v2_payloads, detail, pre_v2_source, run_id,
    )["status"] == "verified"

    empty_report_payloads = json.loads(json.dumps(payloads))
    empty_report_payloads["/v1/reports"]["data"]["reports"] = []
    empty_report_source = json.loads(json.dumps(source))
    empty_report_source["dashboard"]["reports"] = []
    empty_report_source["evidence"]["reports"] = []
    empty_receipt = verify.reconcile_source_receipts(
        empty_report_payloads, detail, empty_report_source, run_id,
    )
    assert empty_receipt["counts"] == {"reports": 0}

    changed_payloads = json.loads(json.dumps(payloads))
    changed_payloads["/v1/reports"]["data"]["reports"][0]["title"] = "altered"
    with pytest.raises(
        verify.DashboardClaimMismatch,
        match="reports_visible_projection",
    ):
        verify.reconcile_source_receipts(
            changed_payloads, detail, source, run_id,
        )

    invalid_relationship_source = json.loads(json.dumps(source))
    invalid_relationship_source["dashboard"]["reports"][0]["run_id"] = "not-a-uuid"
    invalid_relationship_source["evidence"]["reports"][0]["run_id"] = "not-a-uuid"
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts(
            payloads, detail, invalid_relationship_source, run_id,
        )

    for path, value in [
        (("dashboard", "run", "write_counts"), {"suggestions": 2}),
        (("dashboard", "alerts", 0, "telegram_message_ids"), [999]),
        (("dashboard", "alerts", 0, "template_version"), "3"),
        (("dashboard", "alerts", 0, "template_version"), True),
        (("dashboard", "alerts", 0, "template_version"), 0),
        (("dashboard", "alerts", 0, "template_version"), -1),
        (("dashboard", "intelligence_run_id"), "22222222-2222-4222-8222-222222222222"),
        (("dashboard", "intelligence_run_id"), "not-a-uuid"),
        (("dashboard", "policy_version"), 18),
        (("dashboard", "holdings", 0, "price"), "109"),
        (("evidence", "reports", 0, "report_hash"), "invalid"),
    ]:
        changed = json.loads(json.dumps(source))
        target = changed
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        with pytest.raises(RuntimeError, match="source receipt"):
            verify.reconcile_source_receipts(payloads, detail, changed, run_id)

    missing_projection_identity = json.loads(json.dumps(source))
    del missing_projection_identity["dashboard"]["intelligence_run_id"]
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts(
            payloads, detail, missing_projection_identity, run_id,
        )

    missing_visible_projection = json.loads(json.dumps(pre_v2_payloads))
    del missing_visible_projection["/v1/intelligence"]["data"]["run_id"]
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts(
            missing_visible_projection, detail, pre_v2_source, run_id,
        )

    for invalid_visible_version in (3, "03"):
        changed_payloads = json.loads(json.dumps(payloads))
        changed_payloads["/v1/alerts"]["data"]["alerts"][0]["template_version"] = invalid_visible_version
        with pytest.raises(RuntimeError, match="source receipt"):
            verify.reconcile_source_receipts(changed_payloads, detail, source, run_id)

    changed = json.loads(json.dumps(source))
    changed["evidence"]["reports"][0]["canonical"]["title"] = "replaced retained source body"
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts(payloads, detail, changed, run_id)


def test_source_reconciliation_requires_scoped_read_only_database_role():
    source = {
        "dashboard": {
            "database_user": "postgres", "transaction_read_only": "off",
            "overdue_scheduled_phases": [],
        },
        "evidence": v1_chain("6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"),
    }
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts({}, {}, source, "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("evidence_database_user", "postgres"),
        ("evidence_transaction_read_only", "off"),
    ),
)
def test_source_reconciliation_requires_a_scoped_read_only_evidence_role(
    field, value,
):
    source = {
        "dashboard": {
            "database_user": verify.RUNTIME_ROLE,
            "transaction_read_only": "on",
            "overdue_scheduled_phases": [],
        },
        "evidence": {
            "evidence_database_user": verify.EVIDENCE_ROLE,
            "evidence_transaction_read_only": "on",
        },
    }
    source["evidence"][field] = value
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.reconcile_source_receipts(
            {}, {}, source, "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22",
        )


def test_source_reconciliation_retains_an_overdue_phase_as_pending_release_evidence():
    run_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    payloads = {route: envelope({}) for route in verify.CANARY_ROUTES}
    payloads["/v1/today"] = envelope({"boundaries": verify.BOUNDARIES, "portfolio": {"data_as_of": None, "market_state": "unknown", "price_sources": [], "holdings": []}})
    payloads["/v1/portfolio"] = envelope({"holdings": []})
    payloads["/v1/runs"] = envelope({"runs": [{"id": run_id, "kind": "post-market", "status": "completed", "finished_at": "2026-09-03T20:00:00.000Z", "data_as_of": None, "evaluation_count": 0, "suggestion_count": 0, "publication_status": None}]})
    payloads["/v1/alerts"] = envelope({"alerts": []})
    payloads["/v1/intelligence"] = envelope({"run_id": run_id})
    payloads["/v1/reports"] = envelope({"reports": [v1_report_view(run_id)]})
    detail = envelope({"run": payloads["/v1/runs"]["data"]["runs"][0], "request_receipts": [], "evaluations": [], "write_counts": {}, "telegram_message_ids": [], "incomplete_stages": []})
    source = {
        "dashboard": {
            "database_user": verify.RUNTIME_ROLE, "transaction_read_only": "on",
            "run": {"id": run_id, "kind": "post-market", "status": "completed", "finished_at": "2026-09-03T20:00:00.000Z", "data_as_of": None, "write_counts": {}, "telegram_message_ids": []},
            "gateway_request_count": 0, "evaluation_count": 0, "suggestion_count": 0,
            "alerts": [], "policy_version": None, "holdings": [], "portfolio_data_as_of": None,
            "intelligence_run_id": run_id,
            "overdue_scheduled_phases": [{"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "2026-09-03T22:00:00.000Z"}],
            "reports": [v1_public_report_source(run_id)],
        },
        "evidence": v1_chain(run_id),
    }
    receipt = verify.reconcile_source_receipts(payloads, detail, source, run_id)
    overdue = source["dashboard"]["overdue_scheduled_phases"]
    assert receipt["status"] == "verified"
    assert receipt["scheduled_readiness"] == {
        "status": "pending",
        "overdue_phase_count": 1,
        "oldest_deadline_at": "2026-09-03T22:00:00.000Z",
        "latest_deadline_at": "2026-09-03T22:00:00.000Z",
        "phases": ["post-market"],
        "receipt_sha256": verify.canonical_sha256(overdue),
        "overdue_scheduled_phases": overdue,
    }


@pytest.mark.parametrize("overdue", [
    [{"market_date": "2026-09-03", "phase": "unknown", "deadline_at": "2026-09-03T22:00:00.000Z"}],
    [{"market_date": "2026-09-31", "phase": "post-market", "deadline_at": "2026-09-03T22:00:00.000Z"}],
    [{"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "not-a-time"}],
    [{"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "2026-09-03T22:00:00.000Z"}] * 2,
    [
        {"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "2026-09-03T22:00:00.000Z"},
        {"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "2026-09-03T22:15:00.000Z"},
    ],
    [{"market_date": "2026-09-03", "phase": "post-market", "deadline_at": "2026-09-03T22:00:00.000Z"}]
    * (verify.MAX_SCHEDULED_READINESS_ROWS + 1),
])
def test_scheduled_readiness_rejects_malformed_duplicate_or_oversized_rows(overdue):
    with pytest.raises(RuntimeError, match="source receipt"):
        verify.scheduled_readiness_receipt(overdue)


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


def test_evidence_database_url_must_use_the_scoped_release_reader_login():
    valid = (
        "postgresql://stock_agent_release_reader_runtime.hlxpxbxhqctwsqizwjjy:"
        "evidence-password-longer-than-24@aws-1-us-west-2.pooler.supabase.com:"
        "5432/postgres"
    )
    assert verify.validate_evidence_database_url(valid, API_URL) == valid
    for invalid in [
        valid.replace("stock_agent_release_reader_runtime", "postgres"),
        valid.replace("stock_agent_release_reader_runtime", verify.RUNTIME_ROLE),
        valid.replace("evidence-password-longer-than-24", "%41" * 8),
        valid.replace(":5432/", ":6543/"),
        valid.replace("pooler.supabase.com", "example.com"),
    ]:
        with pytest.raises(ValueError, match="evidence database"):
            verify.validate_evidence_database_url(invalid, API_URL)


def test_source_collector_keeps_visible_and_protected_queries_on_separate_read_only_connections(
    monkeypatch,
):
    dashboard_url = (
        "postgresql://stock_agent_dashboard_runtime.hlxpxbxhqctwsqizwjjy:"
        "dashboard-password-longer-than-24@aws-1-us-west-2.pooler.supabase.com:"
        "5432/postgres"
    )
    evidence_url = (
        "postgresql://stock_agent_release_reader_runtime.hlxpxbxhqctwsqizwjjy:"
        "evidence-password-longer-than-24@aws-1-us-west-2.pooler.supabase.com:"
        "5432/postgres"
    )
    run_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    connections = []

    class Cursor:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, parameters=()):
            self.connection.queries.append((query, parameters))

    class Connection:
        def __init__(self, label, kwargs):
            self.label = label
            self.kwargs = kwargs
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor(self)

    def connect(url, **kwargs):
        assert url == dashboard_url
        connection = Connection("dashboard", kwargs)
        connections.append(connection)
        return connection

    class EvidenceSource:
        def __init__(self, url, project_ref):
            assert url == evidence_url
            assert project_ref == "hlxpxbxhqctwsqizwjjy"
            self.connection = Connection("evidence", {
                "row_factory": verify.dict_row,
                "sslmode": "verify-full",
                "connect_timeout": 15,
            })

        def __enter__(self):
            connections.append(self.connection)
            self.connection.queries.extend([
                ("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY", ()),
                ("SET LOCAL statement_timeout='30s'", ()),
            ])
            return self

        def __exit__(self, *_args):
            return False

        def identity(self):
            return {
                "connection_id": "a" * 64,
                "read_only": True,
                "isolated_guard": False,
            }

    def fetch_one(connection, query, parameters=()):
        connection.queries.append((query, parameters))
        if "current_user AS database_user" in query:
            return {
                "database_user": verify.RUNTIME_ROLE,
                "transaction_read_only": "on",
            }
        if "current_user AS evidence_database_user" in query:
            return {
                "evidence_database_user": verify.EVIDENCE_ROLE,
                "evidence_transaction_read_only": "on",
            }
        if "FROM public.analysis_runs" in query:
            return {
                "id": run_id, "kind": "post-market", "status": "completed",
                "finished_at": "2026-09-03T20:00:00.000Z", "data_as_of": None,
                "write_counts": {}, "telegram_message_ids": [],
            }
        if "gateway_request_count" in query:
            return {
                "gateway_request_count": 0,
                "evaluation_count": 0,
                "suggestion_count": 0,
            }
        if "read_owner_intelligence_v2" in query:
            return {
                "projection_present": True,
                "projection_type": "object",
                "run_id_present": True,
                "run_id_type": "null",
                "intelligence_run_id": None,
            }
        return {}

    def fetch_all(connection, query, parameters=()):
        connection.queries.append((query, parameters))
        return []

    from scripts import protected_evidence

    monkeypatch.setattr(verify.psycopg, "connect", connect)
    monkeypatch.setattr(
        protected_evidence, "PostgresReadOnlySource", EvidenceSource,
    )
    monkeypatch.setattr(verify, "_fetch_one", fetch_one)
    monkeypatch.setattr(verify, "_fetch_all", fetch_all)

    receipt = verify.collect_source_receipts(
        dashboard_url, evidence_url, API_URL, run_id,
    )

    assert [connection.label for connection in connections] == [
        "evidence", "dashboard",
    ]
    assert all(connection.kwargs == {
        "row_factory": verify.dict_row,
        "sslmode": "verify-full",
        "connect_timeout": 15,
    } for connection in connections)
    for connection in connections:
        transaction_queries = [query for query, _parameters in connection.queries]
        assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in transaction_queries
        assert "SET LOCAL statement_timeout='30s'" in transaction_queries

    dashboard_queries = "\n".join(
        query for connection in connections if connection.label == "dashboard"
        for query, _parameters in connection.queries
    )
    evidence_queries = "\n".join(
        query for connection in connections if connection.label == "evidence"
        for query, _parameters in connection.queries
    )
    assert "public.market_reports" in dashboard_queries
    assert "public.market_reports" in evidence_queries
    assert "JOIN public.market_intelligence_runs" in evidence_queries
    assert "JOIN public.market_evidence_packets" in evidence_queries
    assert "JOIN public.market_intelligence_runs" not in dashboard_queries
    assert "JOIN public.market_evidence_packets" not in dashboard_queries
    report_queries = [
        (connection.label, query, parameters)
        for connection in connections
        for query, parameters in connection.queries
        if "public.market_reports" in query
    ]
    dashboard_report_query = next(
        query for label, query, _parameters in report_queries
        if label == "dashboard"
    )
    assert "packet_id" not in dashboard_report_query
    assert "rendered_text" not in dashboard_report_query
    assert "rendered_hash" not in dashboard_report_query
    assert [parameters for _label, _query, parameters in report_queries] == [
        (), ([],),
    ]
    evidence_report_query = next(
        query for label, query, _parameters in report_queries
        if label == "evidence"
    )
    assert "ORDER BY r.created_at DESC, r.id DESC LIMIT 50" in evidence_report_query
    assert "unnest(%s::uuid[]) WITH ORDINALITY" in dashboard_report_query
    assert receipt["dashboard"]["reports"] == []
    assert receipt["evidence"]["reports"] == []
    assert "public.read_owner_intelligence_v2(25) AS projection" in dashboard_queries
    assert "jsonb_typeof(projection->'run_id') AS run_id_type" in dashboard_queries
    overdue_queries = [
        (query, parameters)
        for connection in connections if connection.label == "dashboard"
        for query, parameters in connection.queries
        if "read_overdue_scheduled_market_phases" in query
    ]
    assert len(overdue_queries) == 1
    assert "LIMIT %s" in overdue_queries[0][0]
    assert overdue_queries[0][1] == (verify.MAX_SCHEDULED_READINESS_ROWS + 1,)
    assert receipt["dashboard"]["database_user"] == verify.RUNTIME_ROLE
    assert receipt["dashboard"]["intelligence_run_id"] is None
    assert receipt["evidence"]["evidence_database_user"] == verify.EVIDENCE_ROLE
    assert receipt["evidence"]["evidence_authority"] == EVIDENCE_AUTHORITY
    assert dashboard_url not in str(receipt)
    assert evidence_url not in str(receipt)


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
            return 200, json.dumps({"hashed_token": "hash-token-value"}).encode()
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


def test_ephemeral_existing_user_session_uses_non_creating_recovery_link():
    calls = []

    def requester(method, url, _headers, body):
        calls.append((method, url, json.loads(body)))
        if url.endswith("/auth/v1/admin/generate_link"):
            return 200, json.dumps({"hashed_token": "existing-user-token-hash"}).encode()
        return 200, json.dumps({"access_token": "existing-user-access-token-" + "x" * 40}).encode()

    token = verify.obtain_ephemeral_existing_user_access_token(
        "https://hlxpxbxhqctwsqizwjjy.supabase.co", "owner@example.com", ORIGIN,
        "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
        requester=requester,
    )

    assert token.startswith("existing-user-access-token-")
    assert calls[0][2]["type"] == "recovery"
    assert calls[1][2] == {"type": "recovery", "token_hash": "existing-user-token-hash"}


def test_ephemeral_existing_user_session_cannot_create_a_missing_identity():
    calls = []

    def requester(method, url, _headers, body):
        calls.append((method, url, json.loads(body)))
        return 404, b'{"code":"user_not_found"}'

    with pytest.raises(RuntimeError, match="ephemeral owner session"):
        verify.obtain_ephemeral_existing_user_access_token(
            "https://hlxpxbxhqctwsqizwjjy.supabase.co", "missing@example.com", ORIGIN,
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            requester=requester,
        )

    assert len(calls) == 1
    assert calls[0][2]["type"] == "recovery"


def test_ephemeral_owner_session_revocation_uses_global_logout_without_leaking_token():
    observed = {}

    def requester(method, url, headers, body):
        observed.update(method=method, url=url, headers=headers, body=body)
        return 204, b""

    receipt = verify.revoke_ephemeral_owner_session(
        "https://hlxpxbxhqctwsqizwjjy.supabase.co",
        "owner-access-token-" + "x" * 40,
        "sb_publishable_" + "p" * 32,
        requester=requester,
    )

    assert receipt == {"status": "revoked", "scope": "global"}
    assert observed["method"] == "POST"
    assert observed["url"].endswith("/auth/v1/logout?scope=global")
    assert observed["body"] is None
    assert "owner-access-token" not in str(receipt)


def test_ephemeral_owner_session_revocation_can_target_only_current_session():
    observed = {}

    def requester(method, url, headers, body):
        observed.update(method=method, url=url, headers=headers, body=body)
        return 204, b""

    receipt = verify.revoke_ephemeral_owner_session(
        "https://hlxpxbxhqctwsqizwjjy.supabase.co",
        "owner-access-token-" + "x" * 40,
        "sb_publishable_" + "p" * 32,
        scope="local",
        requester=requester,
    )

    assert receipt == {"status": "revoked", "scope": "local"}
    assert observed["url"].endswith("/auth/v1/logout?scope=local")


def test_auth_inventory_is_exactly_one_owner_and_one_bound_denied_canary():
    owner_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    canary_id = "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    canary_email = f"release-canary-{'a' * 32}@example.com"
    calls = []

    def requester(method, url, _headers, body):
        calls.append((method, url, body))
        if "?page=1&" in url:
            return 200, json.dumps({"users": [
                {"id": owner_id.upper(), "email": "OWNER@example.com", "email_confirmed_at": "2026-09-01T00:00:00Z"},
                {"id": canary_id.upper(), "email": canary_email.upper(), "confirmed_at": "2026-09-01T00:00:00Z"},
            ]}).encode()
        return 200, json.dumps({"users": []}).encode()

    receipt = verify.verify_auth_canary_inventory(
        "https://hlxpxbxhqctwsqizwjjy.supabase.co",
        "owner@example.com", owner_id, canary_email, canary_id,
        "sb_secret_" + "s" * 40, requester=requester,
    )

    assert receipt == {
        "status": "verified", "identity_count": 2,
        "privileged_owner_count": 1, "denied_canary_count": 1,
    }
    assert [call[0] for call in calls] == ["GET", "GET"]
    assert all(call[2] is None for call in calls)
    assert "owner@example.com" not in str(receipt)
    assert canary_id not in str(receipt)


@pytest.mark.parametrize("extra_users", [
    [{"id": "8903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "email": "extra@example.com", "confirmed_at": "x"}],
    [{"id": "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22", "email": "owner@example.com", "confirmed_at": "x"}],
])
def test_auth_inventory_rejects_extra_or_duplicate_identities(extra_users):
    owner_id = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    canary_id = "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
    canary_email = f"release-canary-{'a' * 32}@example.com"

    def requester(_method, url, _headers, _body):
        users = [
            {"id": owner_id, "email": "owner@example.com", "confirmed_at": "x"},
            {"id": canary_id, "email": canary_email, "confirmed_at": "x"},
            *extra_users,
        ] if "?page=1&" in url else []
        return 200, json.dumps({"users": users}).encode()

    with pytest.raises(RuntimeError, match="exactly the owner and denied canary"):
        verify.verify_auth_canary_inventory(
            "https://hlxpxbxhqctwsqizwjjy.supabase.co",
            "owner@example.com", owner_id, canary_email, canary_id,
            "sb_secret_" + "s" * 40, requester=requester,
        )


def test_auth_inventory_rejects_an_unbound_canary_address_before_network():
    calls = []
    with pytest.raises(ValueError, match="reserved canary"):
        verify.verify_auth_canary_inventory(
            "https://hlxpxbxhqctwsqizwjjy.supabase.co",
            "owner@example.com", "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22",
            "someone@gmail.com", "7903b3cc-05b7-4f90-bbc2-7e80a3a59e22",
            "sb_secret_" + "s" * 40,
            requester=lambda *_args: calls.append(True),
        )
    assert calls == []
