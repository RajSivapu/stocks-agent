from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from lib.intelligence.exposure import (
    ExposureFact,
    FilingEvidence,
    IssuerExposureBinding,
    exposure_fact_from_persistence,
    evaluate_exposure,
    extract_exposure_facts,
)
from lib.intelligence.http import SourceFailure
from lib.intelligence.providers.sec import (
    extract_filing_passage,
    validate_sec_submissions,
)


VECTORS = json.loads((Path(__file__).parent / "fixtures/exposure_fact_hash_vectors.json").read_text())
NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "intelligence" / "sec_filing_exposure.html"


def issuer(**changes):
    values = dict(
        entity_id="sec-cik:0001801368",
        security_id="NASDAQ:MP",
        security_revision_id="11111111-1111-4111-8111-111111111111",
        reference_manifest_id="22222222-2222-4222-8222-222222222222",
        cik="0001801368",
        canonical_name="MP Materials Corp.",
        ticker="MP",
        reference_status="current",
    )
    values.update(changes)
    return IssuerExposureBinding(**values)


def submissions(**recent_changes):
    recent = {
        "accessionNumber": ["0001193125-26-200001"],
        "filingDate": ["2026-08-08"],
        "reportDate": ["2026-06-30"],
        "acceptanceDateTime": ["20260808163000"],
        "form": ["10-Q"],
        "primaryDocument": ["mp-20260630.htm"],
    }
    recent.update(recent_changes)
    return {
        "cik": "0001801368",
        "name": "MP Materials Corp.",
        "filings": {"recent": recent, "files": []},
    }


def filing(passage: str, **changes):
    values = dict(
        issuer_cik="0001801368",
        accession_number="0001193125-26-200001",
        form="10-Q",
        primary_document="mp-20260630.htm",
        source_url="https://www.sec.gov/Archives/edgar/data/1801368/000119312526200001/mp-20260630.htm",
        source_response_hash="a" * 64,
        submissions_response_hash="e" * 64,
        passage=passage,
        source_locator="item-2:magnetics-segment",
        normalized_passage_hash=hashlib.sha256(passage.encode()).hexdigest(),
        parser_version="sec-visible-passage-v1",
        filing_rule_version="sec-submissions-binding-v1",
        schema_version=1,
        filing_date=date(2026, 8, 8),
        accepted_at=datetime(2026, 8, 8, 16, 30, tzinfo=timezone.utc),
        reporting_period_end=date(2026, 6, 30),
        retrieved_at=NOW,
        source_item_id="33333333-3333-4333-8333-333333333333",
        source_item_content_hash="b" * 64,
        source_receipt_id="44444444-4444-4444-8444-444444444444",
        source_cache_key="c" * 64,
    )
    values.update(changes)
    return FilingEvidence(**values)


def test_filing_agent_accession_is_bound_to_response_issuer_and_primary_document():
    rows = validate_sec_submissions(
        submissions(), issuer_cik="0001801368", retrieved_at=NOW,
        response_url="https://data.sec.gov/submissions/CIK0001801368.json",
    )
    assert rows[0].accession_number == "0001193125-26-200001"
    assert rows[0].issuer_cik == "0001801368"
    assert rows[0].archive_url == (
        "https://www.sec.gov/Archives/edgar/data/1801368/"
        "000119312526200001/mp-20260630.htm"
    )


@pytest.mark.parametrize("payload", [
    submissions(primaryDocument=["../escape.htm"]),
    submissions(primaryDocument=["bad%2fdoc.htm"]),
    submissions(accessionNumber=["0001193125-26-200001", "0001193125-26-200001"]),
    submissions(form=["10-Q", "10-K"]),
])
def test_submissions_reject_malformed_columns_conflicts_and_unsafe_filenames(payload):
    with pytest.raises(SourceFailure):
        validate_sec_submissions(
            payload, issuer_cik="0001801368", retrieved_at=NOW,
            response_url="https://data.sec.gov/submissions/CIK0001801368.json",
        )


def test_submissions_reject_arbitrary_url_even_when_accession_prefix_matches():
    with pytest.raises(SourceFailure, match="UNSAFE_URL"):
        validate_sec_submissions(
            submissions(accessionNumber=["0001801368-26-000001"]),
            issuer_cik="0001801368", retrieved_at=NOW,
            response_url="https://data.sec.gov:444/submissions/CIK0001801368.json?x=1",
        )


def test_safe_parser_finds_passage_after_first_2000_characters_and_excludes_ix_hidden():
    raw = FIXTURE.read_bytes().replace(
        b"<h2 id=\"item-2\">", b"<p>" + b"preamble " * 400 + b"</p><h2 id=\"item-2\">",
    )
    passage = extract_filing_passage(
        raw,
        source_url="https://www.sec.gov/Archives/edgar/data/1801368/000119312526200001/mp-20260630.htm",
        content_type="text/html; charset=utf-8",
        terms=("manufacture", "permanent magnets", "magnetics segment"),
    )
    assert "We manufacture sintered permanent magnets" in passage.passage
    assert "$12.4 million" in passage.passage
    assert "footnote 4" in passage.passage
    assert "Hidden XBRL" not in passage.passage
    assert len(passage.passage) <= 2_000
    assert len(passage.locator) <= 256


@pytest.mark.parametrize("content_type,raw", [
    ("application/pdf", b"%PDF-1.7"),
    ("text/plain", b"<SEC-DOCUMENT>legacy submission</SEC-DOCUMENT>"),
    ("application/xml", b"<xbrl><context>metadata</context></xbrl>"),
    ("text/html", b"<!ENTITY xxe SYSTEM 'file:///etc/passwd'><p>&xxe;</p>"),
])
def test_filing_parser_rejects_non_direct_or_entity_bearing_documents(content_type, raw):
    with pytest.raises(SourceFailure):
        extract_filing_passage(
            raw,
            source_url="https://www.sec.gov/Archives/edgar/data/1801368/000119312526200001/mp.htm",
            content_type=content_type,
            terms=("magnet",),
        )


def test_candidate_taxonomy_term_without_operational_attribution_is_insufficient():
    evidence = filing("Permanent magnets are a candidate taxonomy term for future research.")
    facts = extract_exposure_facts(evidence, issuer=issuer(), role="magnet_manufacturing")
    assert facts[0].status == "insufficient"
    assert facts[0].claim_state == "insufficient"
    assert evaluate_exposure(facts).business_exposure == "unresolved"


@pytest.mark.parametrize(("passage", "claim_state", "status"), [
    ("We plan to manufacture permanent magnets in a future facility.", "planned", "insufficient"),
    ("We forecast that permanent magnet demand will double.", "forecast", "insufficient"),
    ("Our customer manufactures permanent magnets for motors.", "customer", "insufficient"),
    ("We do not manufacture permanent magnets.", "contradicted", "contradicted"),
    ("We manufacture permanent magnets at our Texas facility.", "operational", "supported"),
])
def test_exposure_preserves_planned_forecast_customer_negation_and_operations(
    passage, claim_state, status,
):
    fact = extract_exposure_facts(
        filing(passage), issuer=issuer(), role="magnet_manufacturing",
    )[0]
    assert fact.claim_state == claim_state
    assert fact.status == status
    assert fact.financial_materiality == "unknown"
    assert fact.value is None


def test_supported_fact_binds_all_semantics_and_tampering_changes_hash():
    passage = "We manufacture permanent magnets at our Texas facility."
    fact = extract_exposure_facts(
        filing(passage), issuer=issuer(), role="magnet_manufacturing",
    )[0]
    assert fact.status == "supported"
    assert fact.execution_allowed is False
    assert fact.security_revision_id == issuer().security_revision_id
    assert fact.accession_number == "0001193125-26-200001"
    assert fact.primary_document == "mp-20260630.htm"
    assert fact.submissions_response_hash == "e" * 64
    assert fact.source_response_hash == "a" * 64
    assert fact.normalized_passage_hash == hashlib.sha256(passage.encode()).hexdigest()
    assert replace(fact, filing_date=date(2026, 8, 9)).semantic_hash != fact.semantic_hash
    assert replace(fact, submissions_response_hash="f" * 64).semantic_hash != fact.semantic_hash


def test_amendment_is_distinct_and_never_automatically_supersedes_original():
    base = extract_exposure_facts(
        filing("We manufacture permanent magnets at our Texas facility."),
        issuer=issuer(), role="magnet_manufacturing",
    )[0]
    amended = extract_exposure_facts(
        filing(
            "We manufacture permanent magnets at our Texas facility.",
            form="10-Q/A", accession_number="0001193125-26-200099",
            source_url="https://www.sec.gov/Archives/edgar/data/1801368/000119312526200099/mp-20260630a.htm",
            primary_document="mp-20260630a.htm", source_response_hash="b" * 64,
        ),
        issuer=issuer(), role="magnet_manufacturing",
    )[0]
    assert amended.is_amendment is True
    assert amended.fact_id != base.fact_id
    assert amended.status == base.status == "supported"
    assert "automatic_supersession_forbidden" in amended.limitations


def test_comparable_quantitative_conflict_never_counts_as_supported_exposure():
    first = extract_exposure_facts(
        filing("We manufacture permanent magnets, which generated 12% of our revenue."),
        issuer=issuer(), role="magnet_manufacturing",
    )[0]
    second = extract_exposure_facts(
        filing(
            "We manufacture permanent magnets, which generated 15% of our revenue.",
            accession_number="0001193125-26-200099",
            primary_document="mp-20260630a.htm",
            source_url=(
                "https://www.sec.gov/Archives/edgar/data/1801368/"
                "000119312526200099/mp-20260630a.htm"
            ),
            source_item_id="55555555-5555-4555-8555-555555555555",
            source_response_hash="d" * 64,
        ),
        issuer=issuer(), role="magnet_manufacturing",
    )[0]

    evaluation = evaluate_exposure((first, second))

    assert evaluation.business_exposure == "unresolved"
    assert evaluation.financial_materiality == "unknown"
    assert evaluation.supported_fact_ids == ()
    assert "unresolved_comparable_claim_conflict" in evaluation.limitations


def test_supported_percent_of_revenue_shared_hash_vector_round_trips():
    row = VECTORS["supported_percent_of_revenue"]

    fact = exposure_fact_from_persistence(row)

    assert isinstance(fact, ExposureFact)
    assert fact.semantic_hash == row["content_hash"]
    assert fact.fact_id == row["id"]
    assert fact.financial_materiality == "supported"


@pytest.mark.parametrize(("passage", "reporting_period_end", "expected_value"), [
    ("We manufacture magnets, which generated 101% of our revenue.", date(2026, 6, 30), None),
    ("We manufacture magnets, which generated 12.5% of our revenue.", None, None),
    ("We manufacture magnets, which generated 012.500% of our revenue.", date(2026, 6, 30), "12.5"),
])
def test_revenue_materiality_requires_bounded_value_and_bound_reporting_period(
    passage, reporting_period_end, expected_value,
):
    fact = extract_exposure_facts(
        filing(passage, reporting_period_end=reporting_period_end),
        issuer=issuer(), role="magnet_manufacturing",
    )[0]
    assert fact.value == expected_value
    assert fact.financial_materiality == (
        "supported" if expected_value is not None else "unknown"
    )


@pytest.mark.parametrize("mutation", VECTORS["invalid_supported_mutations"])
def test_supported_financial_materiality_rejects_forged_semantics(mutation):
    row = json.loads(json.dumps(VECTORS["supported_percent_of_revenue"]))
    row["fact"]["value"][mutation["field"]] = mutation["value"]
    row["content_hash"] = hashlib.sha256(json.dumps(
        row["fact"], allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    row["id"] = str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"market-exposure:{row['content_hash']}",
    ))

    with pytest.raises(ValueError, match="invalid"):
        exposure_fact_from_persistence(row)
