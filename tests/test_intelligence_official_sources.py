import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lib.intelligence.http import HttpResult, SourceFailure
from lib.intelligence.providers import CollectionQuery, build_adapter
from lib.intelligence.providers.rss import parse_bounded_feed
from lib.intelligence.providers.white_house import (
    parse_white_house_listing,
    parse_white_house_sitemap,
)
from lib.intelligence.quota import QuotaSession
from lib.intelligence.planner import load_source_capabilities


FIXTURES = Path(__file__).parent / "fixtures" / "intelligence"
NOW = datetime(2026, 9, 8, 11, 30, tzinfo=timezone.utc)
START = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)


class FixtureHttp:
    def __init__(
        self,
        body: bytes | dict,
        *,
        url: str,
        content_type: str,
    ):
        self.body = json.dumps(body).encode() if isinstance(body, dict) else body
        self.url = url
        self.content_type = content_type
        self.requests = []

    def get(self, request):
        self.requests.append(request)
        return HttpResult(
            url=self.url,
            status=200,
            headers={"content-type": self.content_type},
            body=self.body,
            retrieved_at=NOW,
            observed_at=NOW,
        )


def query(capability_id, **overrides):
    values = dict(
        text="official market theme",
        symbols=(),
        start=START,
        end=NOW,
        limit=20,
        capability_id=capability_id,
        overlap_seconds=7_200,
        next_retry_phase="post-market",
    )
    values.update(overrides)
    return CollectionQuery(**values)


def adapter(provider, http, **kwargs):
    return build_adapter(
        provider,
        http,
        QuotaSession({provider: ({"reservation_id": f"{provider}-1", "reserved_requests": 2},)}),
        clock=lambda: NOW,
        **kwargs,
    )


def test_registry_exposes_only_the_verified_official_routes():
    capabilities = load_source_capabilities()

    expected = {
        "doe_energy_news_rss": ("www.energy.gov", "/rss/energygov/2193718"),
        "eia_today_in_energy_rss": ("www.eia.gov", "/rss/todayinenergy.xml"),
        "eia_press_releases_rss": ("www.eia.gov", "/rss/press_rss.xml"),
        "defense_releases_rss": (
            "www.war.gov", "/DesktopModules/ArticleCS/RSS.ashx"
        ),
        "defense_news_rss": (
            "www.war.gov", "/DesktopModules/ArticleCS/RSS.ashx"
        ),
        "white_house_fact_sheets": ("www.whitehouse.gov", "/fact-sheets/"),
        "white_house_presidential_actions": (
            "www.whitehouse.gov", "/presidential-actions/"
        ),
        "white_house_briefings_statements": (
            "www.whitehouse.gov", "/briefings-statements/"
        ),
        "white_house_sitemap": ("www.whitehouse.gov", "/sitemap_index.xml"),
        "sec_filing_document": ("www.sec.gov", "/Archives/edgar/data/#"),
    }
    for capability_id, (host, path) in expected.items():
        query_pack = capabilities[capability_id].query_pack["default"]
        assert query_pack["host"] == host
        assert query_pack["path"] == path

    assert capabilities["defense_releases_rss"].allowed_hosts == frozenset({
        "www.defense.gov", "www.war.gov"
    })
    assert capabilities["eia_statistics_v2"].enabled is False
    assert capabilities["eia_statistics_v2"].health == "configuration_missing"


def test_bounded_rss_parser_accepts_ticker_free_doe_items():
    items = parse_bounded_feed(
        (FIXTURES / "doe_energy_news.xml").read_bytes(),
        source_url="https://www.energy.gov/rss/energygov/2193718",
        max_bytes=100_000,
        max_items=10,
    )

    assert [item.upstream_item_id for item in items] == ["doe-energy-1001", "doe-energy-1000"]
    assert items[0].url == "https://www.energy.gov/articles/grid-manufacturing-awards"
    assert items[0].published_at == datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


def test_bounded_feed_parser_accepts_atom_entries():
    raw = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>atom-1</id><title>Grid update</title>
        <link rel="alternate" href="https://www.energy.gov/articles/grid-update" />
        <updated>2026-09-06T12:00:00Z</updated><summary>Official update.</summary>
      </entry>
    </feed>'''

    items = parse_bounded_feed(
        raw,
        source_url="https://www.energy.gov/rss/energygov/2193718",
        max_bytes=10_000,
        max_items=10,
        allowed_hosts=frozenset({"www.energy.gov"}),
    )

    assert [(item.upstream_item_id, item.title) for item in items] == [
        ("atom-1", "Grid update")
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("guid", "&lt;script&gt;identity&lt;/script&gt;"),
        ("title", "&lt;img src=x onerror=alert(1)&gt;"),
        ("description", "javascript:alert(1)"),
    ],
)
def test_bounded_feed_rejects_decoded_active_markup_in_retained_fields(field, value):
    values = {
        "guid": "safe-id",
        "title": "Safe title",
        "description": "Safe summary",
    }
    values[field] = value
    raw = f"""<rss><channel><item><guid>{values['guid']}</guid>
      <title>{values['title']}</title>
      <link>https://www.energy.gov/articles/grid-update</link>
      <description>{values['description']}</description>
      <pubDate>Mon, 07 Sep 2026 14:00:00 GMT</pubDate>
    </item></channel></rss>""".encode()

    with pytest.raises(SourceFailure, match="INVALID_FEED"):
        parse_bounded_feed(
            raw,
            source_url="https://www.energy.gov/rss/energygov/2193718",
            max_bytes=100_000,
            max_items=10,
        )


@pytest.mark.parametrize(
    "raw",
    [
        b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss>&xxe;</rss>',
        b"<html><script>alert(1)</script></html>",
        b"<rss><channel><item><guid>x</guid><title>X</title><link>http://energy.gov/x</link><pubDate>bad</pubDate></item></channel></rss>",
    ],
)
def test_bounded_rss_parser_rejects_entities_script_html_and_unsafe_items(raw):
    with pytest.raises(SourceFailure, match="INVALID_FEED"):
        parse_bounded_feed(
            raw,
            source_url="https://www.energy.gov/rss/energygov/2193718",
            max_bytes=100_000,
            max_items=10,
        )


def test_doe_adapter_accepts_valid_xml_even_when_mime_is_text_html():
    source_url = "https://www.energy.gov/rss/energygov/2193718"
    http = FixtureHttp(
        (FIXTURES / "doe_energy_news.xml").read_bytes(),
        url=source_url,
        content_type="text/html",
    )

    result = adapter("doe", http).collect(query("doe_energy_news_rss"))

    assert http.requests[0].url == source_url
    assert [item.title for item in result.items] == [
        "Department of Energy Announces Grid Manufacturing Awards",
        "New Critical Minerals Demonstration Program",
    ]
    assert all(item.security_ids == () for item in result.items)
    assert result.receipt.metadata == {
        "backlog_remaining": False,
        "capability_id": "doe_energy_news_rss",
        "coverage_status": "success_nonempty",
        "cursor_end": "2026-09-08T11:30:00+00:00",
        "cursor_start": "2026-09-04T18:00:00+00:00",
            "next_retry_phase": "post-market",
            "overlap_seconds": 7200,
            "page": 1,
            "truncated": False,
            "exhausted": True,
    }


def test_unpageable_feed_overflow_is_a_truthful_frozen_coverage_gap():
    source_url = "https://www.energy.gov/rss/energygov/2193718"
    entries = "".join(
        f"""<item><guid>doe-{index}</guid><title>Energy item {index}</title>
        <link>https://www.energy.gov/articles/energy-item-{index}</link>
        <description>Official summary.</description>
        <pubDate>Mon, 07 Sep 2026 14:00:00 GMT</pubDate></item>"""
        for index in range(21)
    )
    http = FixtureHttp(
        f"<rss><channel>{entries}</channel></rss>".encode(),
        url=source_url,
        content_type="application/rss+xml",
    )

    result = adapter("doe", http).collect(query("doe_energy_news_rss", limit=20))

    assert len(result.items) == 20
    assert result.receipt.returned_count == 21
    assert result.receipt.metadata["truncated"] is True
    assert result.receipt.metadata["backlog_remaining"] is True
    assert result.receipt.metadata["continuation_unavailable"] is True
    assert result.receipt.metadata["coverage_gap"] is True
    assert "backlog_token" not in result.receipt.metadata


def test_official_feed_rejects_an_unapproved_item_path():
    source_url = "https://www.energy.gov/rss/energygov/2193718"
    raw = b"""<rss><channel><item><guid>doe-admin</guid><title>Unsafe path</title>
      <link>https://www.energy.gov/admin/config</link><description>Summary.</description>
      <pubDate>Mon, 07 Sep 2026 14:00:00 GMT</pubDate></item></channel></rss>"""
    result = adapter(
        "doe", FixtureHttp(raw, url=source_url, content_type="application/rss+xml")
    ).collect(query("doe_energy_news_rss"))

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "INVALID_FEED"


@pytest.mark.parametrize(
    "capability_id,expected_url",
    [
        ("eia_today_in_energy_rss", "https://www.eia.gov/rss/todayinenergy.xml"),
        ("eia_press_releases_rss", "https://www.eia.gov/rss/press_rss.xml"),
    ],
)
def test_eia_rss_routes_are_keyless_and_exact(capability_id, expected_url):
    http = FixtureHttp(
        (FIXTURES / "eia_today_in_energy.xml").read_bytes(),
        url=expected_url,
        content_type="application/rss+xml",
    )
    result = adapter(
        "eia",
        http,
        secret_getter=lambda name: (_ for _ in ()).throw(AssertionError(name)),
    ).collect(query(capability_id))

    assert http.requests[0].url == expected_url
    assert result.items[0].provider == "eia"
    assert result.items[0].security_ids == ()


def test_eia_statistics_without_free_key_is_configuration_missing_before_transport():
    http = FixtureHttp({}, url="https://api.eia.gov/v2/", content_type="application/json")
    with pytest.raises(SourceFailure) as failure:
        adapter(
            "eia",
            http,
            secret_getter=lambda _name: "",
        ).collect(query("eia_statistics_v2", series_id="electricity/rto/region-data"))

    assert http.requests == []
    assert failure.value.code == "CONFIGURATION_MISSING"


def test_eia_statistics_uses_only_the_configured_route_with_a_free_key():
    route = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
    http = FixtureHttp(
        {"response": {"data": [{"period": "2026-09-07", "value": 10}]}},
        url=f"{route}?api_key=free-key&start=2026-09-04&end=2026-09-08&length=20",
        content_type="application/json",
    )

    result = adapter(
        "eia", http, secret_getter=lambda name: "free-key" if name == "eia_api_key" else ""
    ).collect(query("eia_statistics_v2", series_id="electricity/rto/region-data"))

    assert http.requests[0].url == http.url
    assert result.items[0].request_url == route
    assert "free-key" not in result.items[0].canonical_content
    assert result.items[0].authority == "official_energy_statistics"


@pytest.mark.parametrize("content_type", (1, 9))
def test_defense_feed_requests_verified_war_route_directly_once(content_type):
    source = (
        "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx"
        f"?ContentType={content_type}&Site=945&max=10"
    )
    raw = b"""<?xml version='1.0'?><rss><channel><item><guid>dod-1</guid><title>Defense industrial award</title><link>https://www.war.gov/News/Releases/Release/Article/1/award/</link><description>Official release.</description><pubDate>Mon, 07 Sep 2026 14:00:00 GMT</pubDate></item></channel></rss>"""
    http = FixtureHttp(raw, url=source, content_type="text/xml")

    result = adapter("dod", http).collect(query(
        "defense_news_rss" if content_type == 1 else "defense_releases_rss"
    ))

    assert http.requests[0].url == source
    assert len(http.requests) == 1
    assert len(result.items) == 1
    assert urlsplit(result.items[0].source_url).hostname == "www.war.gov"
    assert result.items[0].authority == (
        "official_defense_news" if content_type == 1 else "official_defense_statement"
    )


def test_white_house_page_bound_is_capability_specific():
    query("white_house_sitemap", page=21)

    with pytest.raises(ValueError, match="page"):
        query("federal_register_documents", page=21)

    SourceCursor = __import__(
        "lib.intelligence.cursors", fromlist=["SourceCursor"]
    ).SourceCursor
    SourceCursor(provider="white_house", capability_id="white_house_sitemap", page=21)
    with pytest.raises(ValueError, match="page"):
        SourceCursor(provider="federal_register", capability_id="federal_register_documents", page=21)


def test_defense_feed_rejects_a_redirect_that_changes_the_reviewed_query():
    source = "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=10"
    http = FixtureHttp(
        (FIXTURES / "doe_energy_news.xml").read_bytes(),
        url="https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=100",
        content_type="text/xml",
    )

    result = adapter("dod", http).collect(query("defense_releases_rss"))

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "UNSAFE_URL"
    assert result.receipt.metadata["coverage_status"] == "source_failed"


def test_white_house_listing_keeps_only_approved_news_path_and_real_page_cursor():
    records, next_page = parse_white_house_listing(
        (FIXTURES / "white_house_fact_sheets.html").read_bytes(),
        source_url="https://www.whitehouse.gov/fact-sheets/",
        max_bytes=300_000,
        max_items=20,
    )

    assert [record.url for record in records] == [
        "https://www.whitehouse.gov/fact-sheets/2026/09/fact-sheet-grid-resilience/"
    ]
    assert records[0].published_at == datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
    assert next_page == "https://www.whitehouse.gov/fact-sheets/page/2/"


def test_white_house_sitemap_index_keeps_only_current_post_sitemaps():
    parsed = parse_white_house_sitemap(
        (FIXTURES / "white_house_sitemap.xml").read_bytes(),
        source_url="https://www.whitehouse.gov/sitemap_index.xml",
        max_bytes=300_000,
        max_items=20,
    )

    assert parsed.child_sitemaps == (
        "https://www.whitehouse.gov/post-sitemap.xml",
        "https://www.whitehouse.gov/post-sitemap2.xml",
    )
    assert parsed.items == ()


def test_white_house_sitemap_cursor_visits_every_discovered_child_with_index_and_offset():
    index_url = "https://www.whitehouse.gov/sitemap_index.xml"
    child_one = "https://www.whitehouse.gov/post-sitemap.xml"
    child_two = "https://www.whitehouse.gov/post-sitemap2.xml"
    index = (FIXTURES / "white_house_sitemap.xml").read_bytes()
    pages = {
        index_url: index,
        child_one: b"""<urlset><url><loc>https://www.whitehouse.gov/fact-sheets/2026/09/one/</loc></url></urlset>""",
        child_two: b"""<urlset><url><loc>https://www.whitehouse.gov/presidential-actions/2026/09/two/</loc></url></urlset>""",
    }

    class SitemapHttp:
        def __init__(self):
            self.requests = []

        def get(self, request):
            self.requests.append(request)
            return HttpResult(
                url=request.url, status=200, headers={"content-type": "text/xml"},
                body=pages[request.url], retrieved_at=NOW, observed_at=NOW,
            )

    http = SitemapHttp()
    source = adapter("white_house", http)
    source.quota = QuotaSession({
        "white_house": ({"reservation_id": "white-house-pages", "reserved_requests": 3},)
    })
    first = source.collect(query("white_house_sitemap"))
    first_token = first.receipt.metadata["backlog_token"]
    assert first.receipt.metadata["sitemap_child_index"] == 0
    assert first.receipt.metadata["sitemap_offset"] == 0

    second = source.collect(query("white_house_sitemap", cursor_token=first_token))
    second_token = second.receipt.metadata["backlog_token"]
    assert second.items[0].source_url.endswith("/one/")
    assert second.receipt.metadata["sitemap_child_index"] == 1
    assert second.receipt.metadata["sitemap_offset"] == 0

    third = source.collect(query("white_house_sitemap", cursor_token=second_token))
    assert third.items[0].source_url.endswith("/two/")
    assert third.receipt.metadata["backlog_remaining"] is False
    assert "backlog_token" not in third.receipt.metadata
    assert [request.url for request in http.requests] == [index_url, child_one, child_two]


def test_white_house_sitemap_can_traverse_all_twenty_admitted_children():
    index_url = "https://www.whitehouse.gov/sitemap_index.xml"
    children = tuple(
        f"https://www.whitehouse.gov/post-sitemap{index or ''}.xml"
        for index in range(20)
    )
    index = (
        "<sitemapindex>" + "".join(
            f"<sitemap><loc>{child}</loc></sitemap>" for child in children
        ) + "</sitemapindex>"
    ).encode()
    pages = {index_url: index}
    pages.update({
        child: (
            "<urlset><url><loc>"
            f"https://www.whitehouse.gov/fact-sheets/2026/09/item-{number}/"
            "</loc></url></urlset>"
        ).encode()
        for number, child in enumerate(children)
    })

    class SitemapHttp:
        def __init__(self):
            self.requests = []

        def get(self, request):
            self.requests.append(request)
            return HttpResult(
                url=request.url, status=200, headers={"content-type": "text/xml"},
                body=pages[request.url], retrieved_at=NOW, observed_at=NOW,
            )

    http = SitemapHttp()
    source = adapter("white_house", http)
    source.quota = QuotaSession({
        "white_house": ({
            "reservation_id": "white-house-twenty", "reserved_requests": 21,
        },),
    })
    result = source.collect(query("white_house_sitemap", page=1))
    found = []
    page = 2
    while result.receipt.metadata["backlog_remaining"]:
        token = result.receipt.metadata["backlog_token"]
        result = source.collect(query(
            "white_house_sitemap", cursor_token=token, page=page,
        ))
        found.extend(item.source_url for item in result.items)
        page += 1

    assert len(http.requests) == 21
    assert len(found) == 20
    assert result.receipt.metadata["exhausted"] is True


def test_white_house_post_sitemap_filters_paths_and_does_not_treat_lastmod_as_publication():
    raw = b"""<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>https://www.whitehouse.gov/fact-sheets/2026/09/grid/</loc><lastmod>2026-09-08T09:00:00Z</lastmod></url><url><loc>https://www.whitehouse.gov/about-the-white-house/</loc><lastmod>2026-09-08T09:00:00Z</lastmod></url></urlset>"""
    parsed = parse_white_house_sitemap(
        raw,
        source_url="https://www.whitehouse.gov/post-sitemap.xml",
        max_bytes=300_000,
        max_items=20,
    )

    assert len(parsed.items) == 1
    assert parsed.items[0].url == "https://www.whitehouse.gov/fact-sheets/2026/09/grid/"
    assert parsed.items[0].published_at is None
    assert parsed.items[0].metadata["last_modified"] == "2026-09-08T09:00:00Z"


def test_federal_register_continuation_uses_validated_search_after_cursor():
    payload = {
        "count": 2,
        "next_page_url": "https://www.federalregister.gov/api/v1/documents?format=json&search_after=cursor-2",
        "results": [{
            "document_number": "2026-12345",
            "type": "Rule",
            "title": "Grid rule",
            "abstract": "Rule summary",
            "html_url": "https://www.federalregister.gov/documents/2026/09/07/2026-12345/grid-rule",
            "publication_date": "2026-09-07",
            "effective_on": "2026-10-01",
        }],
    }
    http = FixtureHttp(
        payload,
        url="https://www.federalregister.gov/api/v1/documents.json",
        content_type="application/json",
    )

    first = adapter("federal_register", http).collect(query("federal_register_document_search"))

    assert first.receipt.metadata["truncated"] is True
    assert first.receipt.metadata["backlog_remaining"] is True
    assert first.receipt.metadata["backlog_token"] == "cursor-2"
    assert first.items[0].metadata["document_status"] == "Rule"
    assert first.items[0].effective_at == datetime(2026, 10, 1, tzinfo=timezone.utc)

    continued_http = FixtureHttp(
        {"count": 0, "next_page_url": None, "results": []},
        url="https://www.federalregister.gov/api/v1/documents?format=json&search_after=cursor-2",
        content_type="application/json",
    )
    adapter("federal_register", continued_http).collect(query(
        "federal_register_document_search", cursor_token="cursor-2"
    ))
    params = parse_qs(urlsplit(continued_http.requests[0].url).query)
    assert params["search_after"] == ["cursor-2"]


def test_federal_register_rejects_a_repeated_search_after_cursor():
    next_url = (
        "https://www.federalregister.gov/api/v1/documents"
        "?format=json&search_after=cursor-2"
    )
    http = FixtureHttp(
        {"count": 0, "next_page_url": next_url, "results": []},
        url=next_url,
        content_type="application/json",
    )

    result = adapter("federal_register", http).collect(query(
        "federal_register_document_search", cursor_token="cursor-2"
    ))

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "INVALID_RESPONSE"


def test_federal_register_drops_document_url_that_does_not_match_document_identity():
    payload = {
        "count": 1,
        "next_page_url": None,
        "results": [{
            "document_number": "2026-12345",
            "type": "Rule",
            "title": "Grid rule",
            "abstract": "Rule summary",
            "html_url": "https://www.federalregister.gov/documents/2026/09/07/WRONG/grid-rule",
            "publication_date": "2026-09-07",
        }],
    }
    http = FixtureHttp(
        payload,
        url="https://www.federalregister.gov/api/v1/documents.json",
        content_type="application/json",
    )

    result = adapter("federal_register", http).collect(
        query("federal_register_document_search")
    )

    assert result.items == ()
    assert result.receipt.returned_count == 0


def test_sec_routes_require_configured_contact_and_validate_filing_identity():
    missing_http = FixtureHttp({}, url="https://data.sec.gov/submissions/CIK0000000001.json", content_type="application/json")
    with pytest.raises(SourceFailure) as failure:
        adapter(
            "sec_edgar", missing_http, secret_getter=lambda _name: ""
        ).collect(query("sec_issuer_submissions", cik="1"))
    assert missing_http.requests == []
    assert failure.value.code == "CONFIGURATION_MISSING"

    submissions_http = FixtureHttp(
        {
            "cik": "0000000001",
            "name": "Test Issuer",
            "filings": {"recent": {
                "accessionNumber": ["0000000001-26-000001"],
                "filingDate": ["2026-09-07"],
                "reportDate": ["2026-09-01"],
                "form": ["8-K"],
                "primaryDocument": ["test-8k.htm"],
            }},
        },
        url="https://data.sec.gov/submissions/CIK0000000001.json",
        content_type="application/json",
    )
    result = adapter(
        "sec_edgar", submissions_http,
        secret_getter=lambda name: "owner@example.com" if name == "sec_user_agent_contact" else "",
    ).collect(query("sec_issuer_submissions", cik="1"))
    assert submissions_http.requests[0].headers == {
        "User-Agent": "stocks-agent owner research contact=owner@example.com"
    }
    assert result.items[0].entity_ids == ("cik:0000000001",)
    assert result.items[0].authority == "official"

    filing_http = FixtureHttp(
        b"<html><body><h1>Item 2</h1><p>Our official market theme business manufactures equipment.</p></body></html>",
        url="https://www.sec.gov/Archives/edgar/data/1/000000000126000001/test-8k.htm",
        content_type="text/html",
    )
    filing = adapter(
        "sec_edgar", filing_http,
        secret_getter=lambda name: "owner@example.com" if name == "sec_user_agent_contact" else "",
    ).collect(query(
        "sec_filing_document",
        cik="1",
        accession_number="0000000001-26-000001",
        primary_document="test-8k.htm",
    ))
    assert filing_http.requests[0].url == filing_http.url
    assert filing.items[0].upstream_item_id == "0000000001-26-000001:test-8k.htm"
    assert filing.items[0].authority == "official"
    assert filing.items[0].published_at is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"cik": "2"},
        {"primaryDocument": ["../other-8k.htm"]},
    ],
)
def test_sec_submissions_payload_identity_must_match_requested_cik_and_routes(mutation):
    recent = {
        "accessionNumber": ["0000000001-26-000001"],
        "filingDate": ["2026-09-07"],
        "reportDate": ["2026-09-01"],
        "form": ["8-K"],
        "primaryDocument": ["test-8k.htm"],
    }
    payload = {"cik": "1", "name": "Test Issuer", "filings": {"recent": recent}}
    if "cik" in mutation:
        payload["cik"] = mutation["cik"]
    else:
        recent.update(mutation)
    http = FixtureHttp(
        payload,
        url="https://data.sec.gov/submissions/CIK0000000001.json",
        content_type="application/json",
    )

    result = adapter(
        "sec_edgar", http,
        secret_getter=lambda name: "owner@example.com" if name == "sec_user_agent_contact" else "",
    ).collect(query("sec_issuer_submissions", cik="1"))

    assert result.items == ()
    assert result.receipt.status == "failed"
    assert result.receipt.error_code == "INVALID_RESPONSE"


def test_sec_filing_route_rejects_path_injection_before_http():
    http = FixtureHttp(b"<html></html>", url="https://www.sec.gov/", content_type="text/html")
    with pytest.raises(SourceFailure) as failure:
        adapter(
            "sec_edgar", http,
            secret_getter=lambda name: "owner@example.com" if name == "sec_user_agent_contact" else "",
        ).collect(query(
            "sec_filing_document",
            cik="1",
            accession_number="0000000001-26-000001",
            primary_document="../secret.htm",
        ))

    assert http.requests == []
    assert failure.value.code == "INVALID_QUERY"
