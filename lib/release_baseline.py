"""Exact production baseline omissions allowed before candidate migrations."""
from __future__ import annotations

PRE_MIGRATION_OMISSION_REASON = "candidate migrations have not been applied"

PROTECTED_RELEASE_READ_TABLES = ('holdings',
 'transactions',
 'portfolio_commands',
 'portfolio_command_acknowledgements',
 'analysis_runs',
 'market_policy_config',
 'market_evidence_packets',
 'market_reports',
 'market_report_publications',
 'market_intelligence_runs',
 'market_intelligence_collection_completions',
 'market_intelligence_run_events',
 'market_collection_checkpoints',
 'market_collection_checkpoint_history',
 'market_events',
 'market_candidate_rankings',
 'market_gateway_requests',
 'market_source_items',
 'market_intelligence_run_items',
 'market_source_item_provenance',
 'market_run_source_item_provenance',
 'market_report_request_origins',
 'market_publications',
 'market_source_quota_reservations',
 'market_source_receipts',
 'market_alert_drafts',
 'market_alert_events',
 'market_alert_actions',
 'portfolio_cash_ledger_state',
 'reconciled_cash_snapshots',
 'market_run_terminal_outcomes',
 'decision_evaluations',
 'market_policy_comparisons',
 'market_reference_manifests',
 'market_security_reference_revisions',
 'market_discovery_stage_tasks',
 'market_reference_chunk_receipts',
 'market_reference_snapshot_memberships',
 'market_reference_finalization_seals',
 'market_reference_run_bindings',
 'market_reference_predecessor_pins',
 'market_reference_transfer_requests',
 'market_reference_transfer_responses',
 'market_enrichment_selection_manifests',
 'market_enrichment_request_descriptors',
 'market_theme_episode_revisions',
 'market_exposure_facts',
 'market_research_nominations',
 'market_theme_episode_revisions_v2',
 'market_reviewer_identity_receipts_v2',
 'market_research_nomination_requests_v2',
 'market_research_nominations_v2',
 'market_research_nomination_lifecycle_v2',
 'market_intelligence_memory_context_bindings_v2',
 'stock_agent_release_migration_ledger')

PRE_MIGRATION_UNREADABLE_TABLES = (
    "market_source_items",
    "market_intelligence_run_items",
    "market_source_item_provenance",
    "market_run_source_item_provenance",
)

PRE_MIGRATION_ABSENT_TABLES = (
    "market_reference_manifests",
    "market_security_reference_revisions",
    "market_discovery_stage_tasks",
    "market_reference_chunk_receipts",
    "market_reference_snapshot_memberships",
    "market_reference_finalization_seals",
    "market_reference_run_bindings",
    "market_reference_predecessor_pins",
    "market_reference_transfer_requests",
    "market_reference_transfer_responses",
    "market_enrichment_selection_manifests",
    "market_enrichment_request_descriptors",
    "market_theme_episode_revisions",
    "market_exposure_facts",
    "market_research_nominations",
    "market_theme_episode_revisions_v2",
    "market_reviewer_identity_receipts_v2",
    "market_research_nomination_requests_v2",
    "market_research_nominations_v2",
    "market_research_nomination_lifecycle_v2",
    "market_intelligence_memory_context_bindings_v2",
)


def pre_migration_omissions(*, migrated: bool = False) -> dict[str, object]:
    if migrated:
        return {
            "reason": "candidate read scope is already present",
            "absent_tables": [],
            "unreadable_tables": [],
        }
    return {
        "reason": PRE_MIGRATION_OMISSION_REASON,
        "absent_tables": list(PRE_MIGRATION_ABSENT_TABLES),
        "unreadable_tables": list(PRE_MIGRATION_UNREADABLE_TABLES),
    }


def allowed_pre_migration_omissions() -> tuple[dict[str, object], ...]:
    """Allow only the atomic migration transaction's before/after states."""
    return (pre_migration_omissions(), pre_migration_omissions(migrated=True))


def expected_snapshot_tables(omissions: object) -> tuple[str, ...] | None:
    """Return the exact covered table set for one approved omission receipt."""
    if not any(omissions == allowed for allowed in allowed_pre_migration_omissions()):
        return None
    assert isinstance(omissions, dict)
    omitted = set(omissions["absent_tables"]) | set(omissions["unreadable_tables"])
    return tuple(table for table in PROTECTED_RELEASE_READ_TABLES if table not in omitted)
