"""Tests for lib/edgar.py — SEC EDGAR filings + ownership lookups (no API key needed)."""
import json
import urllib.error

import pytest


def _edgar_call(fn, *args, **kwargs):
    """Call an EDGAR function; skip the test on transient network errors."""
    try:
        return fn(*args, **kwargs)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            pytest.skip(f"SEC EDGAR transient error {e.code} — skip")
        raise
    except Exception as e:
        if "timed out" in str(e).lower() or "connection" in str(e).lower():
            pytest.skip(f"SEC EDGAR network error — skip: {e}")
        raise


def test_cik_known_ticker():
    """AAPL's CIK is a stable, well-known value."""
    from lib.edgar import _cik
    assert _edgar_call(_cik, "AAPL") == "0000320193"


def test_cik_unknown_ticker():
    """A ticker EDGAR doesn't track returns None, not an exception."""
    from lib.edgar import _cik
    assert _edgar_call(_cik, "NOTAREALTICKERXYZ") is None


def test_cik_map_cached_to_disk():
    """First resolution creates the on-disk cache file with a fetched date + map."""
    from lib.edgar import CIK_MAP_PATH, _cik
    _edgar_call(_cik, "AAPL")
    assert CIK_MAP_PATH.exists()
    cached = json.loads(CIK_MAP_PATH.read_text())
    assert "fetched" in cached and "AAPL" in cached["map"]


def test_cik_map_refreshes_when_stale():
    """A cache file older than 30 days is refetched, not trusted."""
    from lib import edgar
    original = edgar.CIK_MAP_PATH.read_text() if edgar.CIK_MAP_PATH.exists() else None
    edgar.CIK_MAP_PATH.write_text(json.dumps({"fetched": "2020-01-01", "map": {"FAKE": "0000000001"}}))
    try:
        result = _edgar_call(edgar._cik, "AAPL")
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
        result = _edgar_call(edgar._cik, "AAPL")
        assert result == "0000320193"  # corrupt cache was ignored, real map was fetched
    finally:
        if original is not None:
            edgar.CIK_MAP_PATH.write_text(original)


def test_recent_filings_structure():
    """recent_filings returns real, sorted, correctly-typed rows for a known filer."""
    from lib.edgar import recent_filings
    rows = _edgar_call(recent_filings, "AAPL", limit=5)
    assert isinstance(rows, list) and len(rows) <= 5
    for r in rows:
        assert set(r) == {"form", "filed_date", "accession_no"}
        assert r["form"] in ("10-K", "10-Q", "8-K")
    for i in range(len(rows) - 1):
        assert rows[i]["filed_date"] >= rows[i + 1]["filed_date"]


def test_recent_filings_unknown_ticker():
    """An unmapped ticker degrades to [] rather than raising."""
    from lib.edgar import recent_filings
    assert _edgar_call(recent_filings, "NOTAREALTICKERXYZ") == []


def test_ownership_filings_structure():
    """ownership_filings returns real, sorted 13D/13G rows for a known filer."""
    from lib.edgar import ownership_filings
    rows = _edgar_call(ownership_filings, "AAPL", limit=5)
    assert isinstance(rows, list) and len(rows) <= 5
    for r in rows:
        assert set(r) == {"form", "filed_date", "accession_no"}
        assert r["form"] in ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")
