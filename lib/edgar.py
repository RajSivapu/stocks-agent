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
    """Ticker->10-digit-CIK map, cached to disk and refreshed if >30 days old.

    A corrupted or partially-written cache file (e.g. process killed mid-write) is
    treated the same as a missing one: self-heal by refetching rather than raising.
    """
    if CIK_MAP_PATH.exists():
        try:
            cached = json.loads(CIK_MAP_PATH.read_text())
            fetched = datetime.date.fromisoformat(cached["fetched"])
            if (datetime.date.today() - fetched).days <= CIK_MAP_MAX_AGE_DAYS:
                return cached["map"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return _fetch_cik_map()


def _cik(sym):
    """10-digit zero-padded CIK for `sym`, or None if EDGAR doesn't track it."""
    return _load_cik_map().get(sym.upper())


def _get_filings(sym, forms, limit):
    """Up to `limit` most-recent filings of any type in `forms` for `sym`, newest first.

    Returns [] if `sym` has no CIK mapping, no matching filings, or any lookup step
    (CIK resolution, HTTP fetch) fails — never raises. Only inspects the submissions
    API's inline `filings.recent` page (see module docstring).
    """
    try:
        cik10 = _cik(sym)
        if not cik10:
            return []
        j = _get(f"https://data.sec.gov/submissions/CIK{cik10}.json")
        recent = j.get("filings", {}).get("recent", {})
        rows = [
            {"form": f, "filed_date": d, "accession_no": a}
            for f, d, a in zip(recent.get("form", []), recent.get("filingDate", []), recent.get("accessionNumber", []))
            if f in forms
        ]
        rows.sort(key=lambda r: r["filed_date"], reverse=True)
        return rows[:limit]
    except Exception:
        return []


def parse_submissions(payload, limit=50):
    """Normalize one SEC submissions payload without performing I/O."""
    if not isinstance(payload, dict) or not isinstance(payload.get("filings"), dict):
        raise ValueError("invalid SEC submissions response")
    recent = payload["filings"].get("recent")
    if not isinstance(recent, dict):
        raise ValueError("invalid SEC submissions response")
    columns = (
        recent.get("accessionNumber"), recent.get("filingDate"),
        recent.get("reportDate"), recent.get("form"), recent.get("primaryDocument"),
    )
    if any(not isinstance(column, list) for column in columns):
        raise ValueError("invalid SEC submissions response")
    cik = str(payload.get("cik") or "").zfill(10)
    issuer = str(payload.get("name") or "SEC issuer")
    rows = []
    for accession, filing_date, report_date, form, document in zip(*columns):
        accession = str(accession)
        accession_path = accession.replace("-", "")
        cik_path = cik.lstrip("0") or "0"
        rows.append({
            "upstream_item_id": accession,
            "source_url": (
                f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
                f"{accession_path}/{document}"
            ),
            "title": f"{issuer} {form}",
            "text": f"SEC filing {form}, accession {accession}",
            "published_at": filing_date,
            "effective_at": report_date or None,
            "metadata": {"cik": cik, "form": form, "accession_number": accession},
        })
        if len(rows) >= limit:
            break
    return rows


def recent_filings(sym, forms=("10-K", "10-Q", "8-K"), limit=5):
    """Recent material filings for `sym` (default: 10-K/10-Q/8-K), newest first.

    Guidance: only worth surfacing in a research note if the newest `filed_date` is
    within ~10 days of today — anything older isn't a fresh catalyst.
    """
    return _get_filings(sym, forms, limit)


def ownership_filings(sym, forms=("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"), limit=5):
    """Recent 5%+ beneficial-ownership disclosures for `sym`, newest first.

    Not literal 13F fund-holdings data (see module docstring). An INITIAL filing ("SC 13D"
    or "SC 13G", no "/A") unambiguously means someone just crossed 5% ownership upward —
    13D implies stated intent to influence (often an activist catalyst), 13G a passive
    institution. An AMENDMENT ("SC 13D/A", "SC 13G/A") can reflect an increase, decrease,
    or admin change — this data (form + date only, no share counts) can't tell which; treat
    amendments as a neutral "something changed" flag, never as bullish or bearish on their
    own. Guidance: only worth surfacing if the newest `filed_date` is within ~90 days of
    today — these lag the actual purchase and are a periodic signal, never a timing one.
    """
    return _get_filings(sym, forms, limit)
