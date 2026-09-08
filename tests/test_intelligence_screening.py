from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from lib.intelligence.providers.sec import parse_form4_ownership_xml
from lib.intelligence.providers.yahoo_screen import YahooScreenAdapter
from lib.intelligence.screening import (
    GatewayMarketInputs,
    MarketFilterPolicy,
    ScreenDefinition,
    ScreenObservation,
    ScreenPayload,
    apply_market_filters,
    cluster_form4_purchases,
    load_screen_definitions,
    parse_market_scan_policy,
    run_bounded_screens,
)
from lib.intelligence.universe import (
    IssuerIdentity,
    ReferenceManifest,
    ReferenceSnapshot,
    SecurityIdentity,
)


NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "intelligence" / "yahoo_screen.json"


def _security(
    ticker: str,
    cik: str,
    *,
    sector: str | None = None,
) -> SecurityIdentity:
    del sector
    return SecurityIdentity(
        security_id=f"sec:{ticker}",
        entity_id=f"sec-cik:{cik}",
        ticker=ticker,
        exchange="NASDAQ",
        instrument_type="COMMON_STOCK",
        valid_from=date(2020, 1, 1),
        valid_to=None,
        aliases=(ticker,),
        source_ids=(f"sec-company-tickers:{cik}",),
        eligible=True,
        exclusion_reasons=(),
        revision_id=f"revision:{ticker}",
        reference_manifest_id="manifest-current",
    )


def _reference(*securities: SecurityIdentity) -> ReferenceSnapshot:
    issuers = tuple(
        IssuerIdentity(
            entity_id=row.entity_id,
            cik=row.cik,
            canonical_name=f"{row.ticker} Incorporated",
            former_names=(),
            valid_from=date(2020, 1, 1),
            valid_to=None,
            source_ids=row.source_ids,
        )
        for row in securities
    )
    return ReferenceSnapshot(
        manifest=ReferenceManifest(
            reference_version="sec:fixture-v2",
            source_hash="a" * 64,
            source_url="https://www.sec.gov/files/company_tickers.json",
            retrieved_at=NOW,
            source_timestamp=NOW,
            parser_version=2,
            coverage_status="scope_not_guaranteed",
            reference_status="healthy",
            security_count=len(securities),
        ),
        issuers=issuers,
        securities=tuple(securities),
    )


def _definition(
    screen_id: str,
    *,
    provider: str = "fixture",
    state: str = "enabled",
    reasons: tuple[str, ...] = (),
) -> ScreenDefinition:
    return ScreenDefinition(
        screen_id=screen_id,
        capability_id=f"{provider}_{screen_id}_screen",
        provider=provider,
        state=state,
        reasons=reasons,
        reviewed_at=date(2026, 9, 7),
        endpoint_version="fixture-v1",
        parser_version="fixture-v1",
        retention_class="bounded_derived_lead",
        max_rows=50,
    )


def _form4(
    *,
    owner_cik: str,
    transaction_date: str,
    document_type: str = "4",
    code: str = "P",
    acquired_disposed: str = "A",
    shares: str = "100",
    footnote: str | None = "Open-market purchase on Nasdaq.",
    extra_transaction: bool = False,
) -> bytes:
    linked = '<footnoteId id="F1"/>' if footnote is not None else ""
    footnotes = f'<footnotes><footnote id="F1">{footnote}</footnote></footnotes>' if footnote is not None else ""
    transaction = f"""
      <nonDerivativeTransaction>
        <transactionDate><value>{transaction_date}</value></transactionDate>
        <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>{shares}</value>{linked}</transactionShares>
          <transactionAcquiredDisposedCode><value>{acquired_disposed}</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
      </nonDerivativeTransaction>
    """
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <ownershipDocument>
      <documentType>{document_type}</documentType>
      <periodOfReport>2026-09-06</periodOfReport>
      <issuer><issuerCik>0000000001</issuerCik><issuerTradingSymbol>AAA</issuerTradingSymbol></issuer>
      <reportingOwner><reportingOwnerId><rptOwnerCik>{owner_cik}</rptOwnerCik><rptOwnerName>Owner</rptOwnerName></reportingOwnerId></reportingOwner>
      <nonDerivativeTable>{transaction}{transaction if extra_transaction else ""}</nonDerivativeTable>
      {footnotes}
    </ownershipDocument>""".encode()


def test_configured_screens_are_explicit_zero_transport_coverage():
    definitions = load_screen_definitions()
    calls = []
    adapter = YahooScreenAdapter(http=lambda *_args, **_kwargs: calls.append("opened"))

    results = tuple(adapter.collect(row, observed_at=NOW) for row in definitions)

    assert [row.screen_id for row in definitions] == [
        "top_gainers",
        "top_losers",
        "most_active",
        "unusual_volume",
        "near_52w_high_quality",
        "oversold_quality",
        "insider_buying_clusters",
    ]
    assert calls == []
    assert all(row.receipt.request_cost == 0 for row in results)
    assert [row.state for row in results[:6]] == ["disabled"] * 6
    assert results[6].state == "unsupported"
    assert set(results[0].reasons) == {
        "automation_permission_unproven",
        "public_api_contract_unavailable",
        "robots_review_unverified",
    }
    assert results[6].reasons == (
        "form4_feed_capability_missing",
        "filing_index_capability_missing",
        "ownership_xml_capability_missing",
        "provider_budget_unreserved",
        "parser_contract_missing",
    )
    assert all(row.coverage["outcome"] != "no_event" for row in results)
    assert all(row.coverage["execution_allowed"] is False for row in results)


def test_malformed_screen_payload_is_distinct_from_parsed_empty():
    definition = _definition("top_gainers")
    malformed = ScreenPayload.from_json(definition, b'{"schema_version":1,"rows":', NOW)
    empty_document = ScreenPayload.from_json(definition, b"", NOW)
    empty = ScreenPayload.from_json(
        definition,
        json.dumps({
            "schema_version": 1,
            "screen_id": "top_gainers",
            "as_of": NOW.isoformat(),
            "rows": [],
            "truncated": False,
        }).encode(),
        NOW,
    )

    assert malformed.state == "malformed"
    assert empty_document.state == "malformed"
    assert empty.state == "parsed_empty"
    assert malformed.state != empty.state


def test_every_inactive_state_is_explicit_and_zero_transport():
    definitions = tuple(
        _definition(f"fixture_{state}", state=state, reasons=(f"{state}_reason",))
        for state in ("unavailable", "disabled", "degraded", "unsupported")
    )

    run = run_bounded_screens(
        definitions,
        payloads={},
        reference=None,
        reference_status="reference_unavailable",
        reference_manifest_id=None,
        as_of=date(2026, 9, 7),
        observed_at=NOW,
    )

    assert [row.state for row in run.results] == [
        "unavailable", "disabled", "degraded", "unsupported",
    ]
    assert all(row.receipt.request_cost == 0 for row in run.results)
    assert all(row.coverage["outcome"] != "no_event" for row in run.results)


def test_screen_values_do_not_replace_gateway_owned_market_inputs():
    policy = MarketFilterPolicy(Decimal("5"), 1_000_000, 2_000_000_000)
    lead = ScreenObservation(
        screen_id="top_gainers",
        symbol="AAA",
        sector="Technology",
        rank=1,
        source_url="https://example.test/screen/aaa",
        observed_at=NOW,
        displayed_price=Decimal("100"),
        displayed_average_volume=9_000_000,
        displayed_market_cap=50_000_000_000,
    )

    missing = apply_market_filters(lead, policy, market_inputs=None)
    below = apply_market_filters(
        lead,
        policy,
        market_inputs=GatewayMarketInputs(
            price=Decimal("3"), average_volume=250_000, market_cap=500_000_000,
        ),
    )

    assert missing.research_visible is True
    assert missing.action_eligible is False
    assert set(missing.reasons) == {
        "gateway_price_missing",
        "gateway_average_volume_missing",
        "gateway_market_cap_missing",
        "primary_exposure_required",
    }
    assert below.research_visible is True
    assert below.action_eligible is False
    assert set(below.reasons) == {
        "below_min_price",
        "below_min_average_volume",
        "below_min_market_cap",
        "primary_exposure_required",
    }


def test_market_scan_policy_reads_exact_action_floors_and_rejects_authority():
    policy = parse_market_scan_policy()

    assert policy == MarketFilterPolicy(
        min_price=Decimal("5"),
        min_average_volume=1_000_000,
        min_market_cap=2_000_000_000,
        max_candidates=10,
    )
    assert policy.execution_allowed is False


def test_screen_leads_require_current_reference_and_are_fair_deduplicated_signals():
    symbols = tuple(f"A{index:02d}" for index in range(12))
    reference = _reference(*(
        _security(symbol, f"{index + 1:010d}") for index, symbol in enumerate(symbols)
    ))
    definitions = (_definition("top_gainers"), _definition("most_active"))
    rows = [
        ScreenObservation(
            screen_id="top_gainers" if index % 2 == 0 else "most_active",
            symbol=symbol,
            sector=("Technology", "Energy", None)[index % 3],
            rank=index + 1,
            source_url=f"https://example.test/screens/{symbol.lower()}",
            observed_at=NOW,
        )
        for index, symbol in enumerate(symbols)
    ]
    rows.append(replace(rows[0], screen_id="most_active", rank=99))
    payloads = {
        screen_id: ScreenPayload(
            screen_id=screen_id,
            state="parsed_nonempty",
            rows=tuple(row for row in rows if row.screen_id == screen_id),
            reasons=(),
        )
        for screen_id in ("top_gainers", "most_active")
    }

    first = run_bounded_screens(
        definitions,
        payloads=payloads,
        reference=reference,
        reference_status="healthy",
        reference_manifest_id="manifest-current",
        as_of=date(2026, 9, 7),
        observed_at=NOW,
    )
    reversed_run = run_bounded_screens(
        definitions,
        payloads={key: replace(value, rows=tuple(reversed(value.rows))) for key, value in payloads.items()},
        reference=replace(reference, securities=tuple(reversed(reference.securities))),
        reference_status="healthy",
        reference_manifest_id="manifest-current",
        as_of=date(2026, 9, 7),
        observed_at=NOW,
    )

    assert len(first.leads) == 10
    assert [row.security_id for row in first.leads] == [
        row.security_id for row in reversed_run.leads
    ]
    assert len({row.security_id for row in first.leads}) == 10
    assert {row.primary_screen_id for row in first.leads} == {"top_gainers", "most_active"}
    assert {row.sector for row in first.leads} >= {"Technology", "Energy", "unknown"}
    assert next(row for row in first.leads if row.symbol == "A00").screen_ids == (
        "top_gainers", "most_active"
    )
    assert all(item.metadata["signal_type"] == "screen_signal" for item in first.source_items)
    assert all(item.authority == "discovery_lead" for item in first.source_items)
    assert all(item.metadata["execution_allowed"] is False for item in first.source_items)


def test_reference_unavailable_or_ambiguous_blocks_security_and_action():
    duplicate_a = _security("AAA", "0000000001")
    duplicate_b = replace(
        _security("AAB", "0000000002"), ticker="AAA", aliases=("AAA",), security_id="sec:AAA:other"
    )
    payload = ScreenPayload(
        screen_id="top_gainers",
        state="parsed_nonempty",
        rows=(ScreenObservation(
            screen_id="top_gainers", symbol="AAA", sector=None, rank=1,
            source_url="https://example.test/screens/aaa", observed_at=NOW,
        ),),
        reasons=(),
    )
    definition = (_definition("top_gainers"),)

    unavailable = run_bounded_screens(
        definition, payloads={"top_gainers": payload}, reference=None,
        reference_status="reference_unavailable", reference_manifest_id=None,
        as_of=date(2026, 9, 7), observed_at=NOW,
    )
    ambiguous = run_bounded_screens(
        definition, payloads={"top_gainers": payload},
        reference=_reference(duplicate_a, duplicate_b), reference_status="healthy",
        reference_manifest_id="manifest-current", as_of=date(2026, 9, 7), observed_at=NOW,
    )

    assert unavailable.leads[0].reference_state == "reference_unavailable"
    assert unavailable.leads[0].security_id is None
    assert ambiguous.leads[0].reference_state == "ambiguous"
    assert ambiguous.leads[0].security_id is None
    assert all(row.action_eligible is False for row in (*unavailable.leads, *ambiguous.leads))


def test_stale_reference_can_support_research_identity_but_never_action():
    security = _security("AAA", "0000000001")
    payload = ScreenPayload(
        screen_id="top_gainers",
        state="parsed_nonempty",
        rows=(ScreenObservation(
            screen_id="top_gainers", symbol="AAA", sector="Technology", rank=1,
            source_url="https://example.test/screens/aaa", observed_at=NOW,
        ),),
        reasons=(),
    )

    run = run_bounded_screens(
        (_definition("top_gainers"),),
        payloads={"top_gainers": payload},
        reference=_reference(security),
        reference_status="reference_stale",
        reference_manifest_id="manifest-current",
        as_of=date(2026, 9, 7),
        observed_at=NOW,
        gateway_market_inputs={
            security.security_id: GatewayMarketInputs(
                price=Decimal("25"), average_volume=2_000_000, market_cap=5_000_000_000,
            ),
        },
        primary_exposure_security_ids=frozenset({security.security_id}),
    )

    assert run.leads[0].security_id == security.security_id
    assert run.leads[0].reference_state == "reference_stale"
    assert run.leads[0].research_visible is True
    assert run.leads[0].action_eligible is False
    assert "reference_stale" in run.leads[0].reasons


def test_form4_parser_requires_transaction_linked_open_market_evidence():
    unknown = parse_form4_ownership_xml(
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", footnote=None),
        source_url="https://www.sec.gov/Archives/edgar/data/1/filing-a/ownership.xml",
        filing_id="filing-a",
        filed_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    qualified = parse_form4_ownership_xml(
        _form4(
            owner_cik="0000000012",
            transaction_date="2026-09-03",
            footnote="Open-market purchase under a Rule 10b5-1 plan.",
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/1/filing-b/ownership.xml",
        filing_id="filing-b",
        filed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert unknown.state == "parsed_nonempty"
    assert unknown.transactions[0].venue_state == "purchase_venue_unknown"
    assert unknown.transactions[0].qualifies_for_cluster is False
    assert qualified.transactions[0].venue_state == "open_market_supported"
    assert qualified.transactions[0].qualifies_for_cluster is True
    assert "rule_10b5_1_transaction" in qualified.transactions[0].limitations
    assert qualified.transactions[0].transaction_date == date(2026, 9, 3)
    assert qualified.transactions[0].reporting_period == date(2026, 9, 6)
    assert qualified.transactions[0].filed_at == datetime(2026, 9, 4, tzinfo=timezone.utc)

    private = parse_form4_ownership_xml(
        _form4(
            owner_cik="0000000013",
            transaction_date="2026-09-03",
            footnote="An open market label appeared in a private placement record.",
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/1/filing-c/ownership.xml",
        filing_id="filing-c",
        filed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )
    assert private.transactions[0].venue_state == "purchase_venue_unknown"
    assert private.transactions[0].qualifies_for_cluster is False


def test_form4_nonpurchases_and_broken_documents_never_become_no_event():
    excluded = [
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", document_type="4/A"),
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", code="A"),
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", code="G"),
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", code="M"),
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", acquired_disposed="D"),
        _form4(owner_cik="0000000011", transaction_date="2026-09-01").replace(
            b"</transactionCoding>",
            b"<equitySwapInvolved><value>1</value></equitySwapInvolved></transactionCoding>",
        ),
    ]
    states = [
        parse_form4_ownership_xml(
            raw,
            source_url=f"https://www.sec.gov/Archives/edgar/data/1/{index}/ownership.xml",
            filing_id=f"filing-{index}",
            filed_at=NOW,
        ).state
        for index, raw in enumerate(excluded)
    ]
    malformed = parse_form4_ownership_xml(
        b"<ownershipDocument>",
        source_url="https://www.sec.gov/Archives/edgar/data/1/bad/ownership.xml",
        filing_id="bad",
        filed_at=NOW,
    )
    partial = parse_form4_ownership_xml(
        _form4(owner_cik="0000000011", transaction_date="2026-09-01", shares=""),
        source_url="https://www.sec.gov/Archives/edgar/data/1/partial/ownership.xml",
        filing_id="partial",
        filed_at=NOW,
    )

    assert states[0] == "unsupported"
    assert states[1:] == ["parsed_empty"] * 5
    assert malformed.state == "malformed"
    assert partial.state == "partial"
    assert "no_event" not in {*states, malformed.state, partial.state}


def test_form4_cluster_requires_two_independent_people_for_same_cik_within_seven_days():
    reference = _reference(_security("AAA", "0000000001"))
    one_owner_many_rows = parse_form4_ownership_xml(
        _form4(
            owner_cik="0000000011", transaction_date="2026-09-01", extra_transaction=True,
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/1/one/ownership.xml",
        filing_id="one",
        filed_at=NOW,
    )
    second_owner = parse_form4_ownership_xml(
        _form4(owner_cik="0000000012", transaction_date="2026-09-07"),
        source_url="https://www.sec.gov/Archives/edgar/data/1/two/ownership.xml",
        filing_id="two",
        filed_at=NOW,
    )

    no_cluster = cluster_form4_purchases(
        (one_owner_many_rows,), reference=reference,
        reference_manifest_id="manifest-current", as_of=date(2026, 9, 7),
    )
    cluster = cluster_form4_purchases(
        (one_owner_many_rows, second_owner), reference=reference,
        reference_manifest_id="manifest-current", as_of=date(2026, 9, 7),
    )

    assert no_cluster == ()
    assert len(cluster) == 1
    assert cluster[0].security_id == "sec:AAA"
    assert cluster[0].reporting_person_ciks == ("0000000011", "0000000012")
    assert cluster[0].window_days == 7
    assert cluster[0].execution_allowed is False


def test_foreign_form4_symbol_never_auto_resolves_by_symbol():
    first = parse_form4_ownership_xml(
        _form4(owner_cik="0000000011", transaction_date="2026-09-01"),
        source_url="https://www.sec.gov/Archives/edgar/data/1/foreign/ownership.xml",
        filing_id="foreign",
        filed_at=NOW,
    )
    second = parse_form4_ownership_xml(
        _form4(owner_cik="0000000012", transaction_date="2026-09-02"),
        source_url="https://www.sec.gov/Archives/edgar/data/1/foreign-2/ownership.xml",
        filing_id="foreign-2",
        filed_at=NOW,
    )
    different_cik_reference = _reference(_security("AAA", "0000000999"))

    assert cluster_form4_purchases(
        (first, second),
        reference=different_cik_reference,
        reference_manifest_id="manifest-current",
        as_of=date(2026, 9, 7),
    ) == ()


def test_fixture_contract_is_bounded_and_parsed_offline():
    definition = _definition("top_gainers")
    payload = ScreenPayload.from_json(definition, FIXTURE.read_bytes(), NOW)

    assert payload.state == "parsed_nonempty"
    assert [row.symbol for row in payload.rows] == ["AAA", "BBB"]
    assert payload.rows[0].sector == "Technology"
