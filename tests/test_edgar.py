"""Tests for SEC EDGAR access using only deterministic transport doubles."""
import json
from io import BytesIO

import pytest


@pytest.fixture(autouse=True)
def sec_transport(monkeypatch, tmp_path):
    from lib import edgar

    calls = []
    tickers = {"0": {"ticker": "AAPL", "cik_str": 320193}}
    submissions = {
        "filings": {
            "recent": {
                "form": ["8-K", "10-Q", "SC 13G", "SC 13D/A"],
                "filingDate": ["2026-09-06", "2026-08-01", "2026-07-01", "2026-06-01"],
                "accessionNumber": ["a", "b", "c", "d"],
            }
        }
    }

    class Response:
        def __init__(self, payload):
            self.body = BytesIO(json.dumps(payload).encode())

        def read(self):
            return self.body.read()

    def urlopen(request, **_kwargs):
        calls.append(request)
        payload = submissions if "/submissions/" in request.full_url else tickers
        return Response(payload)

    monkeypatch.setattr(edgar, "CIK_MAP_PATH", tmp_path / "edgar_cik_map.json")
    monkeypatch.setattr(
        edgar.config,
        "optional_secret",
        lambda name: "owner@example.com" if name == "sec_user_agent_contact" else "",
    )
    monkeypatch.setattr(edgar.urllib.request, "urlopen", urlopen)
    return calls


def test_cik_known_ticker():
    """AAPL's CIK is a stable, well-known value."""
    from lib.edgar import _cik
    assert _cik("AAPL") == "0000320193"


def test_cik_unknown_ticker():
    """A ticker EDGAR doesn't track returns None, not an exception."""
    from lib.edgar import _cik
    assert _cik("NOTAREALTICKERXYZ") is None


def test_cik_map_cached_to_disk():
    """First resolution creates the on-disk cache file with a fetched date + map."""
    from lib.edgar import CIK_MAP_PATH, _cik
    _cik("AAPL")
    assert CIK_MAP_PATH.exists()
    cached = json.loads(CIK_MAP_PATH.read_text())
    assert "fetched" in cached and "AAPL" in cached["map"]


def test_cik_map_refreshes_when_stale():
    """A cache file older than 30 days is refetched, not trusted."""
    from lib import edgar
    original = edgar.CIK_MAP_PATH.read_text() if edgar.CIK_MAP_PATH.exists() else None
    edgar.CIK_MAP_PATH.write_text(json.dumps({"fetched": "2020-01-01", "map": {"FAKE": "0000000001"}}))
    try:
        result = edgar._cik("AAPL")
        assert result == "0000320193"  # real map was refetched, not the stale fake one
    finally:
        if original is not None:
            edgar.CIK_MAP_PATH.write_text(original)


def test_cik_map_self_heals_from_corrupt_cache():
    """A corrupted/unparseable cache file is refetched, not raised."""
    from lib import edgar
    original = edgar.CIK_MAP_PATH.read_text() if edgar.CIK_MAP_PATH.exists() else None
    edgar.CIK_MAP_PATH.write_text("not valid json{{{")
    try:
        result = edgar._cik("AAPL")
        assert result == "0000320193"  # corrupt cache was ignored, real map was fetched
    finally:
        if original is not None:
            edgar.CIK_MAP_PATH.write_text(original)


def test_recent_filings_structure():
    """recent_filings returns real, sorted, correctly-typed rows for a known filer."""
    from lib.edgar import recent_filings
    rows = recent_filings("AAPL", limit=5)
    assert isinstance(rows, list) and len(rows) <= 5
    for r in rows:
        assert set(r) == {"form", "filed_date", "accession_no"}
        assert r["form"] in ("10-K", "10-Q", "8-K")
    for i in range(len(rows) - 1):
        assert rows[i]["filed_date"] >= rows[i + 1]["filed_date"]


def test_recent_filings_unknown_ticker():
    """An unmapped ticker degrades to [] rather than raising."""
    from lib.edgar import recent_filings
    assert recent_filings("NOTAREALTICKERXYZ") == []


def test_ownership_filings_structure():
    """ownership_filings returns real, sorted 13D/13G rows for a known filer."""
    from lib.edgar import ownership_filings
    rows = ownership_filings("AAPL", limit=5)
    assert isinstance(rows, list) and len(rows) <= 5
    for r in rows:
        assert set(r) == {"form", "filed_date", "accession_no"}
        assert r["form"] in ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")


def test_sec_transport_requires_contact_before_open(monkeypatch, sec_transport):
    from lib import edgar

    monkeypatch.setattr(edgar.config, "optional_secret", lambda _name: "")

    with pytest.raises(ValueError, match="SEC_USER_AGENT_CONTACT"):
        edgar._get("https://www.sec.gov/files/company_tickers.json")

    assert sec_transport == []


def test_sec_transport_sends_configured_contact(sec_transport):
    from lib import edgar

    edgar._get("https://www.sec.gov/files/company_tickers.json")

    assert sec_transport[0].get_header("User-agent") == (
        "stocks-agent owner research contact=owner@example.com"
    )
