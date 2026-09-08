from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

from lib.intelligence.universe import (
    FormerIssuerName,
    IssuerIdentity,
    ReferenceManifest,
    ReferenceSnapshot,
    SecurityIdentity,
    build_reference_transfer,
    parse_sec_company_tickers,
    reference_snapshot_from_rows,
    security_revision_semantic_document,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql" / "migrations" / "20261009_reference_issuer_names.sql"
NOW = datetime(2026, 9, 6, 15, tzinfo=timezone.utc)
RUN_ID = "11111111-1111-4111-8111-111111111111"


def _snapshot() -> ReferenceSnapshot:
    issuer = IssuerIdentity(
        entity_id="sec-cik:0000000001",
        cik="0000000001",
        canonical_name="Current Company",
        former_names=("Old Company",),
        valid_from=date(2026, 1, 1),
        valid_to=None,
        source_ids=("sec-company-tickers:0000000001",),
        observed_names=("Current Company", "Current Company Class A"),
        dated_former_names=(FormerIssuerName(
            "Old Company", date(2020, 1, 1), date(2025, 12, 31)
        ),),
    )
    securities = tuple(
        SecurityIdentity(
            security_id=f"sec:CLASS-{ticker}",
            entity_id=issuer.entity_id,
            ticker=ticker,
            exchange="NASDAQ",
            instrument_type="COMMON_STOCK",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            aliases=(ticker,),
            source_ids=("sec-company-tickers:0000000001",),
            eligible=True,
            exclusion_reasons=(),
        )
        for ticker in ("AAA", "AAB")
    )
    manifest = ReferenceManifest(
        "fixture:v2", "a" * 64, "https://www.sec.gov/files/company_tickers.json",
        NOW, NOW, 1, "scope_not_guaranteed", "healthy", len(securities),
    )
    return ReferenceSnapshot(manifest, (issuer,), securities)


def test_same_snapshot_sec_titles_are_observed_names_not_former_names():
    source = json.dumps({
        "0": {"cik_str": 1, "ticker": "AAA", "title": "Current Company Class A"},
        "1": {"cik_str": 1, "ticker": "AAB", "title": "Current Company Class B"},
    }).encode()

    issuer = parse_sec_company_tickers(source, retrieved_at=NOW).issuers[0]

    assert issuer.canonical_name == "Current Company Class A"
    assert issuer.observed_names == (
        "Current Company Class A", "Current Company Class B",
    )
    assert issuer.former_names == ()
    assert issuer.dated_former_names == ()


def test_v1_security_hash_document_is_unchanged_and_v2_binds_issuer_names():
    legacy = {
        "revision": 1,
        "security_id": "sec:AAA",
        "entity_id": "sec-cik:0000000001",
        "ticker": "AAA",
        "exchange": "NASDAQ",
        "instrument_type": "COMMON_STOCK",
        "eligible": True,
        "exclusion_reasons": [],
        "aliases": ["AAA"],
        "source_ids": ["fixture"],
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": None,
    }
    v2 = {
        **legacy,
        "semantic_encoding_version": 2,
        "issuer_names": {
            "canonical_name": "Current Company",
            "observed_names": ["Current Company", "Current Company Class A"],
            "former_names": [{
                "name": "Old Company",
                "valid_from": "2020-01-01",
                "valid_to": "2025-12-31",
            }],
        },
    }

    legacy_document = security_revision_semantic_document(legacy)
    v2_document = security_revision_semantic_document(v2)

    assert legacy_document["semantic_encoding_version"] == 1
    assert set(legacy_document["value"]) == set(legacy)
    assert v2_document["semantic_encoding_version"] == 2
    assert v2_document["value"]["issuer_names"] == v2["issuer_names"]
    assert hashlib.sha256(json.dumps(
        legacy_document, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest() != hashlib.sha256(json.dumps(
        v2_document, separators=(",", ":"), sort_keys=True
    ).encode()).hexdigest()


def test_v2_hash_vector_matches_typescript_and_sql_protocol():
    row = {
        "revision": 1,
        "security_id": "sec-cik:0000000001:listing-origin:TEST",
        "entity_id": "sec-cik:0000000001",
        "ticker": "TEST",
        "exchange": None,
        "instrument_type": "COMMON_STOCK",
        "eligible": True,
        "exclusion_reasons": [],
        "aliases": ["TEST"],
        "source_ids": ["sec-company-tickers:0000000001"],
        "valid_from": "2026-09-06T00:00:00.000Z",
        "valid_to": None,
        "semantic_encoding_version": 2,
        "issuer_names": {
            "canonical_name": "Test Company",
            "observed_names": ["Test Company", "Test Company Class A"],
            "former_names": [{
                "name": "Old Test Company",
                "valid_from": "2020-01-01",
                "valid_to": "2025-12-31",
            }],
        },
    }
    digest = hashlib.sha256(json.dumps(
        security_revision_semantic_document(row),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()

    assert digest == "6ed1594329bb40358f01357932fbae01bf5d241336d6f2989634a6f2ba630759"


def test_v2_transfer_embeds_identical_issuer_names_and_caps_chunks_at_88_rows():
    snapshot = _snapshot()
    many = tuple(
        replace(
            snapshot.securities[index % 2],
            security_id=f"sec:{index:05d}",
            ticker=f"T{index:05d}",
            aliases=(f"T{index:05d}",),
        )
        for index in range(177)
    )
    transfer = build_reference_transfer(
        replace(snapshot, securities=many),
        run_id=RUN_ID,
        capability_version=1,
        taxonomy_version=1,
        semantic_encoding_version=2,
    )

    entries = [entry for chunk in transfer.chunks for entry in chunk["entries"]]
    assert transfer.begin["manifest"]["manifest"]["format_version"] == 2
    assert all(entry["semantic_encoding_version"] == 2 for entry in entries)
    assert {json.dumps(entry["issuer_names"], sort_keys=True) for entry in entries} == {
        json.dumps(entries[0]["issuer_names"], sort_keys=True)
    }
    assert all(len(chunk["entries"]) <= 88 for chunk in transfer.chunks)
    assert sum(len(chunk["entries"]) for chunk in transfer.chunks) == 177


def test_v2_read_rows_hydrate_complete_snapshot_and_legacy_rows_are_explicitly_limited():
    transfer = build_reference_transfer(
        _snapshot(),
        run_id=RUN_ID,
        capability_version=1,
        taxonomy_version=1,
        semantic_encoding_version=2,
    )
    manifest = transfer.begin["manifest"]
    rows = [entry for chunk in transfer.chunks for entry in chunk["entries"]]

    hydrated = reference_snapshot_from_rows(manifest, rows)

    assert hydrated.manifest.reference_version == manifest["reference_version"]
    assert hydrated.issuers == _snapshot().issuers
    assert {row.security_id for row in hydrated.securities} == {"sec:CLASS-AAA", "sec:CLASS-AAB"}
    legacy_row = {
        key: value for key, value in rows[0].items()
        if key not in {"semantic_encoding_version", "issuer_names"}
    }
    legacy_row["content_hash"] = hashlib.sha256(json.dumps(
        security_revision_semantic_document(legacy_row),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()).hexdigest()
    legacy = [legacy_row]
    legacy_manifest = {
        **manifest,
        "manifest": {**manifest["manifest"], "format_version": 1},
    }
    limited = reference_snapshot_from_rows(legacy_manifest, legacy)
    assert limited.issuers == ()
    assert limited.limitations == ("issuer_names_unavailable",)


def test_additive_migration_preserves_prior_files_and_redefines_existing_protocol():
    sql = MIGRATION.read_text()

    assert "ADD COLUMN IF NOT EXISTS semantic_encoding_version" in sql
    assert "ADD COLUMN IF NOT EXISTS issuer_names" in sql
    assert "format_version" in sql
    assert "issuer_names_unavailable" in sql
    assert "identical issuer names" in sql
    assert "CREATE OR REPLACE FUNCTION public.begin_market_discovery_reference" in sql
    assert "CREATE OR REPLACE FUNCTION public.record_market_discovery_reference_chunk" in sql
    assert "CREATE OR REPLACE FUNCTION public.finalize_market_discovery_reference" in sql
    assert "CREATE OR REPLACE FUNCTION public.read_market_discovery_reference" in sql
    changed_prior = subprocess.run(
        [
            "git", "diff", "--name-only",
            "899b69d010caa95e839d6cfea668fec915ee2857", "--",
            "sql/migrations/20261004_*.sql",
            "sql/migrations/20261005_*.sql",
            "sql/migrations/20261006_*.sql",
            "sql/migrations/20261007_*.sql",
            "sql/migrations/20261008_*.sql",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert changed_prior.stdout == ""
