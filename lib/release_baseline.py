"""Exact production baseline omissions allowed before candidate migrations."""
from __future__ import annotations

PRE_MIGRATION_OMISSION_REASON = "candidate migrations have not been applied"

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
            "reason": "candidate migrations are already applied",
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
