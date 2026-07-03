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
