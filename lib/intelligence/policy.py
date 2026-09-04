"""Load the reviewed Personal Stock Agent V1 intelligence policy."""

from collections.abc import Mapping
from types import MappingProxyType

from lib.intelligence.types import IntelligencePolicy, PacketLimits


_PROVIDERS = (
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
)
_PHASES = ("pre-market", "intraday", "post-market", "on-demand")
_SEED_DOMAINS = (
    "macro_and_policy",
    "technology_ai_and_semiconductors",
    "energy_nuclear_and_grid_infrastructure",
    "industrial_infrastructure",
    "critical_minerals_and_magnets",
    "healthcare",
    "consumer",
    "defense_trade_and_geopolitics",
    "earnings_and_mergers_and_acquisitions",
)
_MAX_PACKET_LIMITS = PacketLimits()
_INTELLIGENCE_KEYS = frozenset({
    "providers",
    "seed_domains",
    "alpha_vantage_daily_ceiling",
    "alpha_vantage_phase_budget",
    "provider_phase_budgets",
    "packet",
    "paid_fallback_enabled",
    "runtime_model_api_enabled",
    "automatic_policy_changes_enabled",
    "suggestion_only",
    "execution_allowed",
})


def _mapping(parent: Mapping[str, object], key: str) -> Mapping[str, object]:
    try:
        value = parent[key]
    except KeyError as exc:
        raise ValueError(f"missing required intelligence setting: {key}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _required(parent: Mapping[str, object], key: str) -> object:
    try:
        return parent[key]
    except KeyError as exc:
        raise ValueError(f"missing required intelligence setting: {key}") from exc


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _phase_budget(value: object, name: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping) or set(value) != set(_PHASES):
        raise ValueError(f"{name} must contain exactly the approved phases")
    return MappingProxyType({
        phase: _nonnegative_int(value[phase], f"{name}.{phase}")
        for phase in _PHASES
    })


def _packet_limits(value: object) -> PacketLimits:
    if not isinstance(value, Mapping):
        raise ValueError("packet must be an object")
    expected = {
        "max_candidates",
        "max_evidence_per_candidate",
        "max_item_characters",
        "max_serialized_bytes",
    }
    if set(value) != expected:
        raise ValueError("packet must contain exactly the approved limits")
    limits = PacketLimits(**{
        name: _nonnegative_int(value[name], f"packet.{name}")
        for name in expected
    })
    if (
        limits.max_candidates > _MAX_PACKET_LIMITS.max_candidates
        or limits.max_evidence_per_candidate > _MAX_PACKET_LIMITS.max_evidence_per_candidate
        or limits.max_item_characters > _MAX_PACKET_LIMITS.max_item_characters
        or limits.max_serialized_bytes > _MAX_PACKET_LIMITS.max_serialized_bytes
    ):
        raise ValueError("packet limit exceeds the approved maximum")
    return limits


def load_intelligence_policy(settings: Mapping[str, object]) -> IntelligencePolicy:
    """Validate checked-in V1 settings and return an immutable policy view."""
    intelligence = _mapping(settings, "intelligence")
    guardrails = _mapping(settings, "guardrails")
    unexpected = set(intelligence) - _INTELLIGENCE_KEYS
    missing = _INTELLIGENCE_KEYS - set(intelligence)
    if unexpected or missing:
        raise ValueError("intelligence settings must contain exactly the approved keys")

    providers = _required(intelligence, "providers")
    if not isinstance(providers, list) or tuple(providers) != _PROVIDERS:
        raise ValueError("provider allowlist must match the approved V1 providers")
    if _required(intelligence, "seed_domains") != list(_SEED_DOMAINS):
        raise ValueError("seed domains must match the approved V1 taxonomy")

    for key in (
        "paid_fallback_enabled",
        "runtime_model_api_enabled",
        "automatic_policy_changes_enabled",
    ):
        if _required(intelligence, key) is not False:
            raise ValueError(f"{key} must be false")
    if _required(intelligence, "suggestion_only") is not True:
        raise ValueError("suggestion_only must be true")
    if _required(intelligence, "execution_allowed") is not False:
        raise ValueError("execution_allowed must be false")
    if _required(guardrails, "execution_allowed") is not False:
        raise ValueError("execution_allowed must be false")

    daily_ceiling = _nonnegative_int(
        _required(intelligence, "alpha_vantage_daily_ceiling"),
        "alpha_vantage_daily_ceiling",
    )
    if daily_ceiling > 20:
        raise ValueError("alpha vantage daily ceiling must not exceed 20")
    alpha_budget = _phase_budget(
        _required(intelligence, "alpha_vantage_phase_budget"),
        "alpha_vantage phase budget",
    )
    if sum(alpha_budget.values()) > 18:
        raise ValueError("alpha vantage phase budget must not exceed 18")

    raw_provider_budgets = _mapping(intelligence, "provider_phase_budgets")
    expected_budget_providers = set(_PROVIDERS) - {"alpha_vantage"}
    if set(raw_provider_budgets) != expected_budget_providers:
        raise ValueError("provider phase budgets must cover exactly the provider allowlist")
    provider_budgets = MappingProxyType({
        provider: _phase_budget(raw_provider_budgets[provider], f"{provider} phase budget")
        for provider in _PROVIDERS
        if provider != "alpha_vantage"
    })

    return IntelligencePolicy(
        providers=_PROVIDERS,
        alpha_vantage_daily_ceiling=daily_ceiling,
        alpha_vantage_phase_budget=alpha_budget,
        provider_phase_budgets=provider_budgets,
        packet=_packet_limits(_required(intelligence, "packet")),
        suggestion_only=True,
        execution_allowed=False,
    )
