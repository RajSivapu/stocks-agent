import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
from datetime import date, datetime, timezone
from types import MappingProxyType

import pytest
from pglast import parse_sql
from pglast.stream import RawStream

from lib.intelligence.exposure import FilingEvidence, IssuerExposureBinding, extract_exposure_facts
from lib.intelligence.research_queue import (
    EnrichmentRequest,
    adaptive_provider_reservations,
    build_selection_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "sql/migrations/20261010_bounded_adaptive_enrichment.sql"
SCHEMA = ROOT / "sql/schema.sql"
PROTECTED = {
    "sql/reconciliation/20261004_production_schema_reconciliation.sql": "db8486083b6c36a7d574a6135e432f01fa0d1602a3ca560b57743949c6fedc87",
    "sql/migrations/20261005_market_wide_discovery.sql": "708df0bf998e025158294c1902d147cead6cc1e08dd3e246aa9dc8f5465dafea",
    "sql/migrations/20261006_reference_snapshot_transfer.sql": "97548a0dbd92a19b7a8e8cac60fe5024a93ee93eaa5257c71932274e3cd25f33",
    "sql/migrations/20261007_discovery_cursor_context.sql": "789896fe6eef69de41eca22339c98e1f46f6f717ca086878c79910572f89a8a6",
    "sql/migrations/20261008_official_source_completion_contract.sql": "4e63c3aea0ba21b1e646f550fb712a91cd4dd98a0c4618a3657cb7171263d8f8",
    "sql/migrations/20261009_reference_issuer_names.sql": "a6f20e7b50f6ecf091e0d5ef73c2772587caebc30135e3add8adefecab2d6094",
}


def statements():
    return [RawStream()(item) for item in parse_sql(MIGRATION.read_text())]


def test_enrichment_migration_is_additive_and_protected_migrations_are_immutable():
    assert MIGRATION.is_file()
    for name, digest in PROTECTED.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    normalized = "\n".join(statements())
    for table in ("market_enrichment_selection_manifests", "market_enrichment_request_descriptors"):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in normalized
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in normalized
        assert f"CREATE TRIGGER {table}_append_only" in normalized
    assert "RENAME TO checkpoint_market_discovery_stage_v1_internal" in normalized
    assert "RENAME TO claim_market_intelligence_quote_v1_internal" in normalized
    assert "market_reference_snapshot_memberships" in normalized
    assert "market_reference_run_bindings" in normalized
    assert "market_collection_checkpoints" in normalized
    assert "GRANT EXECUTE" in normalized and "TO service_role" in normalized
    assert "TO anon" not in normalized and "TO authenticated" not in normalized


def test_schema_appends_enrichment_migration_verbatim():
    assert SCHEMA.read_text().endswith(MIGRATION.read_text())


def test_protected_enrichment_accepts_prior_run_pin_and_rejects_replay_or_lineage_tampering():
    binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
    if not all(binaries.values()) or os.geteuid() == 0:
        pytest.skip("disposable PostgreSQL requires local server binaries and a non-root user")

    run_id = "11111111-1111-4111-8111-111111111111"
    prior_run_id = "22222222-2222-4222-8222-222222222222"
    manifest_id = "33333333-3333-4333-8333-333333333333"
    revision_id = "44444444-4444-4444-8444-444444444444"
    unpinned_revision_id = "55555555-5555-4555-8555-555555555555"
    source_receipt_id = "66666666-6666-4666-8666-666666666666"
    source_item_id = "77777777-7777-4777-8777-777777777777"
    task_id = "88888888-8888-4888-8888-888888888888"
    unpinned_task_id = "99999999-9999-4999-8999-999999999999"
    cache_key = "a" * 64
    response_hash = "b" * 64
    item_hash = "c" * 64
    source_url = (
        "https://www.sec.gov/Archives/edgar/data/1/"
        "000119312526200001/alpha-20260630.htm"
    )
    evidence = FilingEvidence(
        issuer_cik="0000000001", accession_number="0001193125-26-200001",
        form="10-Q", primary_document="alpha-20260630.htm", source_url=source_url,
        source_response_hash=response_hash,
        submissions_response_hash="e" * 64,
        passage="We manufacture permanent magnets at our Alpha facility.",
        source_locator="item-2:magnetics",
        normalized_passage_hash=hashlib.sha256(
            b"We manufacture permanent magnets at our Alpha facility."
        ).hexdigest(),
        parser_version="sec-visible-passage-v1",
        filing_rule_version="sec-submissions-binding-v1", schema_version=1,
        filing_date=date(2026, 8, 8),
        accepted_at=datetime(2026, 8, 8, 16, 30, tzinfo=timezone.utc),
        reporting_period_end=date(2026, 6, 30),
        retrieved_at=datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
        source_item_id=source_item_id, source_item_content_hash=item_hash,
        source_receipt_id=source_receipt_id, source_cache_key=cache_key,
    )
    binding = IssuerExposureBinding(
        entity_id="sec-cik:0000000001", security_id="NASDAQ:AAA",
        security_revision_id=revision_id, reference_manifest_id=manifest_id,
        cik="0000000001", canonical_name="Alpha Incorporated", ticker="AAA",
    )
    fact_row = extract_exposure_facts(
        evidence, issuer=binding, role="magnet_manufacturing",
        event_ids=("event-1",), hypothesis_ids=("hypothesis-1",),
    )[0].to_persistence_row()
    selected = EnrichmentRequest(
        request_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        entity_id=binding.entity_id, security_id=binding.security_id,
        security_revision_id=revision_id, reference_manifest_id=manifest_id,
        cik=binding.cik, ticker=binding.ticker, instrument_type="COMMON_STOCK",
        event_ids=("event-1",), theme_id="critical_minerals_magnets",
        role="magnet_manufacturing", hypothesis_ids=("hypothesis-1",),
        source_item_ids=(source_item_id,), dependency_task_ids=(), provider="sec_edgar",
        capability_id="sec_issuer_submissions", query_kind="issuer_submissions",
        descriptor=MappingProxyType({
            "cik": binding.cik, "issuer_entity_id": binding.entity_id,
            "reference_manifest_id": manifest_id, "security_revision_id": revision_id,
        }), adverse_path=False, priority=1,
    )
    selection = build_selection_manifest(
        run_id=run_id, phase="on-demand", requests=(selected,), deferred_reasons={},
        provider_reservations=adaptive_provider_reservations("on-demand"),
        request_window={"start": "2026-09-06T10:00:00Z", "end": "2026-09-06T12:00:00Z"},
    ).persistence_payload()
    bound_submission = selection["requests"][0]["descriptor"]
    reservation_id = bound_submission["reservation_id"]
    submission_source_receipt_id = bound_submission["source_receipt_id"]
    submission_cache_key = bound_submission["cache_key"]
    document_request = EnrichmentRequest(
        request_id=task_id, entity_id=binding.entity_id, security_id=binding.security_id,
        security_revision_id=revision_id, reference_manifest_id=manifest_id,
        cik=binding.cik, ticker=binding.ticker, instrument_type="COMMON_STOCK",
        event_ids=("event-1",), theme_id="critical_minerals_magnets",
        role="magnet_manufacturing", hypothesis_ids=("hypothesis-1",),
        source_item_ids=(source_item_id,),
        dependency_task_ids=(selected.request_id,), provider="sec_edgar",
        capability_id="sec_filing_document", query_kind="filing_document",
        descriptor=MappingProxyType({
            "accession_number": evidence.accession_number,
            "accepted_at": "2026-08-08T16:30:00+00:00",
            "filing_date": evidence.filing_date.isoformat(), "form": evidence.form,
            "primary_document": evidence.primary_document,
            "reporting_period_end": evidence.reporting_period_end.isoformat(),
            "submissions_response_hash": evidence.submissions_response_hash,
        }), adverse_path=False, priority=1,
    )
    document_selection = build_selection_manifest(
        run_id=run_id, phase="on-demand", requests=(document_request,), deferred_reasons={},
        provider_reservations=adaptive_provider_reservations("on-demand"),
        request_window={"start": "2026-09-06T10:00:00Z", "end": "2026-09-06T12:00:00Z"},
        selection_stage="filing_documents",
    ).persistence_payload()
    bound_document = document_selection["requests"][0]["descriptor"]
    source_receipt_id = bound_document["source_receipt_id"]
    cache_key = bound_document["cache_key"]
    fact_value = fact_row["fact"]["value"]
    fact_value["source_receipt_id"] = source_receipt_id
    fact_value["source_cache_key"] = cache_key
    fact_row["content_hash"] = hashlib.sha256(json.dumps(
        fact_row["fact"], allow_nan=False, ensure_ascii=False,
        separators=(",", ":"), sort_keys=True,
    ).encode()).hexdigest()
    fact_row["id"] = str(uuid.uuid5(
        uuid.NAMESPACE_URL, f"market-exposure:{fact_row['content_hash']}",
    ))

    def quoted(value):
        return "'" + json.dumps(value, separators=(",", ":"), sort_keys=True).replace("'", "''") + "'::jsonb"

    window = {"start": "2026-09-06T10:00:00Z", "end": "2026-09-06T12:00:00Z"}
    task = {
        "id": task_id, "stage": "enrich", "provider": "sec_edgar",
        "capability_id": "sec_filing_document", "query_kind": "filing_document",
        "query_hash": "d" * 64, "dependency_ids": [], "requested_window": window,
        "state": "succeeded", "attempt_count": 1, "request_budget": 1,
        "result": {"checkpoint": {"cache_key": cache_key}},
    }
    checkpoint = {
        "receipt": {
            "source_receipt_id": source_receipt_id, "response_hash": response_hash,
        },
            "items": [{
                "content_hash": item_hash, "source_url": source_url,
                "request_url": source_url, "authority": "official",
            "normalized_text": fact_value["passage"],
            "canonical_content": json.dumps({"passage": fact_value["passage"]}),
            "metadata": {
                "raw_response_hash": response_hash,
                "normalized_passage_hash": fact_value["normalized_passage_hash"],
                "source_locator": fact_value["source_locator"],
                "parser_version": fact_value["parser_version"],
                "filing_rule_version": fact_value["filing_rule_version"],
            },
        }],
    }
    submission_request_url = "https://data.sec.gov/submissions/CIK0000000001.json"
    submission_checkpoint = {
        "receipt": {
            "provider": "sec_edgar", "status": "succeeded",
            "cache_key": submission_cache_key,
            "source_receipt_id": submission_source_receipt_id,
            "response_hash": evidence.submissions_response_hash,
        },
        "items": [{
            "provider": "sec_edgar", "authority": "official",
            "content_hash": "f" * 64, "source_url": source_url,
            "request_url": submission_request_url,
            "metadata": {
                "issuer_cik": evidence.issuer_cik,
                "accession_number": evidence.accession_number,
                "accepted_at": "2026-08-08T16:30:00+00:00",
                "filing_date": evidence.filing_date.isoformat(),
                "form": evidence.form,
                "primary_document": evidence.primary_document,
                "reporting_period_end": evidence.reporting_period_end.isoformat(),
                "submissions_response_hash": evidence.submissions_response_hash,
            },
        }],
    }
    fact_payload = {
        "task": task, "exposure_facts": [fact_row],
        "theme_episode_revisions": [], "research_nominations": [],
    }

    def run(name, *args, sql=None, check=True):
        result = subprocess.run(
            [binaries[name], *args], input=sql, text=True, capture_output=True, timeout=60,
        )
        if check:
            assert result.returncode == 0, result.stderr
        return result

    with tempfile.TemporaryDirectory(prefix="enrichment-", dir="/tmp") as directory:
        data = str(Path(directory) / "data")
        run("initdb", "-D", data, "-U", "postgres", "--auth=trust", "--no-locale", "--encoding=UTF8")
        run("pg_ctl", "-D", data, "-l", str(Path(directory) / "server.log"),
            "-o", f"-F -k {directory} -c listen_addresses=''", "-w", "start")
        try:
            def execute(sql, *, check=True):
                return run(
                    "psql", "-X", "-h", directory, "-p", "5432", "-U", "postgres",
                    "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-qAt", sql=sql, check=check,
                )

            execute(
                "CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role BYPASSRLS;"
                "CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql AS 'SELECT NULL::uuid';"
                + SCHEMA.read_text()
            )
            execute(
                "INSERT INTO market_policy_config(version,config) VALUES(900,'{}');"
                f"INSERT INTO analysis_runs(id,kind,status) VALUES('{prior_run_id}','on-demand','completed'),('{run_id}','on-demand','running');"
                f"INSERT INTO market_intelligence_runs(id,phase,market_date,policy_version,reservation_plan,request_window) VALUES"
                f"('{prior_run_id}','on-demand','2026-09-05',900,'{{}}',NULL),"
                f"('{run_id}','on-demand','2026-09-06',900,'{{}}',{quoted({**window, 'timezone': 'America/Chicago', 'market_date': '2026-09-06', 'phase': 'on-demand'})});"
                f"INSERT INTO market_reference_manifests(id,run_id,reference_version,revision,capability_version,taxonomy_version,source_hash,valid_from,manifest,content_hash) VALUES"
                f"('{manifest_id}','{prior_run_id}','sec:v2',1,1,1,repeat('e',64),'2026-09-05T00:00:00Z','{{}}',repeat('f',64));"
                f"INSERT INTO market_security_reference_revisions(id,manifest_id,run_id,revision,security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,valid_from,content_hash) VALUES"
                f"('{revision_id}','{manifest_id}','{prior_run_id}',1,'NASDAQ:AAA','sec-cik:0000000001','AAA','NASDAQ','COMMON_STOCK',true,'[]','[\"AAA\"]','[\"source\"]','2026-09-05T00:00:00Z',repeat('1',64)),"
                f"('{unpinned_revision_id}','{manifest_id}','{prior_run_id}',1,'NASDAQ:BBB','sec-cik:0000000002','BBB','NASDAQ','COMMON_STOCK',true,'[]','[\"BBB\"]','[\"source\"]','2026-09-05T00:00:00Z',repeat('2',64));"
                f"INSERT INTO market_reference_finalization_seals(manifest_id,run_id,capability_id,chunk_count,security_count,root_hash) VALUES('{manifest_id}','{prior_run_id}','sec_company_tickers_universe',1,1,repeat('3',64));"
                f"INSERT INTO market_reference_snapshot_memberships(manifest_id,security_revision_id,security_id,ordinal) VALUES('{manifest_id}','{revision_id}','NASDAQ:AAA',0);"
                f"INSERT INTO market_reference_run_bindings(run_id,capability_id,manifest_id,reference_status,reference_as_of,source_retrieved_at,reference_age_seconds,request_payload) VALUES('{run_id}','sec_company_tickers_universe','{manifest_id}','healthy','2026-09-06T12:00:00Z','2026-09-06T12:00:00Z',0,'{{}}');"
                f"INSERT INTO market_source_quota_reservations(id,run_id,provider,market_date,phase,reserved_requests) VALUES('{reservation_id}','{run_id}','sec_edgar','2026-09-06','on-demand',2);"
                f"INSERT INTO market_source_receipts(id,run_id,reservation_id,provider,status,cache_key,requested_window,retrieved_at,expires_at,request_cost,returned_count,accepted_count,duplicate_count,dropped_count,response_hash) VALUES"
                f"('{source_receipt_id}','{run_id}','{reservation_id}','sec_edgar','succeeded','{cache_key}',{quoted(window)},'2026-09-06T12:00:00Z','2026-09-06T12:15:00Z',1,1,1,0,0,'{response_hash}'),"
                f"('{submission_source_receipt_id}','{run_id}','{reservation_id}','sec_edgar','succeeded','{submission_cache_key}',{quoted(window)},'2026-09-06T11:58:00Z','2026-09-06T12:15:00Z',1,1,1,0,0,'{evidence.submissions_response_hash}');"
                f"INSERT INTO market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload) VALUES('{run_id}','{cache_key}',{quoted(window)},'{source_receipt_id}',{quoted(checkpoint)});"
                f"INSERT INTO market_discovery_stage_tasks(id,run_id,stage,capability_id,provider,query_kind,query_hash,dependency_ids,requested_window,state,attempt_count,request_budget,result) VALUES('{unpinned_task_id}','{run_id}','enrich','sec_filing_document','sec_edgar','filing_document',repeat('9',64),'[]',{quoted(window)},'attempting',1,1,'{{}}');"
            )

            sealed = json.loads(execute(
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(selection)});"
            ).stdout)
            assert sealed == {
                "duplicate": False, "manifest_id": selection["manifest"]["manifest_id"],
                "request_count": 1,
            }
            unselected_quote = {
                "ticker": "AAA", "instrument_type": "COMMON_STOCK",
                "security_revision_id": revision_id,
                "reference_manifest_id": manifest_id,
                "selection_manifest_id": selection["manifest"]["manifest_id"],
                "selected_task_id": "12121212-1212-4121-8121-121212121212",
                "cache_key": "0" * 64,
                "source_receipt_id": "13131313-1313-4131-8131-131313131313",
                "reservation_id": reservation_id,
            }
            assert execute(
                f"SELECT claim_market_intelligence_quote('{run_id}',{quoted(unselected_quote)});",
                check=False,
            ).returncode != 0
            replay = json.loads(execute(
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(selection)});"
            ).stdout)
            assert replay["duplicate"] is True
            tampered_selection = json.loads(json.dumps(selection))
            tampered_selection["requests"][0]["descriptor"]["cik"] = "0000000009"
            assert execute(
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(tampered_selection)});",
                check=False,
            ).returncode != 0

            execute(
                f"UPDATE market_discovery_stage_tasks SET state='attempting',attempt_count=1,"
                f"updated_at=statement_timestamp()+interval '1 second' WHERE id='{selected.request_id}';"
                f"UPDATE market_discovery_stage_tasks SET state='succeeded',result={quoted({'checkpoint': {'cache_key': submission_cache_key}})},"
                f"updated_at=statement_timestamp()+interval '2 seconds' WHERE id='{selected.request_id}';"
            )
            assert execute(
                "BEGIN;"
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(document_selection)});"
                "ROLLBACK;",
                check=False,
            ).returncode != 0
            tampered_submission_checkpoint = json.loads(json.dumps(submission_checkpoint))
            tampered_submission_checkpoint["items"][0]["source_url"] = (
                "https://www.sec.gov/Archives/edgar/data/1/"
                "000119312526200001/arbitrary.htm"
            )
            assert execute(
                "BEGIN;"
                f"INSERT INTO market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload) VALUES('{run_id}','{submission_cache_key}',{quoted(window)},'{submission_source_receipt_id}',{quoted(tampered_submission_checkpoint)});"
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(document_selection)});"
                "ROLLBACK;",
                check=False,
            ).returncode != 0
            execute(
                f"INSERT INTO market_collection_checkpoints(run_id,cache_key,request_window,source_receipt_id,payload) VALUES('{run_id}','{submission_cache_key}',{quoted(window)},'{submission_source_receipt_id}',{quoted(submission_checkpoint)});"
            )
            assert execute(
                "SELECT count(*) FROM market_reference_run_bindings b "
                "JOIN market_reference_snapshot_memberships member ON member.manifest_id=b.manifest_id "
                "JOIN market_security_reference_revisions s ON s.id=member.security_revision_id "
                f"WHERE b.run_id='{run_id}' AND b.manifest_id='{manifest_id}' "
                f"AND s.id='{revision_id}' AND s.entity_id='sec-cik:0000000001' AND s.eligible;"
            ).stdout.strip() == "1"
            descriptor_debug = json.loads(execute(
                "WITH r AS (SELECT value FROM jsonb_array_elements("
                f"{quoted(document_selection)}->'requests')) SELECT jsonb_build_object("
                "'descriptor',r.value->'descriptor','stored',jsonb_build_object("
                "'entity_id',s.entity_id,'security_id',s.security_id,'ticker',s.ticker,"
                "'instrument_type',s.instrument_type)) FROM r CROSS JOIN market_security_reference_revisions s "
                f"WHERE s.id='{revision_id}';"
            ).stdout)
            assert descriptor_debug["descriptor"]["security_id"] == descriptor_debug["stored"]["security_id"]
            assert execute(
                "WITH r AS (SELECT value FROM jsonb_array_elements("
                f"{quoted(document_selection)}->'requests')) SELECT count(*) FROM r,"
                "market_reference_run_bindings b JOIN market_reference_snapshot_memberships member "
                "ON member.manifest_id=b.manifest_id JOIN market_security_reference_revisions s "
                "ON s.id=member.security_revision_id WHERE b.run_id="
                f"'{run_id}' AND b.capability_id='sec_company_tickers_universe' "
                "AND b.reference_status IN ('healthy','reference_stale') "
                "AND b.manifest_id=(r.value->'descriptor'->>'reference_manifest_id')::uuid "
                "AND s.id=(r.value->'descriptor'->>'security_revision_id')::uuid AND s.eligible "
                "AND s.entity_id=r.value->'descriptor'->>'entity_id' "
                "AND s.security_id=r.value->'descriptor'->>'security_id' "
                "AND s.ticker=r.value->'descriptor'->>'ticker' "
                "AND s.instrument_type=r.value->'descriptor'->>'instrument_type';"
            ).stdout.strip() == "1"
            json.loads(execute(
                f"SELECT seal_market_enrichment_selection('{run_id}',{quoted(document_selection)});"
            ).stdout)
            frozen_context = json.loads(execute(
                f"SELECT read_market_discovery_context('{run_id}',100);"
            ).stdout)
            frozen_by_stage = {
                row["manifest"]["selection_stage"]: row
                for row in frozen_context["enrichment_selections"]
            }
            assert frozen_by_stage["initial"] == selection
            assert frozen_by_stage["filing_documents"] == document_selection
            assert execute(
                "SELECT count(*) FROM market_enrichment_request_descriptors "
                f"WHERE task_id='{task_id}' AND descriptor->>'source_receipt_id'='{source_receipt_id}' "
                f"AND descriptor->>'cache_key'='{cache_key}';"
            ).stdout.strip() == "1"
            execute(
                f"UPDATE market_discovery_stage_tasks SET state='attempting',attempt_count=1,"
                f"updated_at=statement_timestamp() WHERE id='{task_id}';"
            )
            task["query_hash"] = document_selection["requests"][0]["descriptor_hash"]
            task["dependency_ids"] = [selected.request_id]

            assert execute(
                f"SELECT market_exposure_fact_semantic_hash_v1({quoted(fact_row['fact'])});"
            ).stdout.strip() == fact_row["content_hash"]

            saved = json.loads(execute(
                f"SELECT checkpoint_market_discovery_stage('{run_id}',{quoted(fact_payload)});"
            ).stdout)
            assert saved["task"]["state"] == "succeeded"
            assert execute("SELECT count(*) FROM market_exposure_facts;").stdout.strip() == "1"
            replayed = json.loads(execute(
                f"SELECT checkpoint_market_discovery_stage('{run_id}',{quoted(fact_payload)});"
            ).stdout)
            assert replayed["duplicate"] is True

            unpinned_fact = json.loads(json.dumps(fact_row))
            unpinned_fact["security_revision_id"] = unpinned_revision_id
            unpinned_fact["fact"]["value"]["security_revision_id"] = unpinned_revision_id
            unpinned_fact["content_hash"] = hashlib.sha256(json.dumps(
                unpinned_fact["fact"], allow_nan=False, ensure_ascii=False,
                separators=(",", ":"), sort_keys=True,
            ).encode()).hexdigest()
            unpinned_fact["id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            unpinned_payload = json.loads(json.dumps(fact_payload))
            unpinned_payload["task"]["id"] = unpinned_task_id
            unpinned_payload["task"]["query_hash"] = "9" * 64
            unpinned_payload["exposure_facts"] = [unpinned_fact]
            assert execute(
                f"SELECT checkpoint_market_discovery_stage('{run_id}',{quoted(unpinned_payload)});",
                check=False,
            ).returncode != 0

            assert execute(
                "SELECT has_function_privilege('anon','public.seal_market_enrichment_selection(uuid,jsonb)','EXECUTE');"
            ).stdout.strip() == "f"
            assert execute(
                "SELECT has_function_privilege('service_role','public.seal_market_enrichment_selection(uuid,jsonb)','EXECUTE');"
            ).stdout.strip() == "t"
        finally:
            run("pg_ctl", "-D", data, "-m", "immediate", "-w", "stop", check=False)
