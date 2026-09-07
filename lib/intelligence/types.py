from dataclasses import dataclass
from typing import Literal, Mapping


QueryKind = Literal[
    "feed",
    "theme_search",
    "issuer_submissions",
    "filing_document",
    "series",
    "screener",
    "quote",
    "universe",
]


@dataclass(frozen=True, slots=True)
class PacketLimits:
    max_candidates: int = 12
    max_evidence_per_candidate: int = 8
    max_item_characters: int = 2_000
    max_serialized_bytes: int = 96 * 1024


@dataclass(frozen=True, slots=True)
class IntelligencePolicy:
    providers: tuple[str, ...]
    seed_domains: tuple[str, ...]
    source_capability_version: int
    theme_taxonomy_version: int
    required_baseline_capability_ids: tuple[str, ...]
    adaptive_enrichment_budget: Mapping[str, int]
    alpha_vantage_daily_ceiling: int
    alpha_vantage_phase_budget: Mapping[str, int]
    provider_phase_budgets: Mapping[str, Mapping[str, int]]
    packet: PacketLimits
    suggestion_only: bool
    execution_allowed: bool

    def budget_for(self, provider: str, phase: str) -> int:
        if provider == "alpha_vantage":
            return int(self.alpha_vantage_phase_budget.get(phase, 0))
        return int(self.provider_phase_budgets.get(provider, {}).get(phase, 0))


@dataclass(frozen=True, slots=True)
class SourceCapability:
    capability_id: str
    provider: str
    query_kind: QueryKind
    themes: frozenset[str]
    phases: frozenset[str]
    allowed_hosts: frozenset[str]
    allowed_path_patterns: tuple[str, ...]
    required_credential: str | None
    authority: str
    retention_class: str
    max_requests_per_run: int
    max_items_per_request: int
    requirement_tier: Literal["required_baseline", "optional"]
    health: Literal[
        "enabled", "configuration_missing", "unsupported", "degraded", "disabled"
    ]
    enabled: bool
    provider_priority: int
    query_pack: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DiscoveryTask:
    task_id: str
    stage: Literal["reference", "signals", "resolve", "enrich", "screen", "quote"]
    provider: str
    capability_id: str
    query_kind: QueryKind
    theme_id: str | None
    query: Mapping[str, object]
    window: Mapping[str, str]
    dependencies: tuple[str, ...]
    max_attempts: int
    requires_credential: bool


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    run_id: str
    phase: str
    reference_version: str
    capability_version: int
    tasks: tuple[DiscoveryTask, ...]
    capabilities: Mapping[str, SourceCapability]
    coverage: Mapping[str, object]
    provider_request_totals: Mapping[str, int]
    reserved_holding_quote_requests: int
    reserved_adaptive_requests: int
