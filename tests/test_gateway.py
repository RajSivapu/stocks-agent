import io
import json
from datetime import datetime
from pathlib import Path
import runpy
import urllib.error
from zoneinfo import ZoneInfo

import pytest

from lib import gateway
from lib import config


REQUEST_ID = "00000000-0000-4000-8000-000000000001"
RUN_ID = "00000000-0000-4000-8000-000000000002"
ROOT = Path(__file__).resolve().parents[1]


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


def test_healthcheck_supplies_owner_market_date_to_gateway(monkeypatch, capsys):
    calls = []

    def fake_gateway_call(operation, payload, **kwargs):
        calls.append((operation, payload, kwargs))
        return {"data": {"run_id": RUN_ID}}

    monkeypatch.setattr(gateway, "call", fake_gateway_call)
    monkeypatch.setattr(config, "secret", lambda _name: "test-value")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse(b"{}"))

    before = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    runpy.run_path(str(ROOT / "scripts" / "healthcheck.py"), run_name="__main__")
    after = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    capsys.readouterr()

    assert calls[0][0] == "start_run"
    assert calls[0][1]["phase"] == "on-demand"
    assert calls[0][1]["market_date"] in {before, after}
    assert calls[0][2]["dry_run"] is True
    assert calls[2] == ("evaluate_alert_rules", {}, {"dry_run": True})


def configured(monkeypatch):
    values = {
        "supabase_url": "https://project.supabase.co",
        "market_agent_secret": "scoped-secret",
    }
    monkeypatch.setattr(gateway.config, "secret", values.__getitem__)


def test_call_sends_only_scoped_header_and_compact_decimal_payload(monkeypatch):
    configured(monkeypatch)
    captured = {}

    def opener(request, **kwargs):
        captured["request"] = request
        captured.update(kwargs)
        return FakeResponse({"ok": True, "data": {"run_id": RUN_ID}})

    result = gateway.call(
        "start_run",
        {"phase": "intraday", "amount": "2057.04"},
        dry_run=True,
        request_id=REQUEST_ID,
        timeout=17,
        _opener=opener,
    )

    assert result["ok"] is True
    request = captured["request"]
    assert request.full_url == "https://project.supabase.co/functions/v1/market-briefing-gateway"
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert request.headers["X-market-agent-secret"] == "scoped-secret"
    assert "Authorization" not in request.headers
    assert captured["timeout"] == 17
    body = json.loads(request.data)
    assert body == {
        "dry_run": True,
        "operation": "start_run",
        "payload": {"amount": "2057.04", "phase": "intraday"},
        "request_id": REQUEST_ID,
        "run_id": None,
        "schema_version": 1,
    }
    assert b"SUPABASE_SERVICE_ROLE_KEY" not in request.data


def test_call_allows_credential_proxy_to_inject_scoped_header(monkeypatch):
    def proxy_configuration(name):
        if name == "supabase_url":
            return "https://project.supabase.co"
        raise KeyError(name)

    monkeypatch.setattr(gateway.config, "secret", proxy_configuration)
    captured = {}

    def opener(request, **_kwargs):
        captured["request"] = request
        return FakeResponse({"ok": True, "data": {"run_id": RUN_ID}})

    result = gateway.call(
        "start_run",
        {"phase": "intraday"},
        request_id=REQUEST_ID,
        _opener=opener,
    )

    assert result["ok"] is True
    request = captured["request"]
    assert request.full_url == "https://project.supabase.co/functions/v1/market-briefing-gateway"
    assert "X-market-agent-secret" not in request.headers
    assert set(request.headers) == {"Content-type"}


def test_call_generates_uuid_and_passes_run_identity(monkeypatch):
    configured(monkeypatch)
    captured = {}

    def opener(request, **_kwargs):
        captured.update(json.loads(request.data))
        return FakeResponse({"ok": True, "data": {}})

    gateway.call("read_context", {}, run_id=RUN_ID, _opener=opener)

    assert gateway.UUID_PATTERN.fullmatch(captured["request_id"])
    assert captured["run_id"] == RUN_ID


def test_alert_evaluation_is_allowlisted_and_standalone(monkeypatch):
    configured(monkeypatch)
    captured = {}

    def opener(request, **_kwargs):
        captured.update(json.loads(request.data))
        return FakeResponse({"ok": True, "data": {"dry_run": True}})

    result = gateway.call(
        "evaluate_alert_rules",
        {},
        dry_run=True,
        request_id=REQUEST_ID,
        _opener=opener,
    )

    assert result["ok"] is True
    assert captured["operation"] == "evaluate_alert_rules"
    assert captured["run_id"] is None


@pytest.mark.parametrize("operation", ["send_telegram", "drop_table", "BUY"])
def test_call_rejects_unknown_operations_before_opening(monkeypatch, operation):
    configured(monkeypatch)
    opened = False

    def opener(*_args, **_kwargs):
        nonlocal opened
        opened = True

    with pytest.raises(ValueError, match="operation"):
        gateway.call(operation, {}, _opener=opener)
    assert opened is False


def test_call_enforces_request_size_before_opening(monkeypatch):
    configured(monkeypatch)
    opened = False

    def opener(*_args, **_kwargs):
        nonlocal opened
        opened = True

    with pytest.raises(ValueError, match="request too large"):
        gateway.call(
            "start_run",
            {"padding": "x" * 262_144},
            request_id=REQUEST_ID,
            _opener=opener,
        )
    assert opened is False


def test_call_enforces_declared_and_streamed_response_bounds(monkeypatch):
    configured(monkeypatch)
    with pytest.raises(gateway.GatewayError, match="RESPONSE_TOO_LARGE"):
        gateway.call(
            "start_run",
            {},
            request_id=REQUEST_ID,
            _opener=lambda *_args, **_kwargs: FakeResponse(
                b"{}", headers={"content-length": "1048577"}
            ),
        )
    with pytest.raises(gateway.GatewayError, match="RESPONSE_TOO_LARGE"):
        gateway.call(
            "start_run",
            {},
            request_id=REQUEST_ID,
            _opener=lambda *_args, **_kwargs: FakeResponse(b"x" * 1_048_577),
        )


def test_call_requires_https_without_explicit_test_opener(monkeypatch):
    monkeypatch.setattr(gateway.config, "secret", {
        "supabase_url": "http://project.invalid",
        "market_agent_secret": "scoped-secret",
    }.__getitem__)
    with pytest.raises(ValueError, match="HTTPS"):
        gateway.call("start_run", {}, request_id=REQUEST_ID)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"ok": True},
        {"ok": True, "data": "not-an-object"},
        {"ok": False},
        {"ok": False, "code": "raw secret-scoped-secret"},
    ],
)
def test_call_validates_response_shape(monkeypatch, body):
    configured(monkeypatch)
    with pytest.raises(gateway.GatewayError):
        gateway.call(
            "start_run",
            {},
            request_id=REQUEST_ID,
            _opener=lambda *_args, **_kwargs: FakeResponse(body),
        )


def test_call_raises_only_stable_redacted_error_codes(monkeypatch):
    configured(monkeypatch)

    def opener(*_args, **_kwargs):
        raise urllib.error.URLError(
            "https://project.supabase.co?key=scoped-secret response-body-secret"
        )

    with pytest.raises(gateway.GatewayError) as captured:
        gateway.call("start_run", {}, request_id=REQUEST_ID, _opener=opener)
    assert str(captured.value) == "GATEWAY_UNAVAILABLE"
    assert "scoped-secret" not in repr(captured.value)
    assert "project.supabase.co" not in repr(captured.value)
    assert "response-body-secret" not in repr(captured.value)


def test_call_surfaces_bounded_gateway_error_code(monkeypatch):
    configured(monkeypatch)
    with pytest.raises(gateway.GatewayError, match="RATE_LIMITED"):
        gateway.call(
            "start_run",
            {},
            request_id=REQUEST_ID,
            _opener=lambda *_args, **_kwargs: FakeResponse(
                {"ok": False, "code": "RATE_LIMITED"}
            ),
        )


def test_cli_is_bounded_and_never_prints_raw_errors(monkeypatch, capsys):
    from scripts import market_gateway

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"{}")))
    monkeypatch.setattr(market_gateway.gateway, "call", lambda *_args, **_kwargs: (_ for _ in ()).throw(gateway.GatewayError("RATE_LIMITED")))
    assert market_gateway.main(["start_run", "--dry-run"]) == 1
    assert json.loads(capsys.readouterr().out) == {"code": "RATE_LIMITED", "ok": False}


def test_healthcheck_allows_finnhub_proxy_to_inject_header(monkeypatch, capsys):
    requests = []

    def fake_gateway_call(_operation, _payload, **_kwargs):
        return {"data": {"run_id": RUN_ID}}

    def missing_secret(name):
        raise KeyError(name)

    def fake_urlopen(request, **_kwargs):
        requests.append(request)
        return FakeResponse(b"{}")

    monkeypatch.setattr(gateway, "call", fake_gateway_call)
    monkeypatch.setattr(config, "secret", missing_secret)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    runpy.run_path(str(ROOT / "scripts" / "healthcheck.py"), run_name="__main__")
    result = json.loads(capsys.readouterr().out)

    assert result == {"alerts": "ok", "gateway": "ok", "finnhub": "ok", "yahoo": "ok"}
    finnhub_request = next(r for r in requests if "finnhub.io" in r.full_url)
    assert "X-finnhub-token" not in finnhub_request.headers
