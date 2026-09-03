from __future__ import annotations

import json
import urllib.error

import pytest

from scripts import agent_gateway_v2


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
TRIGGER_ID = "33333333-3333-4333-8333-333333333333"
URL = "https://projectref.supabase.co/functions/v1/agent-gateway"


class FakeResponse:
    def __init__(self, body, status=200, headers=None):
        self.body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status
        self.headers = headers or {}

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_gateway_call_uses_v2_envelope_and_relies_on_proxy_credential():
    captured = {}

    def opener(request, **kwargs):
        captured["request"] = request
        captured.update(kwargs)
        return FakeResponse({"ok": True, "data": {"run_id": RUN_ID}})

    result = agent_gateway_v2.call_gateway(
        URL,
        "start_run",
        {"trigger_request_id": TRIGGER_ID},
        request_id=REQUEST_ID,
        _opener=opener,
    )
    assert result == {"run_id": RUN_ID}
    request = captured["request"]
    assert request.full_url == URL
    assert set(request.headers) == {"Content-type"}
    assert json.loads(request.data) == {
        "contract_version": 2,
        "operation": "start_run",
        "request_id": REQUEST_ID,
        "run_id": None,
        "dry_run": False,
        "payload": {"trigger_request_id": TRIGGER_ID},
    }


@pytest.mark.parametrize("url", [
    "http://projectref.supabase.co/functions/v1/agent-gateway",
    "https://evil.example/functions/v1/agent-gateway",
    "https://projectref.supabase.co/rest/v1/agent-gateway",
    "https://projectref.supabase.co/functions/v1/agent-gateway?token=secret",
])
def test_gateway_url_is_exactly_bounded(url):
    with pytest.raises(agent_gateway_v2.ClientError, match="INVALID_GATEWAY_URL"):
        agent_gateway_v2.call_gateway(url, "start_run", {"trigger_request_id": None})


def test_handshake_probes_in_same_runtime_and_finishes_with_run_challenge():
    calls = []

    def caller(_url, operation, payload, *, run_id=None, **_kwargs):
        calls.append((operation, payload, run_id))
        if operation == "start_run":
            return {"run_id": RUN_ID}
        if operation == "read_bounded_context":
            return {
                "handshake": True,
                "challenge": "a" * 64,
                "contract_version": 2,
                "allowed_source_hosts": sorted(agent_gateway_v2.REQUIRED_SOURCE_HOSTS),
                "holdings": [],
                "plans": [],
            }
        return {"status": "completed"}

    source_checks = [
        {"host": host, "status": "reachable", "content_hash": str(index) * 64,
         "observed_at": "2026-09-03T12:00:00+00:00"}
        for index, host in enumerate(sorted(agent_gateway_v2.REQUIRED_SOURCE_HOSTS), start=1)
    ]
    result = agent_gateway_v2.perform_handshake(
        URL, TRIGGER_ID, _call=caller, _probe=lambda: source_checks
    )
    assert result == {"status": "completed", "source_hosts": 3, "writes": 0, "notifications": 0}
    assert [call[0] for call in calls] == ["start_run", "read_bounded_context", "finish_run"]
    assert calls[-1][1] == {
        "contract_version": 2,
        "challenge": "a" * 64,
        "source_checks": source_checks,
    }


def test_handshake_refuses_missing_or_unreachable_source_without_finish():
    operations = []

    def caller(_url, operation, _payload, *, run_id=None, **_kwargs):
        operations.append(operation)
        if operation == "start_run":
            return {"run_id": RUN_ID}
        return {
            "handshake": True,
            "challenge": "a" * 64,
            "contract_version": 2,
            "allowed_source_hosts": sorted(agent_gateway_v2.REQUIRED_SOURCE_HOSTS),
            "holdings": [],
            "plans": [],
        }

    checks = [{
        "host": host,
        "status": "unreachable" if index == 0 else "reachable",
        "content_hash": None if index == 0 else "b" * 64,
        "observed_at": "2026-09-03T12:00:00+00:00",
    } for index, host in enumerate(sorted(agent_gateway_v2.REQUIRED_SOURCE_HOSTS))]
    with pytest.raises(agent_gateway_v2.ClientError, match="SOURCE_NETWORK_FAILED"):
        agent_gateway_v2.perform_handshake(
            URL, TRIGGER_ID, _call=caller, _probe=lambda: checks
        )
    assert operations == ["start_run", "read_bounded_context"]


def test_invoke_returns_server_resolved_analysis_context_without_model_phase_input():
    calls = []
    context = {"run_id": RUN_ID, "holdings": [], "plans": [], "evidence": []}

    def caller(_url, operation, payload, *, run_id=None, **_kwargs):
        calls.append((operation, payload, run_id))
        return {"run_id": RUN_ID} if operation == "start_run" else context

    result = agent_gateway_v2.perform_invocation(URL, TRIGGER_ID, _call=caller)
    assert result == {"kind": "analysis", "run_id": RUN_ID, "context": context}
    assert calls == [
        ("start_run", {"trigger_request_id": TRIGGER_ID}, None),
        ("read_bounded_context", {}, RUN_ID),
    ]


def test_network_errors_and_server_details_are_redacted():
    def opener(*_args, **_kwargs):
        raise urllib.error.URLError("https://projectref.supabase.co?secret=do-not-print")

    with pytest.raises(agent_gateway_v2.ClientError) as captured:
        agent_gateway_v2.call_gateway(
            URL,
            "start_run",
            {"trigger_request_id": None},
            request_id=REQUEST_ID,
            _opener=opener,
        )
    assert str(captured.value) == "GATEWAY_UNAVAILABLE"
    assert "secret" not in repr(captured.value)
