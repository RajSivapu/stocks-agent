"""SEC EDGAR filings + beneficial-ownership lookups (tertiary data source).

Unauthenticated (SEC requires only a descriptive User-Agent, no API key). Uses 13D/13G
beneficial-ownership disclosures (filed against the target company when a holder crosses
5% ownership) rather than literal 13F fund-holdings data — see
docs/superpowers/specs/2026-07-02-sec-edgar-equity-research-design.md for why. Only reads
each company's `filings.recent` page (no pagination to older filings) — fine for "recent"
lookups, not a full filing-history archive. All HTTP uses stdlib urllib (no requests).
"""
import datetime
import json
import ssl
import urllib.request

from lib import config

ctx = ssl.create_default_context()
UA = {"User-Agent": "stocks-agent (rupesh.sivapu@gmail.com)"}
CIK_MAP_PATH = config.ROOT / "data" / "edgar_cik_map.json"
CIK_MAP_MAX_AGE_DAYS = 30


def _get(u, t=20):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t, context=ctx).read())


def _fetch_cik_map():
    """Download SEC's full ticker->CIK mapping and cache it to disk."""
    data = _get("https://www.sec.gov/files/company_tickers.json")
    m = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
    CIK_MAP_PATH.write_text(json.dumps({"fetched": str(datetime.date.today()), "map": m}))
    return m


def _load_cik_map():
    """Ticker->10-digit-CIK map, cached to disk and refreshed if >30 days old."""
    if CIK_MAP_PATH.exists():
        cached = json.loads(CIK_MAP_PATH.read_text())
        fetched = datetime.date.fromisoformat(cached["fetched"])
        if (datetime.date.today() - fetched).days <= CIK_MAP_MAX_AGE_DAYS:
            return cached["map"]
    return _fetch_cik_map()


def _cik(sym):
    """10-digit zero-padded CIK for `sym`, or None if EDGAR doesn't track it."""
    return _load_cik_map().get(sym.upper())
