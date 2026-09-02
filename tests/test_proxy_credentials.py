import json

from lib import fundamentals


class FakeResponse:
    def read(self):
        return json.dumps({"metric": {"peTTM": 21.5}}).encode()


def test_finnhub_client_allows_credential_proxy_to_inject_header(monkeypatch):
    captured = {}

    def missing_secret(name):
        raise KeyError(name)

    def fake_urlopen(request, **_kwargs):
        captured["request"] = request
        return FakeResponse()

    monkeypatch.setattr(fundamentals.config, "secret", missing_secret)
    monkeypatch.setattr(fundamentals.urllib.request, "urlopen", fake_urlopen)

    result = fundamentals._get("https://finnhub.io/api/v1/stock/metric?symbol=AAPL")

    assert result == {"metric": {"peTTM": 21.5}}
    assert "X-finnhub-token" not in captured["request"].headers
