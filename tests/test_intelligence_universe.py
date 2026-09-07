from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from lib.intelligence.http import BoundedHttpClient
from lib.intelligence.universe import (
    SEC_COMPANY_TICKERS_URL,
    SEC_REFERENCE_USER_AGENT,
    SecurityIdentity,
    build_reference_transfer,
    eligible_for_research,
    merge_reference_sources,
    parse_sec_company_tickers,
    parse_symbol_directory,
    refresh_sec_reference,
)


FIXTURES = Path(__file__).parent / "fixtures" / "intelligence"
SEC_FIXTURE = FIXTURES / "sec_company_tickers.json"
NASDAQ_FIXTURE = FIXTURES / "nasdaq_listed.txt"
OTHER_FIXTURE = FIXTURES / "other_listed.txt"
NOW = datetime(2026, 9, 4, 18, 5, tzinfo=timezone.utc)
RUN_ID = "11111111-1111-4111-8111-111111111111"


def security(
    instrument: str,
    *,
    ticker: str = "TEST",
    exchange: str | None = "NASDAQ",
    eligible: bool = True,
    exclusion_reasons: tuple[str, ...] = (),
) -> SecurityIdentity:
    return SecurityIdentity(
        security_id="sec-cik:0000000001:listing-origin:TEST",
        entity_id="sec-cik:0000000001",
        ticker=ticker,
        exchange=exchange,
        instrument_type=instrument,
        valid_from=date(2026, 9, 1),
        valid_to=None,
        aliases=(ticker,),
        source_ids=("sec-company-tickers:0000000001",),
        eligible=eligible,
        exclusion_reasons=exclusion_reasons,
    )


def test_reference_keeps_cik_leading_zeroes_and_ticker_is_only_a_dated_alias():
    snapshot = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)

    mp = snapshot.by_ticker["MP"]
    assert mp.cik == "0001801368"
    assert mp.entity_id == "sec-cik:0001801368"
    assert mp.security_id == "sec-cik:0001801368:listing-origin:MP"
    assert mp.aliases == ("MP",)
    assert mp.valid_from == NOW.date()
    assert snapshot.issuers_by_id[mp.entity_id].canonical_name == "MP MATERIALS CORP."
    with pytest.raises(FrozenInstanceError):
        mp.ticker = "OTHER"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("instrument", "exchange", "eligible", "reason"),
    [
        ("COMMON_STOCK", "NASDAQ", True, None),
        ("ADR", "NYSE", True, None),
        ("ETF", "NYSEARCA", True, None),
        ("PREFERRED", "NYSE", False, "instrument_type_excluded"),
        ("WARRANT", "NYSE", False, "instrument_type_excluded"),
        ("OTC_COMMON", "OTC", False, "market_excluded"),
    ],
)
def test_suggestion_universe_is_explicit(instrument, exchange, eligible, reason):
    result = eligible_for_research(security(instrument, exchange=exchange))
    assert (result.eligible, result.reason) == (eligible, reason)


def test_existing_exclusions_fail_closed_before_instrument_eligibility():
    result = eligible_for_research(
        security("COMMON_STOCK", eligible=False, exclusion_reasons=("test_issue",))
    )
    assert (result.eligible, result.reason) == (False, "test_issue")


def test_sec_manifest_hashes_the_exact_response_bytes_and_keeps_scope_limit():
    raw = SEC_FIXTURE.read_bytes()
    compact = json.dumps(json.loads(raw), separators=(",", ":")).encode()

    original = parse_sec_company_tickers(raw, retrieved_at=NOW)
    rewritten = parse_sec_company_tickers(compact, retrieved_at=NOW)

    assert original.manifest.source_hash == hashlib.sha256(raw).hexdigest()
    assert rewritten.manifest.source_hash == hashlib.sha256(compact).hexdigest()
    assert original.manifest.source_hash != rewritten.manifest.source_hash
    assert original.manifest.coverage_status == "scope_not_guaranteed"
    assert original.manifest.parser_version == 1
    assert tuple(original.by_ticker) == tuple(rewritten.by_ticker)


def test_sec_parser_types_clear_instruments_and_rejects_malformed_rows():
    snapshot = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)

    assert snapshot.by_ticker["MP"].instrument_type == "COMMON_STOCK"
    assert snapshot.by_ticker["BABA"].instrument_type == "ADR"
    assert snapshot.by_ticker["SPY"].instrument_type == "ETF"
    assert snapshot.by_ticker["ACMEP"].instrument_type == "PREFERRED"
    assert snapshot.by_ticker["ACMEW"].instrument_type == "WARRANT"
    broken = json.dumps({"0": {"cik_str": "not-a-cik", "ticker": "", "title": "X"}}).encode()
    with pytest.raises(ValueError, match="SEC company ticker row"):
        parse_sec_company_tickers(broken, retrieved_at=NOW)


def test_sec_parser_rejects_conflicting_duplicate_symbols_instead_of_choosing_one():
    raw = json.dumps(
        {
            "0": {"cik_str": 1, "ticker": "DUPE", "title": "First Corp"},
            "1": {"cik_str": 2, "ticker": "DUPE", "title": "Second Corp"},
        }
    ).encode()
    with pytest.raises(ValueError, match="conflicting SEC ticker"):
        parse_sec_company_tickers(raw, retrieved_at=NOW)


def test_sec_parser_rejects_duplicate_security_identity_even_for_the_same_issuer():
    raw = json.dumps(
        {
            "0": {"cik_str": 1, "ticker": "DUPE", "title": "First Corp"},
            "1": {"cik_str": 1, "ticker": "DUPE", "title": "First Corp Class A"},
        }
    ).encode()

    with pytest.raises(ValueError, match="duplicate SEC security identity"):
        parse_sec_company_tickers(raw, retrieved_at=NOW)


def test_symbol_directories_retain_creation_time_and_exclude_unsupported_instruments():
    nasdaq = parse_symbol_directory(
        NASDAQ_FIXTURE, retrieved_at=NOW.replace(day=5)
    )
    other = parse_symbol_directory(OTHER_FIXTURE, retrieved_at=NOW)

    assert nasdaq.manifest.source_timestamp == datetime(
        2026, 9, 4, 18, 3, tzinfo=timezone.utc
    )
    assert other.manifest.source_timestamp == datetime(
        2026, 9, 4, 18, 4, tzinfo=timezone.utc
    )
    assert nasdaq.by_ticker["MP"].valid_from == date(2026, 9, 4)
    assert nasdaq.by_ticker["TEST"].exclusion_reasons == ("test_issue",)
    assert other.by_ticker["BABA"].instrument_type == "ADR"
    assert other.by_ticker["SPY"].instrument_type == "ETF"
    assert other.by_ticker["ACMEP"].instrument_type == "PREFERRED"
    assert other.by_ticker["ACMEW"].instrument_type == "WARRANT"
    assert other.by_ticker["OTCM"].instrument_type == "OTC_COMMON"
    assert eligible_for_research(other.by_ticker["OTCM"]).reason == "market_excluded"


def test_merge_enriches_only_exact_symbol_matches_and_surfaces_class_conflicts():
    sec = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)
    nasdaq = parse_symbol_directory(NASDAQ_FIXTURE, retrieved_at=NOW)
    other = parse_symbol_directory(OTHER_FIXTURE, retrieved_at=NOW)

    merged = merge_reference_sources(
        sec.securities, (*nasdaq.securities, *other.securities), as_of=NOW.date()
    )

    assert merged.by_ticker["MP"].exchange == "NASDAQ"
    assert merged.by_ticker["MP"].source_ids == (
        "nasdaq-listed:MP",
        "sec-company-tickers:0001801368",
    )
    dupe = merged.by_ticker["DUPE"]
    assert dupe.eligible is False
    assert dupe.exclusion_reasons == ("ambiguous_listing",)
    assert any(conflict.code == "conflicting_listing_class" for conflict in merged.conflicts)
    # No ticker match means no name-based parent inference or source merge.
    assert "OTCM" not in sec.by_ticker
    assert merged.by_ticker["OTCM"].entity_id == "symbol-directory:OTCM"


def test_merge_preserves_identity_and_former_ticker_when_one_issuer_listing_renames():
    current = replace(
        security("COMMON_STOCK", ticker="NEW"),
        security_id="sec-cik:0000000001:listing-origin:NEW",
        aliases=("NEW",),
        valid_from=date(2026, 9, 4),
    )
    prior = replace(
        security("COMMON_STOCK", ticker="OLD"),
        security_id="sec-cik:0000000001:listing-origin:OLD",
        aliases=("OLD",),
        source_ids=("prior-manifest:fixture",),
        valid_from=date(2020, 1, 2),
    )

    merged = merge_reference_sources((current,), (prior,), as_of=date(2026, 9, 4))

    renamed = merged.by_ticker["NEW"]
    assert renamed.security_id == prior.security_id
    assert renamed.aliases == ("NEW", "OLD")
    assert renamed.valid_from == prior.valid_from


def test_merge_closes_a_missing_prior_listing_at_the_new_snapshot_date():
    prior = replace(
        security("COMMON_STOCK", ticker="GONE"),
        security_id="11111111-1111-4111-8111-111111111112",
        aliases=("GONE",),
        source_ids=("prior-manifest:fixture",),
        valid_from=date(2020, 1, 2),
    )

    merged = merge_reference_sources((), (prior,), as_of=date(2026, 9, 4))

    closed = merged.by_ticker["GONE"]
    assert closed.security_id == prior.security_id
    assert closed.valid_from == date(2020, 1, 2)
    assert closed.valid_to == date(2026, 9, 4)
    assert closed.eligible is False
    assert closed.exclusion_reasons == ("delisted",)


class _Response:
    def __init__(self, body: bytes) -> None:
        self.status = 200
        self.headers = {
            "Content-Type": "application/json",
            "Date": "Fri, 04 Sep 2026 18:05:00 GMT",
        }
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def geturl(self) -> str:
        return SEC_COMPANY_TICKERS_URL

    def close(self) -> None:
        pass


class _Opener:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return _Response(self.body)


class _FailingOpener:
    def open(self, _request, _timeout=None, **_kwargs):
        raise URLError("fixture outage")


def test_refresh_uses_bounded_http_and_a_descriptive_sec_user_agent():
    opener = _Opener(SEC_FIXTURE.read_bytes())
    client = BoundedHttpClient(
        allowed_hosts={"www.sec.gov"}, opener=opener, clock=lambda: NOW
    )

    result = refresh_sec_reference(client)

    assert result.status == "healthy"
    assert result.snapshot is not None
    assert result.snapshot.manifest.source_timestamp == NOW
    assert len(opener.requests) == 1
    request, timeout = opener.requests[0]
    assert request.full_url == SEC_COMPANY_TICKERS_URL
    assert request.get_header("User-agent") == SEC_REFERENCE_USER_AGENT
    assert "stocks-agent" in SEC_REFERENCE_USER_AGENT
    assert "@" in SEC_REFERENCE_USER_AGENT
    assert timeout <= 10


def test_failed_refresh_reuses_last_healthy_snapshot_and_reports_reference_stale():
    previous = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)
    client = BoundedHttpClient(
        allowed_hosts={"www.sec.gov"}, opener=_FailingOpener(), clock=lambda: NOW
    )

    stale = refresh_sec_reference(client, previous=previous)
    unavailable = refresh_sec_reference(client)

    assert stale.snapshot == previous
    assert stale.status == "reference_stale"
    assert stale.error_code == "SOURCE_UNAVAILABLE"
    assert unavailable.snapshot is None
    assert unavailable.status == "reference_unavailable"


def test_reference_transfer_matches_protected_ledger_and_stays_inside_every_call_bound():
    previous = parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW)
    changed_mp = replace(
        previous.by_ticker["MP"], aliases=("MP", "MPCO"), valid_from=date(2026, 9, 5)
    )
    current = replace(
        previous,
        securities=tuple(
            changed_mp if row.ticker == "MP" else row for row in previous.securities
        ),
    )

    transfer = build_reference_transfer(
        current,
        run_id=RUN_ID,
        capability_version=1,
        taxonomy_version=1,
        predecessor_manifest_id="22222222-2222-4222-8222-222222222222",
    )

    assert set(transfer.begin) == {
        "manifest",
        "capability_id",
        "chunk_count",
        "security_count",
        "root_hash",
        "predecessor_manifest_id",
    }
    assert set(transfer.begin["manifest"]) == {
        "id",
        "reference_version",
        "revision",
        "capability_version",
        "taxonomy_version",
        "source_hash",
        "valid_from",
        "valid_to",
        "manifest",
        "content_hash",
    }
    assert transfer.begin["manifest"]["manifest"]["coverage_status"] == "scope_not_guaranteed"
    assert transfer.begin["manifest"]["manifest"]["reference_status"] == "healthy"
    assert transfer.begin["manifest"]["manifest"]["symbol_directory_status"] == (
        "disabled_pending_https_and_terms_review"
    )
    assert transfer.begin["security_count"] == len(current.securities)
    assert transfer.begin["chunk_count"] == 1
    assert len(transfer.chunks[0]["entries"]) == len(current.securities)
    revision = next(
        row for row in transfer.chunks[0]["entries"] if row["ticker"] == "MP"
    )
    assert revision["aliases"] == ["MP", "MPCO"]
    assert "execution_allowed" not in json.dumps(transfer.begin)
    assert len(json.dumps(transfer.begin, separators=(",", ":")).encode()) <= 192 * 1024
    assert all(
        len(json.dumps(chunk, separators=(",", ":")).encode()) <= 192 * 1024
        and len(chunk["entries"]) <= 200
        for chunk in transfer.chunks
    )
    assert transfer.begin["root_hash"] == hashlib.sha256(
        "".join(chunk["chunk_hash"] for chunk in transfer.chunks).encode()
    ).hexdigest()
    manifest_content = dict(transfer.begin["manifest"])
    manifest_hash = manifest_content.pop("content_hash")
    expected = hashlib.sha256(
        json.dumps(
            manifest_content, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    assert manifest_hash == expected


def test_reference_transfer_chunks_the_full_supported_snapshot_without_raising_limits():
    base = security("COMMON_STOCK")
    securities = tuple(
        replace(
            base,
            security_id=f"sec-cik:{index:010d}:listing-origin:T{index:05d}",
            entity_id=f"sec-cik:{index:010d}",
            ticker=f"T{index:05d}",
            aliases=(f"T{index:05d}",),
            source_ids=(f"sec-company-tickers:{index:010d}",),
        )
        for index in range(1, 15_001)
    )
    snapshot = replace(
        parse_sec_company_tickers(SEC_FIXTURE, retrieved_at=NOW),
        securities=securities,
    )

    transfer = build_reference_transfer(
        snapshot,
        run_id=RUN_ID,
        capability_version=1,
        taxonomy_version=1,
    )

    assert len(transfer.chunks) == 75
    assert sum(len(chunk["entries"]) for chunk in transfer.chunks) == 15_000
    assert len(transfer.chunks) + 3 <= 80  # begin, finalize, and pin
    encoded_total = 0
    for index, chunk in enumerate(transfer.chunks):
        assert chunk["chunk_index"] == index
        envelope = {
            "schema_version": 1,
            "operation": "record_discovery_reference_chunk",
            "request_id": f"00000000-0000-4000-8000-{index:012d}",
            "run_id": RUN_ID,
            "dry_run": False,
            "payload": chunk,
        }
        encoded = json.dumps(envelope, separators=(",", ":")).encode()
        encoded_total += len(encoded)
        assert len(encoded) <= 262_144
    assert encoded_total <= 16 * 1024 * 1024


def test_capability_registry_keeps_unreviewed_symbol_directory_ingestion_disabled():
    config = json.loads(
        (Path(__file__).parents[1] / "config" / "intelligence_sources.json").read_text()
    )
    sec = next(
        row
        for row in config["capabilities"]
        if row["capability_id"] == "sec_company_tickers_universe"
    )

    assert sec["query_pack"]["default"]["coverage_status"] == "scope_not_guaranteed"
    assert sec["query_pack"]["default"]["symbol_directory_ingestion"] == (
        "disabled_pending_https_and_terms_review"
    )
