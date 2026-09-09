"""Read-only production evidence adapters. No JSON file can impersonate these sources.

GitHub deployment records reference an immutable Actions artifact by numeric ID.
The artifact must belong to a successful protected release workflow on main. DB
reads use one repeatable-read, read-only transaction and a restricted login.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
from typing import Mapping
from urllib.parse import unquote, urlparse
import zipfile

import psycopg
from psycopg.rows import dict_row

from lib.intelligence.canonical import EVENT_CANONICAL_SQL, RANKING_CANONICAL_SQL
from lib.release_baseline import (
    PRE_MIGRATION_OMISSION_REASON,
    PROTECTED_RELEASE_READ_TABLES,
    PRE_MIGRATION_ABSENT_TABLES,
    PRE_MIGRATION_UNREADABLE_TABLES,
    pre_migration_omissions,
)
from lib.release_reader_closure_contract import (
    CLOSURE_PRE_MIGRATION_ABSENT_TABLES,
    CLOSURE_PRE_MIGRATION_UNREADABLE_TABLES,
    CLOSURE_READ_TABLES,
    CLOSURE_READER_CONTRACT,
)
from scripts.export_recovery_bundle import MAX_PAYLOAD_BYTES
from scripts.verify_personal_stock_agent_v1 import path_is_safe, require

READER = "stock_agent_release_reader_runtime"
READER_PRIVILEGE_ROLE = "stock_agent_release_reader"
READER_AUTHORITY_CLOSURE_PATH = (
    "sql/migrations/20261017_release_reader_extension_closure.sql"
)
RECOVERY_SQL = {
    "decision_evaluations": "SELECT id::text,request_id::text,run_id::text,candidate_id::text,policy_version,input_digest,raw_action,final_action,policy_status,reason_codes,explanations,normalized,evidence,analyst,checker,created_at::text FROM public.decision_evaluations",
    "policy_comparisons": "SELECT id::text,run_id::text,packet_id::text,evaluation_id::text,comparison,created_at::text FROM public.market_policy_comparisons",
    "holdings": "SELECT ticker,shares::text AS shares,avg_cost::text AS average_cost,bucket,opened_at::text AS opened_at,notes,stop::text AS stop,target::text AS target,high_water_price::text AS high_water_price,hold_override_until::text AS hold_override_until,stop_alert_active,stop_near_alert_active,target_near_alert_active,target_alert_active FROM public.holdings",
    "transactions": "SELECT id::text AS id,ticker,qty::text AS quantity,price::text AS price,ts::text AS ts,side,source,executed_on::text AS executed_on FROM public.transactions",
    "commands": """SELECT id::text AS id,status,telegram_update_id::text AS telegram_update_id,chat_id::text AS chat_id,user_id::text AS user_id,
        operation,ticker,qty::text AS qty,price::text AS price,executed_on::text AS executed_on,bucket,expected_shares::text AS expected_shares,
        stop::text AS stop,amount::text AS amount,cadence,next_due_on::text AS next_due_on,
        expected_plan_updated_at::text AS expected_plan_updated_at,preview,
        confirmation_message_id::text AS confirmation_message_id,expires_at::text AS expires_at,
        applied_at::text AS applied_at,realized_pnl::text AS realized_pnl,result,error,created_at::text AS created_at,updated_at::text AS updated_at FROM public.portfolio_commands""",
    "command_acknowledgements": """SELECT command_id::text AS command_id,telegram_update_id::text AS telegram_update_id,status,result,error,
        lease_token::text AS lease_token,lease_expires_at::text AS lease_expires_at,attempt_count,
        created_at::text AS created_at,updated_at::text AS updated_at FROM public.portfolio_command_acknowledgements""",
    "runs": """SELECT id::text AS id,status,kind AS phase,started_at::text AS started_at,finished_at::text AS finished_at,
        data_as_of::text AS data_as_of,source_status,symbols,write_counts,telegram_message_ids,summary,error,
        scheduled_phase,scheduled_market_date::text AS scheduled_market_date,gateway_request_id::text AS gateway_request_id
        FROM public.analysis_runs""",
    "gateway_requests": """SELECT request_id::text AS request_id,operation,run_id::text AS run_id,status,lease_token::text AS lease_token,
        attempt_count,response,response_digest,created_at::text AS created_at,claimed_at::text AS claimed_at,finished_at::text AS finished_at
        FROM public.market_gateway_requests""",
    "policies": "SELECT version,config,active,created_at::text AS created_at,activated_at::text AS activated_at FROM public.market_policy_config",
    "intelligence_runs": """SELECT id::text AS id,phase,market_date::text AS market_date,policy_version,reservation_plan,request_window,
        created_at::text AS created_at FROM public.market_intelligence_runs""",
    "reference_manifests": """SELECT id::text AS id,run_id::text AS run_id,reference_version,revision,capability_version,
        taxonomy_version,source_hash,valid_from::text AS valid_from,valid_to::text AS valid_to,manifest,content_hash,
        created_at::text AS created_at FROM public.market_reference_manifests""",
    "security_reference_revisions": """SELECT id::text AS id,manifest_id::text AS manifest_id,run_id::text AS run_id,revision,
        security_id,entity_id,ticker,exchange,instrument_type,eligible,exclusion_reasons,aliases,source_ids,
        valid_from::text AS valid_from,valid_to::text AS valid_to,content_hash,
        semantic_encoding_version,issuer_names,created_at::text AS created_at
        FROM public.market_security_reference_revisions""",
    "reference_chunk_receipts": """SELECT manifest_id::text AS manifest_id,run_id::text AS run_id,capability_id,
        chunk_index,chunk_count,entry_count,chunk_hash,predecessor_manifest_id::text AS predecessor_manifest_id,
        payload,created_at::text AS created_at FROM public.market_reference_chunk_receipts""",
    "reference_snapshot_memberships": """SELECT manifest_id::text AS manifest_id,
        security_revision_id::text AS security_revision_id,security_id,ordinal,created_at::text AS created_at
        FROM public.market_reference_snapshot_memberships""",
    "reference_finalization_seals": """SELECT manifest_id::text AS manifest_id,run_id::text AS run_id,capability_id,
        predecessor_manifest_id::text AS predecessor_manifest_id,chunk_count,security_count,root_hash,
        finalized_at::text AS finalized_at FROM public.market_reference_finalization_seals""",
    "reference_run_bindings": """SELECT run_id::text AS run_id,capability_id,manifest_id::text AS manifest_id,
        reference_status,reference_as_of::text AS reference_as_of,source_retrieved_at::text AS source_retrieved_at,
        reference_age_seconds,request_payload,created_at::text AS created_at FROM public.market_reference_run_bindings""",
    "reference_predecessor_pins": """SELECT run_id::text AS run_id,capability_id,manifest_id::text AS manifest_id,
        reference_status,reference_as_of::text AS reference_as_of,source_retrieved_at::text AS source_retrieved_at,
        reference_age_seconds,request_payload,created_at::text AS created_at FROM public.market_reference_predecessor_pins""",
    "reference_transfer_requests": """SELECT request_id::text AS request_id,run_id::text AS run_id,operation,
        encoded_bytes,request_hash,request_payload,created_at::text AS created_at
        FROM public.market_reference_transfer_requests""",
    "reference_transfer_responses": """SELECT request_id::text AS request_id,run_id::text AS run_id,
        encoded_bytes,response_hash,created_at::text AS created_at
        FROM public.market_reference_transfer_responses""",
    "discovery_stage_tasks": """SELECT id::text AS id,run_id::text AS run_id,stage,capability_id,provider,query_kind,query_hash,
        dependency_ids,requested_window,state,attempt_count,request_budget,result,created_at::text AS created_at,
        updated_at::text AS updated_at FROM public.market_discovery_stage_tasks""",
    "enrichment_selection_manifests": """SELECT id::text AS id,run_id::text AS run_id,selection_stage,phase,
        request_count,provider_reservations,deferred_reasons,manifest,content_hash,created_at::text AS created_at
        FROM public.market_enrichment_selection_manifests""",
    "enrichment_request_descriptors": """SELECT id::text AS id,manifest_id::text AS manifest_id,run_id::text AS run_id,
        task_id::text AS task_id,provider,capability_id,query_kind,descriptor,content_hash,created_at::text AS created_at
        FROM public.market_enrichment_request_descriptors""",
    "theme_episode_revisions": """SELECT id::text AS id,run_id::text AS run_id,task_id::text AS task_id,theme_id,revision,
        episode,source_ids,valid_from::text AS valid_from,valid_to::text AS valid_to,content_hash,
        created_at::text AS created_at FROM public.market_theme_episode_revisions""",
    "exposure_facts": """SELECT id::text AS id,run_id::text AS run_id,task_id::text AS task_id,
        security_revision_id::text AS security_revision_id,theme_episode_revision_id::text AS theme_episode_revision_id,
        exposure_kind,fact,source_ids,valid_from::text AS valid_from,valid_to::text AS valid_to,content_hash,
        created_at::text AS created_at FROM public.market_exposure_facts""",
    "research_nominations": """SELECT id::text AS id,run_id::text AS run_id,task_id::text AS task_id,
        security_revision_id::text AS security_revision_id,theme_episode_revision_id::text AS theme_episode_revision_id,
        exposure_fact_ids,state,rationale,created_at::text AS created_at,updated_at::text AS updated_at
        FROM public.market_research_nominations""",
    "theme_episode_revisions_v2": """SELECT revision_id::text AS revision_id,theme_id,episode_id::text AS episode_id,
        revision,identity_version,anchor_hash,origin_run_id::text AS origin_run_id,
        predecessor_revision_id::text AS predecessor_revision_id,predecessor_content_hash,theme_mechanism,
        subject_identity,jurisdiction,effective_period_start::text AS effective_period_start,
        effective_period_end::text AS effective_period_end,authoritative_id,source_membership,source_ids,
        supporting_source_ids,opposing_source_ids,added_source_ids,investigated_entity_ids,missing_questions,
        invalidation_conditions,
        to_char(first_seen AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS first_seen,
        to_char(last_seen AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS last_seen,
        to_char(next_review_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS next_review_at,
        to_char(expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS expires_at,
        state,closure_reason,reopen_reason,
        content_hash,execution_allowed,created_at::text AS created_at FROM public.market_theme_episode_revisions_v2""",
    "reviewer_identity_receipts_v2": """SELECT receipt_id::text AS receipt_id,run_id::text AS run_id,
        packet_id::text AS packet_id,packet_hash,reference_manifest_id::text AS reference_manifest_id,
        actor_identity,reviewed_role,predecessor_receipt_id::text AS predecessor_receipt_id,review_hash,
        reviewed_at::text AS reviewed_at,execution_allowed FROM public.market_reviewer_identity_receipts_v2""",
    "research_nomination_requests_v2": """SELECT request_id::text AS request_id,run_id::text AS run_id,
        packet_id::text AS packet_id,reviewer_receipt_id::text AS reviewer_receipt_id,request_hash,
        accepted_count,response,created_at::text AS created_at FROM public.market_research_nomination_requests_v2""",
    "research_nominations_v2": """SELECT nomination_id::text AS nomination_id,request_id::text AS request_id,
        origin_run_id::text AS origin_run_id,packet_id::text AS packet_id,packet_hash,
        reviewer_receipt_id::text AS reviewer_receipt_id,actor_identity,reviewed_role,
        reference_manifest_id::text AS reference_manifest_id,theme_id,entity_id,security_id,relationship_role,
        reason,evidence_ids,required_evidence_kind,priority,created_at::text AS created_at,
        expires_at::text AS expires_at,execution_allowed FROM public.market_research_nominations_v2""",
    "research_nomination_lifecycle_v2": """SELECT receipt_id::text AS receipt_id,
        nomination_id::text AS nomination_id,transition_run_id::text AS transition_run_id,
        predecessor_receipt_id::text AS predecessor_receipt_id,state,reason,selection_descriptor,
        created_at::text AS created_at,receipt_hash,execution_allowed FROM public.market_research_nomination_lifecycle_v2""",
    "intelligence_memory_context_bindings_v2": """SELECT run_id::text AS run_id,as_of::text AS as_of,
        reference_manifest_id::text AS reference_manifest_id,reference_hash,selected_revision_ids,
        selected_nomination_ids,context,snapshot_hash,created_at::text AS created_at
        FROM public.market_intelligence_memory_context_bindings_v2""",
    "intelligence_run_events": """SELECT id::text AS id,run_id::text AS run_id,status,detail,created_at::text AS created_at
        FROM public.market_intelligence_run_events""",
    "source_quota_reservations": """SELECT id::text AS id,run_id::text AS run_id,provider,market_date::text AS market_date,
        phase,reserved_requests,cache_keys,created_at::text AS created_at FROM public.market_source_quota_reservations""",
    "source_receipts": """SELECT id::text AS id,run_id::text AS run_id,reservation_id::text AS reservation_id,
        provider,status,cache_key,requested_window,
        to_char(retrieved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.MS"Z"') AS retrieved_at,
        expires_at::text AS expires_at,
        request_cost,upstream_remaining,returned_count,accepted_count,duplicate_count,dropped_count,error,response_hash,
        created_at::text AS created_at FROM public.market_source_receipts""",
    "source_items": """SELECT id::text AS id,source_receipt_id::text AS source_receipt_id,provider,upstream_item_id,
        canonical_url,published_at::text AS published_at,effective_at::text AS effective_at,title,normalized_text,
        canonical_content,content_hash,metadata,created_at::text AS created_at FROM public.market_source_items""",
    "intelligence_run_items": """SELECT id::text AS id,run_id::text AS run_id,source_item_id::text AS source_item_id,
        source_receipt_id::text AS source_receipt_id,disposition,drop_reason,created_at::text AS created_at
        FROM public.market_intelligence_run_items""",
    "source_item_provenance": """SELECT source_item_id::text AS source_item_id,provider,canonical_item_url,request_url,
        retrieved_at::text AS retrieved_at,reporting_at::text AS reporting_at,entity_ids,security_ids,discovery_status,
        created_at::text AS created_at FROM public.market_source_item_provenance""",
    "run_source_item_provenance": """SELECT run_item_id::text AS run_item_id,run_id::text AS run_id,
        source_item_id::text AS source_item_id,source_receipt_id::text AS source_receipt_id,provider,request_url,
        retrieved_at::text AS retrieved_at,reporting_at::text AS reporting_at,entity_ids,security_ids,discovery_status,
        created_at::text AS created_at FROM public.market_run_source_item_provenance""",
    "events": """SELECT id::text AS id,run_id::text AS run_id,event_type,title,summary,
        CASE WHEN occurred_at IS NULL THEN NULL ELSE to_char(occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') END AS occurred_at,
        CASE WHEN effective_at IS NULL THEN NULL ELSE to_char(effective_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') END AS effective_at,
        materiality::text AS materiality,
        confidence::text AS confidence,evidence_item_ids,content_hash,created_at::text AS created_at
        FROM public.market_events""",
    "candidate_rankings": """SELECT id::text AS id,run_id::text AS run_id,event_id::text AS event_id,candidate_key,ticker,
        rank,component_scores,total_score::text AS total_score,qualified,veto_reasons,exposure_item_ids,content_hash,
        created_at::text AS created_at FROM public.market_candidate_rankings""",
    "collection_checkpoints": """SELECT run_id::text AS run_id,cache_key,request_window,source_receipt_id::text AS source_receipt_id,
        payload,created_at::text AS created_at FROM public.market_collection_checkpoints""",
    "collection_checkpoint_history": """SELECT run_id::text AS run_id,cache_key,source_receipt_id::text AS source_receipt_id,
        payload,replaced_at::text AS replaced_at FROM public.market_collection_checkpoint_history""",
    "collection_completions": """SELECT completion_id::text AS completion_id,run_id::text AS run_id,payload,receipt,
        created_at::text AS created_at FROM public.market_intelligence_collection_completions""",
    "packets": """SELECT id::text AS id,run_id::text AS run_id,policy_version,status,candidate_count,evidence_count,
        packet_hash,packet,created_at::text AS created_at FROM public.market_evidence_packets""",
    "reports": """SELECT id::text AS id,run_id::text AS run_id,packet_id::text AS packet_id,idempotency_key,market_date::text AS market_date,kind,
        report_hash,rendered_hash,report,rendered_text,created_at::text AS created_at FROM public.market_reports""",
    "report_origins": """SELECT request_id::text AS request_id,run_id::text AS run_id,scheduled_phase,market_date::text AS market_date,
        requested_kind,requested_report_id::text AS requested_report_id,requested_packet_id::text AS requested_packet_id,
        requested_idempotency_key,requested_report_hash,created_at::text AS created_at FROM public.market_report_request_origins""",
    "publications": """SELECT report_id::text AS report_id,idempotency_key,status,telegram_message_ids,
        telegram_accepted_at::text AS telegram_accepted_at,suppression_reason,attempt_count,lease_token::text AS lease_token,
        lease_expires_at::text AS lease_expires_at,error,created_at::text AS created_at,updated_at::text AS updated_at
        FROM public.market_report_publications""",
    "evaluation_publications": """SELECT id::text AS id,idempotency_key::text AS idempotency_key,run_id::text AS run_id,
        market_date::text AS market_date,phase,kind,template_version,rendered_body,rendered_hash,status,telegram_message_ids,
        attempt_count,lease_token::text AS lease_token,sending_started_at::text AS sending_started_at,delivered_at::text AS delivered_at,
        telegram_accepted_at::text AS telegram_accepted_at,error,created_at::text AS created_at,updated_at::text AS updated_at
        FROM public.market_publications""",
    "cash_ledger_state": "SELECT singleton,revision::text AS revision,updated_at::text AS updated_at FROM public.portfolio_cash_ledger_state",
    "cash_snapshots": """SELECT id::text AS id,as_of::text AS as_of,fresh_through::text AS fresh_through,
        ledger_watermark::text AS ledger_watermark,core_available::text AS core_available,growth_available::text AS growth_available,
        speculative_available::text AS speculative_available,created_at::text AS created_at FROM public.reconciled_cash_snapshots""",
    "run_terminal_outcomes": """SELECT run_id::text AS run_id,evaluation_request_id::text AS evaluation_request_id,outcome,
        created_at::text AS created_at FROM public.market_run_terminal_outcomes""",
    "roles": """SELECT r.rolname AS role,r.rolcanlogin AS login,r.rolinherit AS inherit,r.rolsuper AS superuser,r.rolbypassrls AS bypass_rls,
        COALESCE((SELECT jsonb_agg(parent.rolname ORDER BY parent.rolname) FROM pg_catalog.pg_auth_members m
                  JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid WHERE m.member=r.oid),'[]'::jsonb) AS memberships,
        COALESCE((SELECT jsonb_agg(g.privilege ORDER BY g.privilege) FROM (
            SELECT a.privilege_type||':'||n.nspname||'.'||c.relname||':grantable='||a.is_grantable::text AS privilege
              FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE a.grantee=r.oid
            UNION ALL
            SELECT a.privilege_type||':'||n.nspname||'.'||c.relname||'.'||col.attname||':grantable='||a.is_grantable::text
              FROM pg_catalog.pg_attribute col JOIN pg_catalog.pg_class c ON c.oid=col.attrelid
              JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
              CROSS JOIN LATERAL aclexplode(col.attacl) a WHERE a.grantee=r.oid
        ) g),'[]'::jsonb) AS grants
        FROM pg_catalog.pg_roles r WHERE r.rolname IN ('stock_agent_dashboard','stock_agent_dashboard_runtime')""",
    "schema_version": """SELECT version,statements
                         FROM supabase_migrations.schema_migrations""",
    "release_migration_ledger": """SELECT path,version,sha256,applied_at::text
                                  FROM public.stock_agent_release_migration_ledger""",
}
READ_TABLES = PROTECTED_RELEASE_READ_TABLES

LEGACY_EXTENSION_RELATION_COLUMNS = {
    "pg_stat_statements": {
        "userid", "dbid", "toplevel", "queryid", "query", "plans",
        "total_plan_time", "min_plan_time", "max_plan_time", "mean_plan_time",
        "stddev_plan_time", "calls", "total_exec_time", "min_exec_time",
        "max_exec_time", "mean_exec_time", "stddev_exec_time", "rows",
        "shared_blks_hit", "shared_blks_read", "shared_blks_dirtied",
        "shared_blks_written", "local_blks_hit", "local_blks_read",
        "local_blks_dirtied", "local_blks_written", "temp_blks_read",
        "temp_blks_written", "shared_blk_read_time", "shared_blk_write_time",
        "local_blk_read_time", "local_blk_write_time", "temp_blk_read_time",
        "temp_blk_write_time", "wal_records", "wal_fpi", "wal_bytes",
        "jit_functions", "jit_generation_time", "jit_inlining_count",
        "jit_inlining_time", "jit_optimization_count", "jit_optimization_time",
        "jit_emission_count", "jit_emission_time", "jit_deform_count",
        "jit_deform_time", "stats_since", "minmax_stats_since",
    },
    "pg_stat_statements_info": {"dealloc", "stats_reset"},
}

LEGACY_EXTENSION_FUNCTIONS = {
    "extensions.armor(bytea)": "pgcrypto",
    "extensions.armor(bytea,text[],text[])": "pgcrypto",
    "extensions.crypt(text,text)": "pgcrypto",
    "extensions.dearmor(text)": "pgcrypto",
    "extensions.decrypt(bytea,bytea,text)": "pgcrypto",
    "extensions.decrypt_iv(bytea,bytea,bytea,text)": "pgcrypto",
    "extensions.digest(bytea,text)": "pgcrypto",
    "extensions.digest(text,text)": "pgcrypto",
    "extensions.encrypt(bytea,bytea,text)": "pgcrypto",
    "extensions.encrypt_iv(bytea,bytea,bytea,text)": "pgcrypto",
    "extensions.gen_random_bytes(integer)": "pgcrypto",
    "extensions.gen_random_uuid()": "pgcrypto",
    "extensions.gen_salt(text)": "pgcrypto",
    "extensions.gen_salt(text,integer)": "pgcrypto",
    "extensions.hmac(bytea,bytea,text)": "pgcrypto",
    "extensions.hmac(text,text,text)": "pgcrypto",
    "extensions.pgp_armor_headers(text)": "pgcrypto",
    "extensions.pgp_key_id(bytea)": "pgcrypto",
    "extensions.pgp_pub_decrypt(bytea,bytea)": "pgcrypto",
    "extensions.pgp_pub_decrypt(bytea,bytea,text)": "pgcrypto",
    "extensions.pgp_pub_decrypt(bytea,bytea,text,text)": "pgcrypto",
    "extensions.pgp_pub_decrypt_bytea(bytea,bytea)": "pgcrypto",
    "extensions.pgp_pub_decrypt_bytea(bytea,bytea,text)": "pgcrypto",
    "extensions.pgp_pub_decrypt_bytea(bytea,bytea,text,text)": "pgcrypto",
    "extensions.pgp_pub_encrypt(text,bytea)": "pgcrypto",
    "extensions.pgp_pub_encrypt(text,bytea,text)": "pgcrypto",
    "extensions.pgp_pub_encrypt_bytea(bytea,bytea)": "pgcrypto",
    "extensions.pgp_pub_encrypt_bytea(bytea,bytea,text)": "pgcrypto",
    "extensions.pgp_sym_decrypt(bytea,text)": "pgcrypto",
    "extensions.pgp_sym_decrypt(bytea,text,text)": "pgcrypto",
    "extensions.pgp_sym_decrypt_bytea(bytea,text)": "pgcrypto",
    "extensions.pgp_sym_decrypt_bytea(bytea,text,text)": "pgcrypto",
    "extensions.pgp_sym_encrypt(text,text)": "pgcrypto",
    "extensions.pgp_sym_encrypt(text,text,text)": "pgcrypto",
    "extensions.pgp_sym_encrypt_bytea(bytea,text)": "pgcrypto",
    "extensions.pgp_sym_encrypt_bytea(bytea,text,text)": "pgcrypto",
    "extensions.pg_stat_statements(boolean)": "pg_stat_statements",
    "extensions.pg_stat_statements_info()": "pg_stat_statements",
}

_SAFE_FUNCTION_SIGNATURE = re.compile(r"[A-Za-z0-9_., ()\[\]]{1,192}\Z")
_MAX_FUNCTION_AUTHORITY_ISSUES = 16


class ReleaseReaderFunctionAuthorityError(RuntimeError):
    """Carry only bounded catalog identity needed for a read-only diagnostic."""

    def __init__(self, issues: list[dict[str, str]], total: int):
        super().__init__("release reader function authority is unsafe")
        self.issues = issues[:_MAX_FUNCTION_AUTHORITY_ISSUES]
        self.issue_count = total
        self.truncated = total > len(self.issues)


def _safe_function_identity(value: object) -> str:
    if isinstance(value, str) and _SAFE_FUNCTION_SIGNATURE.fullmatch(value):
        return value
    encoded = repr(value).encode("utf-8", errors="replace")
    return f"unreportable:{hashlib.sha256(encoded).hexdigest()}"


def with_schema_version_hashes(rows: object) -> list[dict[str, object]]:
    """Hash migration statements without granting the reader extension access."""
    require(
        isinstance(rows, list)
        and all(
            isinstance(row, Mapping)
            and isinstance(row.get("version"), str)
            and isinstance(row.get("statements"), list)
            and all(isinstance(statement, str) for statement in row["statements"])
            for row in rows
        ),
        "migration ledger rows are malformed",
    )
    return [
        {
            **dict(row),
            "sha256": hashlib.sha256(
                "\n".join(row["statements"]).encode()
            ).hexdigest(),
        }
        for row in rows
    ]


def release_reader_authority_closure_manifest() -> dict[str, str]:
    """Resolve the authority closure from the candidate's canonical manifest."""
    from scripts.deploy_owner_dashboard_api import candidate_migration_manifest

    matches = [
        item for item in candidate_migration_manifest()
        if item.get("path") == READER_AUTHORITY_CLOSURE_PATH
    ]
    require(
        len(matches) == 1
        and set(matches[0]) == {"path", "version", "sha256"}
        and matches[0].get("version") == "20261017"
        and re.fullmatch(r"[0-9a-f]{64}", matches[0].get("sha256", ""))
        is not None,
        "release reader authority closure manifest is unavailable",
    )
    return dict(matches[0])


def _administrative_reader_edge(edge: object) -> bool:
    if not isinstance(edge, Mapping):
        return False
    if edge.get("member_superuser") is True:
        return True
    return (
        edge.get("member") == "postgres"
        and edge.get("member_superuser") is False
        and edge.get("member_createrole") is True
        and edge.get("admin_option") is True
        and edge.get("inherit_option") is False
        and edge.get("set_option") is False
    )


def verify_release_reader_authority(
    snapshot: Mapping[str, object], readable_tables: tuple[str, ...], *,
    allow_legacy_extension_authority: bool = False,
) -> dict[str, object]:
    """Require an exact role graph and global read-only authority closure."""
    expected_roles = [
        {
            "role": READER_PRIVILEGE_ROLE, "login": False, "inherit": True,
            "superuser": False, "createdb": False, "createrole": False,
            "replication": False, "bypass_rls": False,
        },
        {
            "role": READER, "login": True, "inherit": True,
            "superuser": False, "createdb": False, "createrole": False,
            "replication": False, "bypass_rls": False,
        },
    ]
    require(snapshot.get("roles") == expected_roles,
            "release reader role has unsafe role authority")
    memberships = snapshot.get("memberships")
    require(isinstance(memberships, list),
            "release reader membership authority is unavailable")
    non_administrative = [
        edge for edge in memberships if not _administrative_reader_edge(edge)
    ]
    require(non_administrative == [{
        "member": READER,
        "granted": READER_PRIVILEGE_ROLE,
        "admin_option": False,
        "inherit_option": True,
        "set_option": True,
        "member_superuser": False,
        "member_createrole": False,
    }], "release reader membership is not exact")

    database_privileges = snapshot.get("database_privileges")
    require(isinstance(database_privileges, list),
            "release reader database authority is unavailable")
    require(all(isinstance(row, Mapping) for row in database_privileges),
            "release reader database authority is unavailable")
    require(
        {(row.get("privilege"), row.get("grantable"))
         for row in database_privileges if isinstance(row, Mapping)}
        == {("CONNECT", False), ("TEMPORARY", False)},
        "release reader database authority is unsafe",
    )

    schema_privileges = snapshot.get("schema_privileges")
    require(isinstance(schema_privileges, list),
            "release reader schema authority is unavailable")
    require(all(isinstance(row, Mapping) for row in schema_privileges),
            "release reader schema authority is unavailable")
    schemas = {
        (row.get("schema"), row.get("privilege"), row.get("grantable"))
        for row in schema_privileges if isinstance(row, Mapping)
    }
    allowed_schemas = {
        ("public", "USAGE", False),
        ("supabase_migrations", "USAGE", False),
    }
    required_schemas = {
        ("public", "USAGE", False),
        ("supabase_migrations", "USAGE", False),
    }
    if allow_legacy_extension_authority:
        allowed_schemas.add(("extensions", "USAGE", False))
        required_schemas.add(("extensions", "USAGE", False))
    require(schemas.issubset(allowed_schemas) and required_schemas.issubset(schemas),
            "release reader schema authority is unsafe")

    relation_privileges = snapshot.get("relation_privileges")
    require(isinstance(relation_privileges, list),
            "release reader relation authority is unavailable")
    require(all(isinstance(row, Mapping) for row in relation_privileges),
            "release reader relation authority is unavailable")
    public_relations = set()
    legacy_extension_relations = set()
    for row in relation_privileges:
        relation = (row.get("schema"), row.get("relation"))
        common_safe = (
            row.get("privilege") == "SELECT"
            and row.get("grantable") is False
        )
        if relation[0] == "public":
            require(
                common_safe
                and relation[1] in readable_tables
                and row.get("kind") in {"r", "p"}
                and row.get("extension") is None,
                "release reader relation authority is unsafe",
            )
            public_relations.add(relation[1])
        elif relation[0] == "extensions":
            require(
                allow_legacy_extension_authority
                and common_safe
                and relation[1] in LEGACY_EXTENSION_RELATION_COLUMNS
                and row.get("kind") == "v"
                and row.get("extension") == "pg_stat_statements",
                "release reader relation authority is unsafe",
            )
            legacy_extension_relations.add(relation[1])
        else:
            require(False, "release reader relation authority is unsafe")
    require(public_relations == set(readable_tables),
            "release reader relation authority is unsafe")

    column_privileges = snapshot.get("column_privileges")
    require(isinstance(column_privileges, list),
            "release reader column authority is unavailable")
    migration_columns = set()
    legacy_extension_columns: dict[str, set[str]] = {}
    for row in column_privileges:
        require(isinstance(row, Mapping),
                "release reader column authority is unavailable")
        relation = (row.get("schema"), row.get("relation"))
        if relation == ("supabase_migrations", "schema_migrations"):
            migration_columns.add(row.get("column"))
            require(
                row.get("privilege") == "SELECT"
                and row.get("grantable") is False
                and row.get("column") in {"version", "statements"}
                and row.get("kind") == "r"
                and row.get("extension") is None,
                "release reader column authority is unsafe",
            )
        elif relation[0] == "extensions":
            require(
                allow_legacy_extension_authority
                and relation[1] in legacy_extension_relations
                and row.get("column")
                    in LEGACY_EXTENSION_RELATION_COLUMNS[relation[1]]
                and row.get("privilege") == "SELECT"
                and row.get("grantable") is False
                and row.get("kind") == "v"
                and row.get("extension") == "pg_stat_statements",
                "release reader column authority is unsafe",
            )
            legacy_extension_columns.setdefault(relation[1], set()).add(
                row.get("column")
            )
        else:
            require(
                relation[0] == "public"
                and relation[1] in readable_tables
                and row.get("privilege") == "SELECT"
                and row.get("grantable") is False,
                "release reader column authority is unsafe",
            )
    require(migration_columns == {"version", "statements"},
            "release reader migration-ledger authority is incomplete")
    require(
        all(
            legacy_extension_columns.get(relation) ==
                LEGACY_EXTENSION_RELATION_COLUMNS[relation]
            for relation in legacy_extension_relations
        ),
        "release reader extension relation columns are unsafe",
    )

    require(snapshot.get("sequence_privileges") == [],
            "release reader sequence authority is unsafe")
    require(snapshot.get("large_object_privileges") == [],
            "release reader large-object authority is unsafe")
    require(snapshot.get("owned_objects") == [],
            "release reader may not own database objects")
    function_privileges = snapshot.get("function_privileges")
    require(isinstance(function_privileges, list),
            "release reader function authority is unavailable")
    actual_functions = set()
    function_issues = []
    for row in function_privileges:
        function = row.get("function") if isinstance(row, Mapping) else None
        reason = None
        if not isinstance(row, Mapping):
            reason = "malformed"
        elif not allow_legacy_extension_authority:
            reason = "unexpected_post_closure"
        elif row.get("schema") != "extensions":
            reason = "schema_not_allowed"
        elif not isinstance(function, str) or function not in LEGACY_EXTENSION_FUNCTIONS:
            reason = "function_not_allowlisted"
        elif row.get("extension") != LEGACY_EXTENSION_FUNCTIONS[function]:
            reason = "extension_identity_mismatch"
        elif row.get("security_definer") is not False:
            reason = "security_definer"
        elif row.get("grantable") is not False:
            reason = "grantable"
        elif row.get("language") != "c":
            reason = "language_mismatch"
        elif row.get("owner") in {READER, READER_PRIVILEGE_ROLE}:
            reason = "reader_owned"
        if reason is not None:
            function_issues.append({
                "function": _safe_function_identity(function),
                "reason": reason,
            })
        else:
            actual_functions.add(function)
    if (
        allow_legacy_extension_authority
        and "extensions.digest(bytea,text)" not in actual_functions
    ):
        function_issues.append({
            "function": "extensions.digest(bytea,text)",
            "reason": "required_function_missing",
        })
    if function_issues:
        function_issues.sort(key=lambda item: (item["function"], item["reason"]))
        raise ReleaseReaderFunctionAuthorityError(
            function_issues, len(function_issues),
        )
    return {
        "status": "verified",
        "runtime_role": READER,
        "privilege_role": READER_PRIVILEGE_ROLE,
        "read_table_count": len(readable_tables),
        "write_privileges": 0,
        "owned_objects": 0,
    }


RELEASE_READER_AUTHORITY_SQL = """SELECT /* release_reader_global_authority */
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'role',r.rolname,'login',r.rolcanlogin,'inherit',r.rolinherit,
      'superuser',r.rolsuper,'createdb',r.rolcreatedb,
      'createrole',r.rolcreaterole,'replication',r.rolreplication,
      'bypass_rls',r.rolbypassrls) ORDER BY r.rolname)
    FROM pg_catalog.pg_roles r
   WHERE r.rolname IN ('stock_agent_release_reader','stock_agent_release_reader_runtime')),'[]'::jsonb) AS roles,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'member',member.rolname,'granted',granted.rolname,
      'admin_option',membership.admin_option,
      'inherit_option',COALESCE((to_jsonb(membership)->>'inherit_option')::boolean,true),
      'set_option',COALESCE((to_jsonb(membership)->>'set_option')::boolean,true),
      'member_superuser',member.rolsuper,'member_createrole',member.rolcreaterole)
      ORDER BY granted.rolname,member.rolname)
    FROM pg_catalog.pg_auth_members membership
    JOIN pg_catalog.pg_roles member ON member.oid=membership.member
    JOIN pg_catalog.pg_roles granted ON granted.oid=membership.roleid
   WHERE member.rolname IN ('stock_agent_release_reader','stock_agent_release_reader_runtime')
      OR granted.rolname IN ('stock_agent_release_reader','stock_agent_release_reader_runtime')),'[]'::jsonb) AS memberships,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'privilege',privilege,'grantable',pg_catalog.has_database_privilege(
        current_user,pg_catalog.current_database(),privilege||' WITH GRANT OPTION')) ORDER BY privilege)
    FROM unnest(ARRAY['CONNECT','CREATE','TEMPORARY']) privilege
   WHERE pg_catalog.has_database_privilege(current_user,pg_catalog.current_database(),privilege)),'[]'::jsonb) AS database_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'schema',namespace.nspname,'privilege',privilege,
      'grantable',pg_catalog.has_schema_privilege(current_user,namespace.oid,privilege||' WITH GRANT OPTION'))
      ORDER BY namespace.nspname,privilege)
    FROM pg_catalog.pg_namespace namespace
    CROSS JOIN unnest(ARRAY['USAGE','CREATE']) privilege
   WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
     AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
     AND pg_catalog.has_schema_privilege(current_user,namespace.oid,privilege)),'[]'::jsonb) AS schema_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'schema',namespace.nspname,'relation',class.relname,'privilege',privilege,
      'kind',class.relkind,'extension',extension.extname,
      'grantable',pg_catalog.has_table_privilege(current_user,class.oid,privilege||' WITH GRANT OPTION'))
      ORDER BY namespace.nspname,class.relname,privilege)
    FROM pg_catalog.pg_class class
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid=class.relnamespace
    LEFT JOIN pg_catalog.pg_depend dependency ON dependency.classid='pg_class'::regclass
      AND dependency.objid=class.oid AND dependency.deptype='e'
    LEFT JOIN pg_catalog.pg_extension extension ON extension.oid=dependency.refobjid
    CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER']) privilege
   WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
     AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
     AND class.relkind IN ('r','p','v','m','f')
     AND pg_catalog.has_schema_privilege(current_user,namespace.oid,'USAGE')
     AND pg_catalog.has_table_privilege(current_user,class.oid,privilege)),'[]'::jsonb) AS relation_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'schema',namespace.nspname,'relation',class.relname,'column',attribute.attname,
      'kind',class.relkind,'extension',extension.extname,'privilege',privilege,
      'grantable',pg_catalog.has_column_privilege(
        current_user,class.oid,attribute.attnum,privilege||' WITH GRANT OPTION'))
      ORDER BY namespace.nspname,class.relname,attribute.attnum,privilege)
    FROM pg_catalog.pg_class class
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid=class.relnamespace
    JOIN pg_catalog.pg_attribute attribute ON attribute.attrelid=class.oid
    LEFT JOIN pg_catalog.pg_depend dependency ON dependency.classid='pg_class'::regclass
      AND dependency.objid=class.oid AND dependency.deptype='e'
    LEFT JOIN pg_catalog.pg_extension extension ON extension.oid=dependency.refobjid
    CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) privilege
   WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
     AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
     AND class.relkind IN ('r','p','v','m','f')
     AND attribute.attnum>0 AND NOT attribute.attisdropped
     AND pg_catalog.has_schema_privilege(current_user,namespace.oid,'USAGE')
     AND pg_catalog.has_column_privilege(current_user,class.oid,attribute.attnum,privilege)),'[]'::jsonb) AS column_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'schema',namespace.nspname,'sequence',class.relname,'privilege',privilege,
      'grantable',pg_catalog.has_sequence_privilege(current_user,class.oid,privilege||' WITH GRANT OPTION'))
      ORDER BY namespace.nspname,class.relname,privilege)
    FROM pg_catalog.pg_class class
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid=class.relnamespace
    CROSS JOIN unnest(ARRAY['USAGE','SELECT','UPDATE']) privilege
   WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
     AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
     AND class.relkind='S'
     AND pg_catalog.has_schema_privilege(current_user,namespace.oid,'USAGE')
     AND pg_catalog.has_sequence_privilege(current_user,class.oid,privilege)),'[]'::jsonb) AS sequence_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'oid',large_object.oid::text,'privilege',acl.privilege_type,
      'grantable',acl.is_grantable)
      ORDER BY large_object.oid,acl.privilege_type,acl.is_grantable)
    FROM pg_catalog.pg_largeobject_metadata large_object
    CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(
      large_object.lomacl,pg_catalog.acldefault('L',large_object.lomowner))) acl
   WHERE acl.privilege_type IN ('SELECT','UPDATE')
     AND (acl.grantee=0 OR pg_catalog.pg_has_role(
       current_user,acl.grantee,'USAGE'))),'[]'::jsonb) AS large_object_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'schema',namespace.nspname,'function',namespace.nspname||'.'||procedure.proname||
        '('||replace(pg_catalog.oidvectortypes(procedure.proargtypes),', ',',')||')',
      'security_definer',procedure.prosecdef,'extension',extension.extname,
      'language',language.lanname,'owner',owner.rolname,
      'grantable',pg_catalog.has_function_privilege(
        current_user,procedure.oid,'EXECUTE WITH GRANT OPTION'))
      ORDER BY namespace.nspname,procedure.proname,
        pg_catalog.oidvectortypes(procedure.proargtypes))
    FROM pg_catalog.pg_proc procedure
    JOIN pg_catalog.pg_namespace namespace ON namespace.oid=procedure.pronamespace
    JOIN pg_catalog.pg_roles owner ON owner.oid=procedure.proowner
    JOIN pg_catalog.pg_language language ON language.oid=procedure.prolang
    LEFT JOIN pg_catalog.pg_depend dependency ON dependency.classid='pg_proc'::regclass
      AND dependency.objid=procedure.oid AND dependency.deptype='e'
    LEFT JOIN pg_catalog.pg_extension extension ON extension.oid=dependency.refobjid
   WHERE namespace.nspname NOT IN ('pg_catalog','information_schema')
     AND namespace.nspname !~ '^pg_(toast|temp)(_|$)'
     AND pg_catalog.has_schema_privilege(current_user,namespace.oid,'USAGE')
     AND pg_catalog.has_function_privilege(current_user,procedure.oid,'EXECUTE')),'[]'::jsonb) AS function_privileges,
  COALESCE((SELECT jsonb_agg(jsonb_build_object(
      'catalog',dependency.classid::regclass::text,
      'object_id',dependency.objid::text,
      'description',pg_catalog.pg_describe_object(
        dependency.classid,dependency.objid,dependency.objsubid))
      ORDER BY dependency.classid::regclass::text,dependency.objid,dependency.objsubid)
    FROM pg_catalog.pg_shdepend dependency
    JOIN pg_catalog.pg_roles owner ON owner.oid=dependency.refobjid
    JOIN pg_catalog.pg_database database
      ON database.datname=pg_catalog.current_database()
   WHERE dependency.refclassid='pg_authid'::regclass
     AND dependency.deptype='o'
     AND owner.rolname IN ('stock_agent_release_reader','stock_agent_release_reader_runtime')
     AND dependency.dbid IN (0,database.oid)),'[]'::jsonb) AS owned_objects"""

class PostgresReadOnlySource:
    def __init__(self, database_url: str, project_ref: str, *, isolated_guard: bool = False,
                 production_project_ref: str | None = None,
                 pre_migration_baseline: bool = False,
                 reader_contract: str = "current"):
        parsed = urlparse(database_url)
        require(bool(re.fullmatch(r"[a-z0-9]{20}", project_ref)), "exact database project identity is required")
        user = unquote(parsed.username or "")
        direct = parsed.hostname == f"db.{project_ref}.supabase.co" and user == READER
        pooler = bool(re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", parsed.hostname or "")) and user == f"{READER}.{project_ref}"
        require(parsed.scheme in {"postgres", "postgresql"} and (direct or pooler) and parsed.port in {None, 5432}
                and parsed.path == "/postgres" and parsed.password and not parsed.query and not parsed.fragment,
                "database URL must identify the exact project and read-only login")
        if isolated_guard:
            require(bool(re.fullmatch(r"[a-z0-9]{20}", production_project_ref or "")) and project_ref != production_project_ref,
                    "guarded restore project must differ from production")
        require(
            reader_contract in {"current", CLOSURE_READER_CONTRACT},
            "release reader contract is unavailable",
        )
        self._url = database_url
        self.project_ref = project_ref
        self.isolated_guard = isolated_guard
        self.pre_migration_baseline = pre_migration_baseline
        self.reader_contract = reader_contract
        if reader_contract == CLOSURE_READER_CONTRACT:
            self._contract_read_tables = CLOSURE_READ_TABLES
            self._contract_absent_tables = CLOSURE_PRE_MIGRATION_ABSENT_TABLES
            self._contract_unreadable_tables = (
                CLOSURE_PRE_MIGRATION_UNREADABLE_TABLES
            )
        else:
            self._contract_read_tables = READ_TABLES
            self._contract_absent_tables = PRE_MIGRATION_ABSENT_TABLES
            self._contract_unreadable_tables = PRE_MIGRATION_UNREADABLE_TABLES
        self.connection = None
        self._read_tables: tuple[str, ...] = ()
        self._pre_migration_omissions: dict[str, object] | None = None

    def __enter__(self):
        try:
            self.connection = psycopg.connect(self._url, row_factory=dict_row, sslmode="verify-full", connect_timeout=15)
            self.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.connection.execute("SET LOCAL statement_timeout='30s'")
            row = self.query("""SELECT current_user AS role,current_database() AS database,
                inet_server_addr()::text AS server,inet_server_port() AS port,
                current_setting('transaction_read_only') AS read_only,rolsuper,rolbypassrls
                FROM pg_catalog.pg_roles WHERE rolname=current_user""")[0]
            require(row["role"] == READER and row["read_only"] == "on" and not row["rolsuper"] and not row["rolbypassrls"]
                    and row["server"] and row["database"], "queried database identity is not a restricted read-only source")
            readable_tables = []
            absent_tables = []
            unreadable_tables = []
            contract_read_tables = getattr(
                self, "_contract_read_tables", READ_TABLES,
            )
            contract_absent_tables = getattr(
                self, "_contract_absent_tables", PRE_MIGRATION_ABSENT_TABLES,
            )
            contract_unreadable_tables = getattr(
                self, "_contract_unreadable_tables",
                PRE_MIGRATION_UNREADABLE_TABLES,
            )
            for table in contract_read_tables:
                presence = self.query("SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{table}",))
                require(len(presence) == 1 and type(presence[0].get("present")) is bool,
                        "release table identity is unavailable")
                if not presence[0]["present"]:
                    absent_tables.append(table)
                    continue
                privileges = self.query("SELECT has_table_privilege(current_user,%s,'SELECT') AS readable,has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER') AS writable", (f"public.{table}", f"public.{table}"))
                require(len(privileges) == 1
                        and type(privileges[0].get("readable")) is bool
                        and type(privileges[0].get("writable")) is bool,
                        "release table privileges are unavailable")
                require(privileges[0]["writable"] is False,
                        "read-only database source lacks SELECT or has write authority")
                if privileges[0]["readable"] is not True:
                    unreadable_tables.append(table)
                    continue
                policy = self.query("""SELECT c.relrowsecurity AS rls_enabled,
                    NOT pg_has_role(current_user,c.relowner,'MEMBER') AS reader_is_not_owner,
                    EXISTS (
                        SELECT 1 FROM pg_catalog.pg_policy p
                        WHERE p.polrelid=c.oid AND p.polcmd IN ('r','*') AND p.polpermissive
                          AND pg_get_expr(p.polqual,p.polrelid)='true'
                          AND EXISTS (
                              SELECT 1 FROM unnest(p.polroles) AS role_oid
                              WHERE role_oid=0 OR pg_has_role(current_user,role_oid,'MEMBER')
                          )
                    ) AS unrestricted_select,
                    NOT EXISTS (
                        SELECT 1 FROM pg_catalog.pg_policy p
                        WHERE p.polrelid=c.oid AND p.polcmd IN ('r','*') AND NOT p.polpermissive
                          AND pg_get_expr(p.polqual,p.polrelid) IS DISTINCT FROM 'true'
                          AND EXISTS (
                              SELECT 1 FROM unnest(p.polroles) AS role_oid
                              WHERE role_oid=0 OR pg_has_role(current_user,role_oid,'MEMBER')
                          )
                    ) AS no_restrictive_filter
                    FROM pg_catalog.pg_class c
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=%s""", (table,))
                require(len(policy) == 1
                        and policy[0].get("rls_enabled") is True
                        and policy[0].get("reader_is_not_owner") is True
                        and policy[0].get("unrestricted_select") is True
                        and policy[0].get("no_restrictive_filter") is True,
                        "read-only database source has incomplete row security coverage")
                readable_tables.append(table)
            legacy_extension_authority = False
            if self.pre_migration_baseline:
                unmigrated = (
                    tuple(absent_tables) == contract_absent_tables
                    and tuple(unreadable_tables) == contract_unreadable_tables
                )
                migrated = not absent_tables and not unreadable_tables
                require(unmigrated or migrated,
                        "pre-migration release reader baseline mismatch")
                self._pre_migration_omissions = (
                    pre_migration_omissions(migrated=True)
                    if migrated
                    else {
                        "reason": PRE_MIGRATION_OMISSION_REASON,
                        "absent_tables": list(contract_absent_tables),
                        "unreadable_tables": list(contract_unreadable_tables),
                    }
                )
                closure = release_reader_authority_closure_manifest()
                closure_rows = self.query(
                    """SELECT path,version,sha256
                         FROM public.stock_agent_release_migration_ledger
                        WHERE path=%s OR version=%s""",
                    (closure["path"], closure["version"]),
                )
                require(
                    closure_rows in ([], [closure])
                    and (not closure_rows or migrated),
                    "release reader authority closure ledger mismatch",
                )
                legacy_extension_authority = not closure_rows
            else:
                require(not absent_tables, "release table is missing")
                require(not unreadable_tables,
                        "read-only database source lacks SELECT or has write authority")
            self._read_tables = tuple(readable_tables)
            authority = self.query(RELEASE_READER_AUTHORITY_SQL)
            require(len(authority) == 1,
                    "release reader authority snapshot is unavailable")
            self._authority = verify_release_reader_authority(
                authority[0], self._read_tables,
                allow_legacy_extension_authority=legacy_extension_authority,
            )
            self._identity = {"project_ref": self.project_ref, "connection_id": hashlib.sha256(f"{row['server']}:{row['port']}/{row['database']}".encode()).hexdigest(),
                              "read_only": True, "isolated_guard": self.isolated_guard}
            return self
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def __exit__(self, *_exc):
        if self.connection is not None:
            self.connection.rollback()
            self.connection.close()

    def identity(self):
        require(self.connection is not None and not self.connection.closed, "read-only source is not connected")
        return dict(self._identity)

    def authority_receipt(self) -> dict[str, object]:
        require(self.connection is not None and not self.connection.closed,
                "read-only source is not connected")
        return dict(self._authority)

    def pre_migration_scope(self) -> dict[str, object]:
        require(
            self.connection is not None
            and not self.connection.closed
            and self.pre_migration_baseline
            and self._pre_migration_omissions is not None,
            "pre-migration source scope is unavailable",
        )
        return dict(self._pre_migration_omissions)

    def query(self, sql: str, parameters: tuple = ()) -> list[dict]:
        require(self.connection is not None and sql.lstrip().upper().startswith("SELECT "), "only fixed SELECT evidence queries are permitted")
        with self.connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, parameters)
            return [dict(row) for row in cursor.fetchall()]

    def read_records(self):
        self.identity()
        records = {name: self.query(sql) for name, sql in RECOVERY_SQL.items()}
        records["schema_version"] = with_schema_version_hashes(
            records["schema_version"]
        )
        return records

    def counts(self):
        self.identity()
        return {name: self.query(f"SELECT count(*) AS count FROM ({sql}) AS records")[0]["count"] for name, sql in RECOVERY_SQL.items()}

    def refresh_snapshot(self):
        """Begin a fresh read-only snapshot after an isolated writer commits restore data."""
        self.identity()
        self.connection.rollback()
        self.connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.connection.execute("SET LOCAL statement_timeout='30s'")

    def dry_run_snapshot(self) -> dict:
        """Hash canonical full rows across every release write surface."""
        self.identity()
        tables = {}
        for name in self._read_tables:
            rows = self.query(f"SELECT to_jsonb(t) AS row FROM public.{name} AS t ORDER BY to_jsonb(t)::text")
            canonical_rows = [json.dumps(row["row"], sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in rows]
            require(all(isinstance(value, str) for value in canonical_rows), "dry-run rows are malformed")
            tables[name] = {
                "count": len(canonical_rows),
                "rows_sha256": hashlib.sha256("\n".join(canonical_rows).encode()).hexdigest(),
            }
        snapshot = {"source": self.identity(), "tables": tables}
        if self._pre_migration_omissions is not None:
            snapshot["pre_migration_omissions"] = dict(self._pre_migration_omissions)
        return snapshot

    def release_rows(self, run_id: str) -> dict:
        require(bool(re.fullmatch(r"[0-9a-f-]{36}", run_id)), "run UUID is required")
        selected_manifests = (
            "SELECT manifest_id FROM public.market_reference_run_bindings "
            "WHERE run_id=%s::uuid AND manifest_id IS NOT NULL"
        )
        queries = {
            "run": "SELECT id::text AS id,kind,scheduled_phase,scheduled_market_date::text AS scheduled_market_date,status,started_at::text AS started_at,finished_at::text AS finished_at,gateway_request_id::text AS gateway_request_id,telegram_message_ids FROM public.analysis_runs WHERE id=%s::uuid",
            "intelligence_runs": "SELECT id::text AS id,phase,market_date::text AS market_date,reservation_plan,request_window FROM public.market_intelligence_runs WHERE id=%s::uuid",
            "reference_manifests": RECOVERY_SQL["reference_manifests"] + f" WHERE id IN ({selected_manifests})",
            "security_reference_revisions": RECOVERY_SQL["security_reference_revisions"] + f" WHERE id IN (SELECT security_revision_id FROM public.market_reference_snapshot_memberships WHERE manifest_id IN ({selected_manifests}))",
            "reference_chunk_receipts": RECOVERY_SQL["reference_chunk_receipts"] + f" WHERE manifest_id IN ({selected_manifests})",
            "reference_snapshot_memberships": RECOVERY_SQL["reference_snapshot_memberships"] + f" WHERE manifest_id IN ({selected_manifests})",
            "reference_finalization_seals": RECOVERY_SQL["reference_finalization_seals"] + f" WHERE manifest_id IN ({selected_manifests})",
            "reference_run_bindings": RECOVERY_SQL["reference_run_bindings"] + " WHERE run_id=%s::uuid",
            "reference_predecessor_pins": RECOVERY_SQL["reference_predecessor_pins"] + " WHERE run_id=%s::uuid",
            "reference_transfer_requests": RECOVERY_SQL["reference_transfer_requests"] + " WHERE run_id=%s::uuid",
            "reference_transfer_responses": RECOVERY_SQL["reference_transfer_responses"] + " WHERE run_id=%s::uuid",
            "discovery_stage_tasks": RECOVERY_SQL["discovery_stage_tasks"] + " WHERE run_id=%s::uuid",
            "enrichment_selection_manifests": RECOVERY_SQL["enrichment_selection_manifests"] + " WHERE run_id=%s::uuid",
            "enrichment_request_descriptors": RECOVERY_SQL["enrichment_request_descriptors"] + " WHERE run_id=%s::uuid",
            "theme_episode_revisions": RECOVERY_SQL["theme_episode_revisions"] + " WHERE run_id=%s::uuid",
            "exposure_facts": RECOVERY_SQL["exposure_facts"] + " WHERE run_id=%s::uuid",
            "research_nominations": RECOVERY_SQL["research_nominations"] + " WHERE run_id=%s::uuid",
            "theme_episode_revisions_v2": RECOVERY_SQL["theme_episode_revisions_v2"] + " WHERE origin_run_id=%s::uuid",
            "reviewer_identity_receipts_v2": RECOVERY_SQL["reviewer_identity_receipts_v2"] + " WHERE run_id=%s::uuid",
            "research_nomination_requests_v2": RECOVERY_SQL["research_nomination_requests_v2"] + " WHERE run_id=%s::uuid",
            "research_nominations_v2": RECOVERY_SQL["research_nominations_v2"] + " WHERE origin_run_id=%s::uuid",
            "research_nomination_lifecycle_v2": RECOVERY_SQL["research_nomination_lifecycle_v2"] + " WHERE transition_run_id=%s::uuid",
            "intelligence_memory_context_bindings_v2": RECOVERY_SQL["intelligence_memory_context_bindings_v2"] + " WHERE run_id=%s::uuid",
            "source_quota_reservations": RECOVERY_SQL["source_quota_reservations"] + """ WHERE run_id=%s::uuid OR id IN (
                SELECT receipt.reservation_id FROM public.market_source_receipts receipt
                JOIN public.market_source_items item ON item.source_receipt_id=receipt.id
                JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
                WHERE run_item.run_id=%s::uuid
            )""",
            "source_receipts": RECOVERY_SQL["source_receipts"] + """ WHERE run_id=%s::uuid OR id IN (
                SELECT item.source_receipt_id FROM public.market_source_items item
                JOIN public.market_intelligence_run_items run_item ON run_item.source_item_id=item.id
                WHERE run_item.run_id=%s::uuid
            )""",
            "source_items": RECOVERY_SQL["source_items"] + " WHERE id IN (SELECT source_item_id FROM public.market_intelligence_run_items WHERE run_id=%s::uuid)",
            "intelligence_run_items": RECOVERY_SQL["intelligence_run_items"] + " WHERE run_id=%s::uuid",
            "source_item_provenance": RECOVERY_SQL["source_item_provenance"] + " WHERE source_item_id IN (SELECT source_item_id FROM public.market_intelligence_run_items WHERE run_id=%s::uuid)",
            "run_source_item_provenance": RECOVERY_SQL["run_source_item_provenance"] + " WHERE run_id=%s::uuid",
            "completions": RECOVERY_SQL["collection_completions"] + " WHERE run_id=%s::uuid",
            "run_events": "SELECT id::text AS id,run_id::text AS run_id,status FROM public.market_intelligence_run_events WHERE run_id=%s::uuid",
            "checkpoints": "SELECT run_id::text AS run_id,cache_key FROM public.market_collection_checkpoints WHERE run_id=%s::uuid",
            "packets": RECOVERY_SQL["packets"] + " WHERE run_id=%s::uuid",
            "reports": "SELECT id::text AS id,run_id::text AS run_id,packet_id::text AS packet_id,report_hash,rendered_hash,report,rendered_text,idempotency_key,market_date::text AS market_date,kind FROM public.market_reports WHERE run_id=%s::uuid",
            "publications": RECOVERY_SQL["publications"] + " WHERE report_id IN (SELECT id FROM public.market_reports WHERE run_id=%s::uuid)",
            "events": f"""SELECT id::text AS id,run_id::text AS run_id,content_hash,{EVENT_CANONICAL_SQL} AS canonical FROM public.market_events WHERE run_id=%s::uuid""",
            "rankings": f"""SELECT id::text AS id,run_id::text AS run_id,event_id::text AS event_id,content_hash,{RANKING_CANONICAL_SQL} AS canonical FROM public.market_candidate_rankings WHERE run_id=%s::uuid""",
            "evaluation_publications": "SELECT id::text AS id,run_id::text AS run_id,status,phase,market_date::text AS market_date FROM public.market_publications WHERE run_id=%s::uuid",
            "run_outcomes": RECOVERY_SQL["run_terminal_outcomes"] + " WHERE run_id=%s::uuid",
            "origins": "SELECT request_id::text AS request_id,run_id::text AS run_id,requested_packet_id::text AS requested_packet_id,scheduled_phase,market_date::text AS market_date,requested_kind,requested_report_id::text AS requested_report_id,requested_idempotency_key,requested_report_hash FROM public.market_report_request_origins WHERE run_id=%s::uuid",
            "quota": "SELECT q.id::text AS id,q.run_id::text AS run_id,q.provider,q.reserved_requests,COALESCE((SELECT sum(r.request_cost) FROM public.market_source_receipts r WHERE r.reservation_id=q.id),0)::int AS actual_requests FROM public.market_source_quota_reservations q WHERE q.run_id=%s::uuid",
        }
        result = {
            name: self.query(sql, (run_id,) * sql.count("%s"))
            for name, sql in queries.items()
        }
        result["requests"] = self.query("""SELECT request_id::text AS request_id,run_id::text AS run_id,operation,status,response FROM public.market_gateway_requests
            WHERE run_id=%s::uuid OR request_id IN (SELECT request_id FROM public.market_report_request_origins WHERE run_id=%s::uuid)""", (run_id, run_id))
        return result


class GitHubProductionDataSource:
    def __init__(self, repository: str, project_ref: str, database: PostgresReadOnlySource):
        require(bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)), "GitHub repository must be exact owner/name")
        require(bool(re.fullmatch(r"[a-z0-9]{20}", project_ref)), "production project identity is required")
        self.prefix = f"repos/{repository}"
        self.repository = repository
        self.project_ref, self.database = project_ref, database
        self.candidate = None
        self._artifact_cache = {}
        self._artifact_identities = {}

    def _get(self, path: str, *, binary: bool = False):
        require(path.startswith(self.prefix + "/") and ".." not in path and not path.startswith("-"), "unsafe protected record path")
        result = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True, check=False, timeout=60)
        require(result.returncode == 0 and len(result.stdout) <= MAX_PAYLOAD_BYTES, "protected GitHub evidence is unavailable")
        return result.stdout if binary else json.loads(result.stdout)

    def deployment(self, deployment_id: int) -> Mapping:
        require(type(deployment_id) is int and deployment_id > 0, "numeric deployment ID required")
        deployment = self._get(f"{self.prefix}/deployments/{deployment_id}")
        require(deployment.get("environment") == "production" and deployment.get("production_environment") is True,
                "protected production deployment record is required")
        statuses = self._get(f"{self.prefix}/deployments/{deployment_id}/statuses")
        require(statuses and statuses[0].get("state") == "success", "latest production deployment status is not successful")
        self.candidate = deployment["sha"]
        payload = deployment.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as error:
                raise RuntimeError("protected deployment payload is malformed") from error
        require(isinstance(payload, Mapping) and payload.get("candidate_sha") == self.candidate
                and type(payload.get("release_workflow_run_id")) is int
                and payload["release_workflow_run_id"] > 0
                and str(payload.get("release_workflow_run_attempt", "")).isdigit()
                and int(payload["release_workflow_run_attempt"]) > 0,
                "protected deployment payload identity is incomplete")
        match = re.fullmatch(r"release-artifact:([1-9][0-9]*)", str(statuses[0].get("description", "")))
        require(match is not None, "protected deployment status lacks immutable release artifact identity")
        artifact_id = int(match.group(1))
        files = self.artifact(artifact_id)
        require(set(files) == {"release-record.json"}, "release artifact has unexpected files")
        record = json.loads(files["release-record.json"])
        require(record["candidate_sha"] == self.candidate and record["project_ref"] == self.project_ref
                and record["deployment_id"] == deployment["id"]
                and record.get("repository") == self.repository
                and record.get("release_workflow_run_id") == payload["release_workflow_run_id"]
                and record.get("release_workflow_run_attempt") == int(payload["release_workflow_run_attempt"]),
                "protected deployment candidate/project/run mismatch")
        release_identity = self._artifact_identities[artifact_id]
        require(release_identity["name"] == f"release-record-{deployment['id']}"
                and release_identity["workflow_run_id"] == record.get("release_workflow_run_id")
                and release_identity["workflow_run_attempt"] == record.get("release_workflow_run_attempt"),
                "release artifact identity is inconsistent")
        backend = record.get("backend_evidence_artifact")
        require(isinstance(backend, Mapping) and backend.get("artifact_id") != artifact_id,
                "protected backend artifact identity is missing or aliases the release record")
        self.artifact(backend.get("artifact_id"))
        backend_identity = self._artifact_identities[backend["artifact_id"]]
        require({key: backend_identity[key] for key in ("artifact_id", "name", "digest")} == {
            "artifact_id": backend.get("artifact_id"), "name": backend.get("name"), "digest": backend.get("digest")}
            and backend_identity["workflow_run_id"] == record.get("release_workflow_run_id")
            and backend_identity["workflow_run_attempt"] == record.get("release_workflow_run_attempt"),
            "protected backend artifact metadata is inconsistent")
        return {**record, "release_artifact": release_identity,
            "id": deployment["id"], "sha": deployment["sha"], "environment": deployment["environment"],
            "deployed_at": statuses[0]["created_at"]}

    def artifact(self, artifact_id: int, *, active_run_id: int | None = None) -> dict[str, bytes]:
        require(type(artifact_id) is int and artifact_id > 0, "numeric protected artifact ID required")
        if artifact_id in self._artifact_cache:
            return dict(self._artifact_cache[artifact_id])
        metadata = self._get(f"{self.prefix}/actions/artifacts/{artifact_id}")
        run = self._get(f"{self.prefix}/actions/runs/{metadata['workflow_run']['id']}")
        # Only the in-process protected deployment verifier can inspect its own
        # running release. Final independent verification still requires success.
        active = (type(active_run_id) is int and active_run_id > 0
                  and run.get("id") == metadata["workflow_run"]["id"] == active_run_id
                  and run.get("status") == "in_progress" and run.get("conclusion") is None)
        require(metadata.get("id") == artifact_id and not metadata["expired"]
                and isinstance(metadata.get("name"), str) and metadata["name"]
                and re.fullmatch(r"sha256:[0-9a-f]{64}", str(metadata.get("digest", "")))
                and metadata["workflow_run"]["head_sha"] == self.candidate
                and run.get("id") == metadata["workflow_run"]["id"]
                and run.get("repository", {}).get("full_name") == self.repository
                and run.get("head_sha") == self.candidate and run.get("head_branch") == "main"
                and run.get("event") == "workflow_dispatch"
                and run.get("name") == "Protected owner dashboard release"
                and (run.get("conclusion") == "success" or active)
                and run.get("path") == ".github/workflows/owner-dashboard-release.yml"
                and type(run.get("run_attempt")) is int and run["run_attempt"] > 0,
                "artifact did not originate in the protected candidate release workflow")
        raw = self._get(f"{self.prefix}/actions/artifacts/{artifact_id}/zip", binary=True)
        require(metadata["digest"] == "sha256:" + hashlib.sha256(raw).hexdigest(),
                "protected artifact archive digest mismatch")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(members and len(members) <= 1_000
                    and len({member.filename for member in members}) == len(members)
                    and all(path_is_safe(member.filename) and not member.is_dir() and (member.external_attr >> 16) & 0o170000 != 0o120000 for member in members)
                    and sum(member.file_size for member in members) <= MAX_PAYLOAD_BYTES, "protected artifact has unsafe paths or members")
            files = {member.filename: archive.read(member) for member in members}
        self._artifact_identities[artifact_id] = {"artifact_id": artifact_id,
            "name": metadata["name"], "digest": metadata["digest"],
            "workflow_run_id": run["id"], "workflow_run_attempt": run["run_attempt"],
            "repository": self.repository, "workflow_name": run["name"], "workflow_path": run["path"],
            "event": run["event"], "head_branch": run["head_branch"], "head_sha": run["head_sha"]}
        self._artifact_cache[artifact_id] = dict(files)
        return files

    def ci(self, workflow_run_id: int):
        require(type(workflow_run_id) is int and workflow_run_id > 0, "numeric CI run ID required")
        return self._get(f"{self.prefix}/actions/runs/{workflow_run_id}")

    def merge(self, number: int):
        require(type(number) is int and number > 0, "numeric pull request ID required")
        return self._get(f"{self.prefix}/pulls/{number}")

    def reviews(self, number: int):
        rows = self._get(f"{self.prefix}/pulls/{number}/reviews?per_page=100")
        require(len(rows) < 100, "review evidence exceeds bounded page; cannot infer completeness")
        latest = {}
        for row in rows:
            if row["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                reviewer = row.get("user", {}).get("id")
                require(type(reviewer) is int and reviewer > 0, "reviewer identity is unavailable")
                review_id = row.get("id")
                require(type(review_id) is int and review_id > 0, "review identity is unavailable")
                current = latest.get(reviewer)
                row_order = (str(row.get("submitted_at") or ""), review_id)
                current_order = ((str(current.get("submitted_at") or ""), current["id"])
                                 if current is not None else None)
                if current_order is None or row_order > current_order:
                    latest[reviewer] = row
        return list(latest.values())

    def authorization_comments(self, number: int):
        require(type(number) is int and number > 0, "numeric pull request ID required")
        rows = self._get(f"{self.prefix}/issues/{number}/comments?per_page=100")
        require(len(rows) < 100, "owner authorization evidence exceeds bounded page; cannot infer completeness")
        return rows

    def repository_owner_id(self):
        row = self._get(self.prefix)
        owner_id = row.get("owner", {}).get("id")
        require(type(owner_id) is int and owner_id > 0, "repository owner identity is unavailable")
        return owner_id

    def release_rows(self, run_id: str):
        require(self.database.identity()["project_ref"] == self.project_ref, "queried production database identity mismatch")
        return self.database.release_rows(run_id)

    def scheduled_run(self, deployed_at: str) -> str:
        require(self.database.identity()["project_ref"] == self.project_ref, "queried production database identity mismatch")
        rows = self.database.query("""SELECT id::text AS id FROM public.analysis_runs
            WHERE started_at>%s::timestamptz AND scheduled_phase IN ('pre-market','intraday','post-market')
            ORDER BY started_at,id LIMIT 1""", (deployed_at,))
        require(len(rows) == 1, "next existing scheduled production run is unavailable")
        return rows[0]["id"]
