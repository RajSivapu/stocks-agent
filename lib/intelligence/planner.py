"""Versioned source capabilities and deterministic bounded discovery plans."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re
from types import MappingProxyType
from uuid import UUID, uuid5

from lib.intelligence.types import (
    DiscoveryPlan,
    DiscoveryTask,
    IntelligencePolicy,
    SourceCapability,
)


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SOURCE_CONFIG = _ROOT / "config" / "intelligence_sources.json"
_QUERY_KINDS = frozenset({
    "feed",
    "theme_search",
    "issuer_submissions",
    "filing_document",
    "series",
    "screener",
    "quote",
    "universe",
})
_PHASES = frozenset({"pre-market", "intraday", "post-market", "on-demand"})
_PROVIDERS = frozenset({
    "gdelt",
    "alpha_vantage",
    "finnhub",
    "yahoo",
    "sec_edgar",
    "federal_register",
    "white_house",
    "doe",
    "dod",
    "eia",
    "fred",
    "bls",
    "bea",
})
_APPROVED_HOSTS = frozenset({
    "api.bls.gov",
    "api.eia.gov",
    "api.gdeltproject.org",
    "api.stlouisfed.org",
    "apps.bea.gov",
    "data.sec.gov",
    "finnhub.io",
    "query1.finance.yahoo.com",
    "query2.finance.yahoo.com",
    "www.alphavantage.co",
    "www.defense.gov",
    "www.eia.gov",
    "www.energy.gov",
    "www.federalregister.gov",
    "www.sec.gov",
    "www.whitehouse.gov",
    "www.war.gov",
})
_SEED_THEMES = frozenset({
    "macro_and_policy",
    "technology_ai_and_semiconductors",
    "energy_nuclear_and_grid_infrastructure",
    "industrial_infrastructure",
    "critical_minerals_and_magnets",
    "healthcare",
    "consumer",
    "defense_trade_and_geopolitics",
    "earnings_and_mergers_and_acquisitions",
})
_REQUIRED_BASELINE_IDS = (
    "sec_company_tickers_universe",
    "gdelt_theme_search",
)
_HEALTH_STATES = frozenset({
    "enabled", "configuration_missing", "unsupported", "degraded", "disabled"
})
_REQUIREMENT_TIERS = frozenset({"required_baseline", "optional"})
_FIXED_ALPHA_TOPICS = frozenset({
    "blockchain",
    "earnings",
    "economy_fiscal",
    "economy_macro",
    "economy_monetary",
    "energy_transportation",
    "finance",
    "financial_markets",
    "ipo",
    "life_sciences",
    "manufacturing",
    "mergers_and_acquisitions",
    "real_estate",
    "retail_wholesale",
    "technology",
})
_CAPABILITY_FIELDS = frozenset({
    "capability_id",
    "provider",
    "query_kind",
    "themes",
    "phases",
    "allowed_hosts",
    "allowed_path_patterns",
    "required_credential",
    "authority",
    "retention_class",
    "max_requests_per_run",
    "max_items_per_request",
    "requirement_tier",
    "health",
    "enabled",
    "provider_priority",
    "query_pack",
})
_IDENTIFIER_KINDS = frozenset({"issuer_submissions", "filing_document", "quote"})
_MAX_RUN_REQUESTS = 100


class _FrozenList(tuple):
    """Tuple storage with value equality against ordinary JSON-style sequences."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return False

    __hash__ = None


class _CapabilityRegistry(Mapping[str, SourceCapability]):
    def __init__(
        self,
        values: Mapping[str, SourceCapability],
        *,
        version: int,
    ) -> None:
        self._values = MappingProxyType(dict(values))
        self.version = version

    def __getitem__(self, key: str) -> SourceCapability:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def _bounded_integer(value: object, name: str, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _bounded_string(value: object, name: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _string_sequence(
    value: object,
    name: str,
    *,
    allow_empty: bool = False,
    maximum: int = 100,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum or (not value and not allow_empty):
        raise ValueError(f"{name} must be a bounded string list")
    if any(not isinstance(entry, str) or not entry or len(entry) > 200 for entry in value):
        raise ValueError(f"{name} must be a bounded string list")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(value)


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_deep_freeze(item) for item in value)
    return value


def _query_leaves(
    query_pack: Mapping[str, object],
    *,
    query_kind: str,
    themes: frozenset[str],
) -> tuple[Mapping[str, object], ...]:
    if query_kind == "theme_search":
        if (
            not set(query_pack) <= {"themes", "targets"}
            or "themes" not in query_pack
            or not isinstance(query_pack["themes"], Mapping)
        ):
            raise ValueError("theme-search query pack must contain theme queries")
        theme_queries = query_pack["themes"]
        if set(theme_queries) != set(themes):
            raise ValueError("theme-search query pack must cover exactly its supported themes")
        targets = query_pack.get("targets", {})
        if not isinstance(targets, Mapping):
            raise ValueError("theme-search target queries must be an object")
        leaves = tuple(theme_queries[theme] for theme in sorted(theme_queries)) + tuple(
            targets[target] for target in sorted(targets)
        )
    else:
        if set(query_pack) != {"default"}:
            raise ValueError("capability query pack must contain one default query")
        leaves = (query_pack["default"],)
    if any(not isinstance(leaf, Mapping) or not leaf for leaf in leaves):
        raise ValueError("capability query pack contains an invalid query")
    return leaves  # type: ignore[return-value]


def _validate_query_pack(
    capability_id: str,
    provider: str,
    query_kind: str,
    themes: frozenset[str],
    allowed_hosts: frozenset[str],
    path_patterns: tuple[str, ...],
    value: object,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{capability_id} query pack must be an object")
    leaves = _query_leaves(value, query_kind=query_kind, themes=themes)
    for query in leaves:
        host = query.get("host")
        path = query.get("path")
        if host not in allowed_hosts:
            raise ValueError(f"{capability_id} configured host is not allowed")
        if not isinstance(path, str) or not any(
            fnmatchcase(path, pattern) for pattern in path_patterns
        ):
            raise ValueError(f"{capability_id} configured path is not allowed")
        if any(
            not isinstance(key, str)
            or len(key) > 80
            or isinstance(item, str) and len(item) > 500
            for key, item in query.items()
        ):
            raise ValueError(f"{capability_id} query pack exceeds bounds")
        if query_kind in _IDENTIFIER_KINDS and query.get("identifier_required") not in {
            "cik", "security"
        }:
            raise ValueError(f"{capability_id} requires an approved identifier")
        if query_kind == "series" and not (
            query.get("identifier_required") == "series"
            or isinstance(query.get("series_id"), str)
            or isinstance(query.get("series_ids"), list)
        ):
            raise ValueError(f"{capability_id} requires configured series identifiers")
        if provider == "alpha_vantage":
            topics = query.get("topics")
            if not isinstance(topics, str) or not topics:
                raise ValueError("alpha vantage requires documented fixed topics")
            if any(topic not in _FIXED_ALPHA_TOPICS for topic in topics.split(",")):
                raise ValueError("alpha vantage requires documented fixed topics")
    return _deep_freeze(value)  # type: ignore[return-value]


def load_source_capabilities(
    path: str | Path | None = None,
) -> Mapping[str, SourceCapability]:
    """Load and strictly validate the reviewed zero-cost capability registry."""
    source_path = Path(path) if path is not None else _DEFAULT_SOURCE_CONFIG
    try:
        document = json.loads(source_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("source capability registry is unreadable") from exc
    if not isinstance(document, Mapping) or set(document) != {
        "version",
        "theme_taxonomy_version",
        "paid_fallback_enabled",
        "required_baseline_capability_ids",
        "capabilities",
    }:
        raise ValueError("source capability registry must contain exactly the reviewed keys")
    if document["version"] != 1 or document["theme_taxonomy_version"] != 1:
        raise ValueError("source capability registry must use the reviewed version")
    if document["paid_fallback_enabled"] is not False:
        raise ValueError("paid fallback must be disabled")
    baseline_ids = _string_sequence(
        document["required_baseline_capability_ids"],
        "required baseline capability IDs",
    )
    if baseline_ids != _REQUIRED_BASELINE_IDS:
        raise ValueError("required baseline capability IDs must match the reviewed baseline")
    raw_capabilities = document["capabilities"]
    if not isinstance(raw_capabilities, list) or not 1 <= len(raw_capabilities) <= 100:
        raise ValueError("capabilities must be a bounded list")

    capabilities: dict[str, SourceCapability] = {}
    query_packs: dict[str, Mapping[str, object]] = {}
    for raw in raw_capabilities:
        if not isinstance(raw, Mapping) or set(raw) != _CAPABILITY_FIELDS:
            raise ValueError("capability must contain exactly the reviewed fields")
        capability_id = _bounded_string(raw["capability_id"], "capability ID")
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", capability_id):
            raise ValueError("capability ID must use stable snake case")
        if capability_id in capabilities:
            raise ValueError("duplicate capability ID")
        provider = _bounded_string(raw["provider"], f"{capability_id} provider")
        if provider not in _PROVIDERS:
            raise ValueError(f"{capability_id} uses an unapproved provider")
        query_kind = raw["query_kind"]
        if query_kind not in _QUERY_KINDS:
            raise ValueError(f"{capability_id} uses an unknown query kind")
        themes = frozenset(_string_sequence(
            raw["themes"], f"{capability_id} themes", allow_empty=True
        ))
        if not themes <= _SEED_THEMES:
            raise ValueError(f"{capability_id} uses an unknown theme")
        phases = frozenset(_string_sequence(raw["phases"], f"{capability_id} phases"))
        if not phases <= _PHASES:
            raise ValueError(f"{capability_id} uses an unknown phase")
        allowed_hosts = frozenset(_string_sequence(
            raw["allowed_hosts"], f"{capability_id} allowed hosts"
        ))
        if not allowed_hosts <= _APPROVED_HOSTS:
            raise ValueError(f"{capability_id} uses an unapproved host")
        path_patterns = _string_sequence(
            raw["allowed_path_patterns"], f"{capability_id} path patterns"
        )
        if any(not pattern.startswith("/") or ".." in pattern for pattern in path_patterns):
            raise ValueError(f"{capability_id} has an invalid path pattern")
        credential = raw["required_credential"]
        if credential is not None and (
            not isinstance(credential, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", credential)
        ):
            raise ValueError(f"{capability_id} has an invalid credential name")
        tier = raw["requirement_tier"]
        if tier not in _REQUIREMENT_TIERS:
            raise ValueError(f"{capability_id} has an invalid requirement tier")
        health = raw["health"]
        if health not in _HEALTH_STATES:
            raise ValueError(f"{capability_id} has an invalid health")
        enabled = raw["enabled"]
        if not isinstance(enabled, bool):
            raise ValueError(f"{capability_id} enabled must be boolean")
        if enabled and health not in {"enabled", "degraded"}:
            raise ValueError(f"{capability_id} enabled state conflicts with health")
        priority = _bounded_integer(
            raw["provider_priority"],
            f"{capability_id} provider priority",
            minimum=0,
            maximum=100,
        )
        query_packs[capability_id] = _validate_query_pack(
            capability_id,
            provider,
            query_kind,
            themes,
            allowed_hosts,
            path_patterns,
            raw["query_pack"],
        )
        capabilities[capability_id] = SourceCapability(
            capability_id=capability_id,
            provider=provider,
            query_kind=query_kind,
            themes=themes,
            phases=phases,
            allowed_hosts=allowed_hosts,
            allowed_path_patterns=path_patterns,
            required_credential=credential,
            authority=_bounded_string(raw["authority"], f"{capability_id} authority"),
            retention_class=_bounded_string(
                raw["retention_class"], f"{capability_id} retention class"
            ),
            max_requests_per_run=_bounded_integer(
                raw["max_requests_per_run"],
                f"{capability_id} request limit",
                minimum=1,
                maximum=100,
            ),
            max_items_per_request=_bounded_integer(
                raw["max_items_per_request"],
                f"{capability_id} item limit",
                minimum=1,
                maximum=15000,
            ),
            requirement_tier=tier,
            health=health,
            enabled=enabled,
            provider_priority=priority,
            query_pack=query_packs[capability_id],
        )

    for baseline_id in baseline_ids:
        baseline = capabilities.get(baseline_id)
        if (
            baseline is None
            or baseline.requirement_tier != "required_baseline"
            or not baseline.enabled
            or baseline.health != "enabled"
        ):
            raise ValueError(f"required baseline capability {baseline_id} is missing or disabled")
        if baseline.required_credential is not None:
            raise ValueError(f"required baseline capability {baseline_id} must be zero-key")
    unlisted_required = {
        item.capability_id
        for item in capabilities.values()
        if item.requirement_tier == "required_baseline"
    } - set(baseline_ids)
    if unlisted_required:
        raise ValueError("required baseline capability is not listed in the reviewed baseline")
    return _CapabilityRegistry(
        capabilities,
        version=1,
    )


def _validate_window(window: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(window, Mapping) or set(window) != {"start", "end"}:
        raise ValueError("requested window must contain start and end")
    parsed: dict[str, datetime] = {}
    for key in ("start", "end"):
        value = window[key]
        if not isinstance(value, str) or len(value) > 80:
            raise ValueError("requested window timestamps must be bounded strings")
        try:
            parsed[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("requested window timestamps must be ISO-8601") from exc
        if parsed[key].tzinfo is None:
            raise ValueError("requested window timestamps must include an offset")
    if parsed["start"] > parsed["end"]:
        raise ValueError("requested window start must not follow end")
    return MappingProxyType({key: window[key] for key in sorted(window)})


def _scan_value(
    scans: Mapping[tuple[str, str], str], capability: SourceCapability, theme_id: str
) -> str:
    return scans.get(
        (capability.capability_id, theme_id),
        scans.get((capability.provider, theme_id), ""),
    )


def _query_for(
    capability: SourceCapability, theme_id: str | None
) -> Mapping[str, object] | None:
    pack = capability.query_pack
    if capability.query_kind == "theme_search":
        themes = pack.get("themes")
        value = themes.get(theme_id) if isinstance(themes, Mapping) else None
    else:
        value = pack.get("default")
    if not isinstance(value, Mapping):
        return None
    if value.get("identifier_required") in {"cik", "security", "series"}:
        return None
    return _deep_freeze(value)  # type: ignore[return-value]


def configured_provider_query(provider: str, target: str) -> str:
    """Resolve a legacy collector target through the reviewed capability query packs."""
    target_to_theme = {
        "macro_policy": "macro_and_policy",
        "technology_ai_semiconductors": "technology_ai_and_semiconductors",
        "energy_nuclear_grid": "energy_nuclear_and_grid_infrastructure",
        "industrial_infrastructure": "industrial_infrastructure",
        "critical_minerals_magnets": "critical_minerals_and_magnets",
        "healthcare": "healthcare",
        "consumer": "consumer",
        "defense_trade_geopolitics": "defense_trade_and_geopolitics",
        "earnings_ma": "earnings_and_mergers_and_acquisitions",
    }
    theme_id = target_to_theme.get(target)
    registry = load_source_capabilities()
    for capability in registry.values():
        if capability.provider != provider or capability.query_kind != "theme_search":
            continue
        pack = capability.query_pack
        values = pack.get("themes") if theme_id is not None else pack.get("targets")
        query = values.get(theme_id or target) if isinstance(values, Mapping) else None
        if not isinstance(query, Mapping):
            continue
        text = query.get("query", query.get("term", query.get("topics")))
        if isinstance(text, str) and text.strip():
            return text.strip()
    raise ValueError("unsupported provider target query")


def _task_id(
    run_id: str,
    *,
    stage: str,
    capability: SourceCapability,
    theme_id: str | None,
    query: Mapping[str, object],
    window: Mapping[str, str],
    dependencies: tuple[str, ...],
    reference_version: str,
) -> str:
    canonical = json.dumps(
        {
            "capability_id": capability.capability_id,
            "dependencies": dependencies,
            "provider": capability.provider,
            "query": dict(query),
            "query_kind": capability.query_kind,
            "reference_version": reference_version,
            "stage": stage,
            "theme_id": theme_id,
            "window": dict(window),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return str(uuid5(UUID(run_id), canonical))


def build_discovery_plan(
    policy: IntelligencePolicy,
    capabilities: Mapping[str, SourceCapability],
    *,
    phase: str,
    run_id: str,
    reference_version: str,
    requested_window: Mapping[str, str],
    available_credentials: frozenset[str],
    required_holding_quote_requests: int,
    last_completed_scans: Mapping[tuple[str, str], str],
) -> DiscoveryPlan:
    """Return a stable bounded plan; unsupported work is coverage, never a guessed request."""
    if phase not in _PHASES:
        raise ValueError("phase is not approved")
    try:
        parsed_run_id = UUID(run_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("run ID must be a UUID") from exc
    if parsed_run_id.version != 4:
        raise ValueError("run ID must be a version-4 UUID")
    _bounded_string(reference_version, "reference version")
    window = _validate_window(requested_window)
    holding_reserve = _bounded_integer(
        required_holding_quote_requests,
        "required holding quote requests",
        minimum=0,
        maximum=_MAX_RUN_REQUESTS,
    )
    adaptive_reserve = int(policy.adaptive_enrichment_budget.get(phase, 0))
    if holding_reserve + adaptive_reserve > _MAX_RUN_REQUESTS:
        raise ValueError("reserved requests exceed the run request ceiling")
    if not isinstance(available_credentials, frozenset) or any(
        not isinstance(name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", name)
        for name in available_credentials
    ):
        raise ValueError("available credentials must contain names only")
    if not isinstance(last_completed_scans, Mapping) or any(
        not isinstance(key, tuple)
        or len(key) != 2
        or any(not isinstance(part, str) or not part for part in key)
        or not isinstance(value, str)
        or len(value) > 80
        for key, value in last_completed_scans.items()
    ):
        raise ValueError("last completed scans must map capability-theme pairs to timestamps")
    if set(capabilities) != {capability.capability_id for capability in capabilities.values()}:
        raise ValueError("capability registry must be keyed by capability ID")
    registry_version = getattr(capabilities, "version", policy.source_capability_version)
    if registry_version != policy.source_capability_version:
        raise ValueError("capability registry version does not match policy")
    for baseline_id in policy.required_baseline_capability_ids:
        capability = capabilities.get(baseline_id)
        if (
            capability is None
            or capability.requirement_tier != "required_baseline"
            or not capability.enabled
            or capability.health != "enabled"
        ):
            raise ValueError(f"required baseline capability {baseline_id} is missing or disabled")
        if capability.required_credential is not None:
            raise ValueError(f"required baseline capability {baseline_id} must be zero-key")

    task_capacity = _MAX_RUN_REQUESTS
    tasks: list[DiscoveryTask] = []
    provider_totals: dict[str, int] = {}
    capability_totals: dict[str, int] = {}
    unsupported_pairs: list[Mapping[str, str]] = []
    missing_credentials: set[str] = set()
    deferred_capabilities: set[str] = set()

    def usable(capability: SourceCapability) -> bool:
        if not capability.enabled or capability.health not in {"enabled", "degraded"}:
            deferred_capabilities.add(capability.capability_id)
            return False
        if phase not in capability.phases:
            return False
        if (
            capability.required_credential is not None
            and capability.required_credential not in available_credentials
        ):
            missing_credentials.add(capability.capability_id)
            return False
        return True

    def add_task(capability: SourceCapability, theme_id: str | None) -> bool:
        query = _query_for(capability, theme_id)
        if query is None:
            deferred_capabilities.add(capability.capability_id)
            return False
        provider_total = provider_totals.get(capability.provider, 0)
        capability_total = capability_totals.get(capability.capability_id, 0)
        provider_budget = policy.budget_for(capability.provider, phase)
        if capability.provider == "yahoo":
            provider_budget = max(0, provider_budget - holding_reserve)
        if (
            len(tasks) >= task_capacity
            or provider_total >= provider_budget
            or capability_total >= capability.max_requests_per_run
        ):
            deferred_capabilities.add(capability.capability_id)
            return False
        stage = {
            "universe": "reference",
            "screener": "screen",
            "quote": "quote",
            "issuer_submissions": "enrich",
            "filing_document": "enrich",
        }.get(capability.query_kind, "signals")
        dependencies: tuple[str, ...] = ()
        task = DiscoveryTask(
            task_id=_task_id(
                run_id,
                stage=stage,
                capability=capability,
                theme_id=theme_id,
                query=query,
                window=window,
                dependencies=dependencies,
                reference_version=reference_version,
            ),
            stage=stage,
            provider=capability.provider,
            capability_id=capability.capability_id,
            query_kind=capability.query_kind,
            theme_id=theme_id,
            query=query,
            window=window,
            dependencies=dependencies,
            max_attempts=1,
            requires_credential=capability.required_credential is not None,
        )
        tasks.append(task)
        provider_totals[capability.provider] = provider_total + 1
        capability_totals[capability.capability_id] = capability_total + 1
        return True

    ordered_capabilities = sorted(
        capabilities.values(),
        key=lambda item: (item.provider_priority, item.provider, item.capability_id),
    )

    # Required non-theme capabilities establish reference identity before discovery.
    for baseline_id in policy.required_baseline_capability_ids:
        capability = capabilities[baseline_id]
        if capability.query_kind != "theme_search" and usable(capability):
            add_task(capability, None)

    # First pass: one keyless, capability-matched opportunity for every theme.
    first_opportunities: list[tuple[str, str, int, str, str, SourceCapability]] = []
    themes_with_opportunities: set[str] = set()
    for theme_id in policy.seed_domains:
        for capability in ordered_capabilities:
            if (
                capability.query_kind != "theme_search"
                or capability.required_credential is not None
                or theme_id not in capability.themes
                or not usable(capability)
                or _query_for(capability, theme_id) is None
            ):
                continue
            themes_with_opportunities.add(theme_id)
            first_opportunities.append((
                _scan_value(last_completed_scans, capability, theme_id),
                theme_id,
                capability.provider_priority,
                capability.provider,
                capability.capability_id,
                capability,
            ))
        if theme_id not in themes_with_opportunities:
            unsupported_pairs.append(MappingProxyType({
                "theme_id": theme_id,
                "reason": "no_executable_zero_key_capability",
            }))
    planned_first_pass_themes: set[str] = set()
    for _scan, theme_id, _priority, _provider, _capability_id, capability in sorted(
        first_opportunities
    ):
        if theme_id not in planned_first_pass_themes and add_task(capability, theme_id):
            planned_first_pass_themes.add(theme_id)

    quota_blocked_themes = themes_with_opportunities - planned_first_pass_themes
    if quota_blocked_themes:
        raise ValueError("provider budgets cannot fit required discovery")

    for baseline_id in policy.required_baseline_capability_ids:
        capability = capabilities[baseline_id]
        if any(task.capability_id == baseline_id for task in tasks):
            continue
        baseline_themes = sorted(set(policy.seed_domains) & capability.themes)
        if not baseline_themes or not add_task(capability, baseline_themes[0]):
            raise ValueError("provider budgets cannot fit required discovery")

    if len(tasks) + holding_reserve + adaptive_reserve > _MAX_RUN_REQUESTS:
        raise ValueError("reservations cannot fit required discovery")
    task_capacity = _MAX_RUN_REQUESTS - holding_reserve - adaptive_reserve

    # Static zero-key signals are bounded once per capability.
    for capability in ordered_capabilities:
        if (
            capability.requirement_tier == "required_baseline"
            or capability.required_credential is not None
            or capability.query_kind not in {"feed", "series"}
            or not usable(capability)
        ):
            continue
        add_task(capability, None)

    # Repeat theme coverage remains fair and uses only supported pairs.
    for credentialed in (False, True):
        repeats: list[tuple[str, str, int, str, str, SourceCapability]] = []
        for capability in ordered_capabilities:
            if (
                capability.query_kind != "theme_search"
                or (capability.required_credential is not None) != credentialed
                or capability.requirement_tier == "required_baseline"
                or not usable(capability)
            ):
                continue
            for theme_id in capability.themes:
                if _query_for(capability, theme_id) is not None:
                    repeats.append((
                        _scan_value(last_completed_scans, capability, theme_id),
                        theme_id,
                        capability.provider_priority,
                        capability.provider,
                        capability.capability_id,
                        capability,
                    ))
        for _scan, theme_id, _priority, _provider, _capability_id, capability in sorted(
            repeats
        ):
            add_task(capability, theme_id)

    # Broad screens are last so holding quotes and adaptive work stay pre-reserved.
    for capability in ordered_capabilities:
        if capability.query_kind == "screener" and usable(capability):
            add_task(capability, None)

    # Record credentials and disabled health even for capabilities that need later-stage inputs.
    for capability in capabilities.values():
        usable(capability)

    frozen_coverage = MappingProxyType({
        "unsupported_pairs": _FrozenList(unsupported_pairs),
        "missing_credentials": _FrozenList(sorted(missing_credentials)),
        "deferred_capability_ids": _FrozenList(sorted(deferred_capabilities)),
        "planned_theme_ids": _FrozenList(sorted({
            task.theme_id for task in tasks if task.theme_id is not None
        })),
        "unplanned_theme_ids": _FrozenList(sorted(
            set(policy.seed_domains)
            - {task.theme_id for task in tasks if task.theme_id is not None}
        )),
    })
    frozen_capabilities: Mapping[str, SourceCapability]
    if isinstance(capabilities, _CapabilityRegistry):
        frozen_capabilities = capabilities
    else:
        frozen_capabilities = MappingProxyType(dict(capabilities))
    return DiscoveryPlan(
        run_id=run_id,
        phase=phase,
        reference_version=reference_version,
        capability_version=policy.source_capability_version,
        tasks=tuple(tasks),
        capabilities=frozen_capabilities,
        coverage=frozen_coverage,
        provider_request_totals=MappingProxyType(dict(sorted(provider_totals.items()))),
        reserved_holding_quote_requests=holding_reserve,
        reserved_adaptive_requests=adaptive_reserve,
    )
