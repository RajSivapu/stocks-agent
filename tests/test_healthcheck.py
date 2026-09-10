import json
from email.message import Message
from urllib.error import HTTPError

from lib.intelligence.health import build_capability_health


def test_healthcheck_reports_exact_routes_without_credential_values():
    probes = []

    def probe(request):
        probes.append(request)
        return request.allowed_final_urls[-1]

    result = build_capability_health(
        configured_values={
            "SEC_USER_AGENT_CONTACT": "owner@example.com",
            "FINNHUB_API_KEY": "finnhub-secret",
        },
        probe=probe,
    )

    assert result["zero_key_baseline"] == "ok"
    assert result["capabilities"]["alpha_vantage_topic_news"]["status"] == (
        "configuration_missing"
    )
    assert result["capabilities"]["eia_statistics_v2"]["status"] == (
        "configuration_missing"
    )
    assert result["capabilities"]["sec_company_tickers_universe"]["status"] == "ok"
    assert result["capabilities"]["sec_filing_document"]["status"] == (
        "ready_requires_identifier"
    )

    requested = {request.capability_id: request.resolved_url() for request in probes}
    assert requested["doe_energy_news_rss"] == (
        "https://www.energy.gov/rss/energygov/2193718"
    )
    assert requested["defense_releases_rss"] == (
        "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx"
        "?ContentType=9&Site=945&max=10"
    )
    assert requested["white_house_fact_sheets"] == "https://www.whitehouse.gov/fact-sheets/"
    assert requested["white_house_sitemap"] == "https://www.whitehouse.gov/sitemap_index.xml"
    assert requested["federal_register_document_search"].startswith(
        "https://www.federalregister.gov/api/v1/documents.json?"
    )
    gdelt = next(request for request in probes if request.capability_id == "gdelt_theme_search")
    assert gdelt.resolved_url() == "https://data.gdeltproject.org/gdeltv3/gal/feed.rss"
    assert gdelt.headers == {
        "Accept": "application/rss+xml, application/xml;q=0.9",
        "User-Agent": "stocks-agent owner research",
    }

    encoded = json.dumps(result, sort_keys=True)
    assert "owner@example.com" not in encoded
    assert "finnhub-secret" not in encoded


def test_missing_sec_contact_fails_keyless_baseline_without_opening_sec():
    probes = []

    result = build_capability_health(
        configured_values={},
        probe=lambda request: probes.append(request) or request.url,
    )

    assert result["zero_key_baseline"] == "configuration_missing"
    assert result["capabilities"]["sec_company_tickers_universe"]["status"] == (
        "configuration_missing"
    )
    assert all(request.provider != "sec_edgar" for request in probes)


def test_healthcheck_rejects_unreviewed_redirect_as_source_failed():
    def probe(request):
        if request.capability_id == "defense_news_rss":
            return (
                "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx"
                "?ContentType=9&Site=945&max=10"
            )
        return request.allowed_final_urls[-1]

    result = build_capability_health(
        configured_values={"SEC_USER_AGENT_CONTACT": "owner@example.com"},
        probe=probe,
    )

    assert result["capabilities"]["defense_news_rss"]["status"] == "source_failed"
    assert result["capabilities"]["defense_releases_rss"]["status"] == "ok"


def test_optional_alpha_vantage_does_not_control_keyless_baseline():
    result = build_capability_health(
        configured_values={"SEC_USER_AGENT_CONTACT": "owner@example.com"},
        probe=lambda request: request.allowed_final_urls[-1],
    )

    assert result["capabilities"]["alpha_vantage_topic_news"]["status"] == (
        "configuration_missing"
    )
    assert result["capabilities"]["gdelt_theme_search"]["status"] == "ok"
    assert result["zero_key_baseline"] == "ok"


def test_disabled_eia_statistics_reports_present_key_without_enabling_route():
    result = build_capability_health(
        configured_values={
            "SEC_USER_AGENT_CONTACT": "owner@example.com",
            "EIA_API_KEY": "existing-free-key",
        },
        probe=lambda request: request.allowed_final_urls[-1],
    )

    eia = result["capabilities"]["eia_statistics_v2"]
    assert eia["enabled"] is False
    assert eia["declared_health"] == "configuration_missing"
    assert eia["configuration_status"] == "present"
    assert eia["status"] == "configuration_missing"


def test_health_probe_opens_only_an_exact_validated_defense_redirect(monkeypatch):
    import scripts.healthcheck as healthcheck
    from lib.intelligence.health import _defense_request

    request = _defense_request("defense_releases_rss", content_type=9)
    destination = request.allowed_final_urls[-1]
    calls = []

    class Response:
        status = 200
        headers = Message()

        def geturl(self):
            return destination

        def close(self):
            pass

    class Opener:
        def open(self, outgoing, timeout):
            calls.append(outgoing.full_url)
            if len(calls) == 1:
                headers = Message()
                headers["Location"] = destination
                raise HTTPError(outgoing.full_url, 302, "redirect", headers, None)
            return Response()

    monkeypatch.setattr(
        healthcheck.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("automatic redirect transport was used")
        ),
    )
    monkeypatch.setattr(
        healthcheck.urllib.request, "build_opener", lambda *_handlers: Opener()
    )

    assert healthcheck._probe(request) == destination
    assert calls == [request.resolved_url(), destination]
