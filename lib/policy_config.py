"""Build the versioned, owner-reviewed market decision policy.

The model never supplies this object.  It is projected from checked-in settings,
validated without coercion, and activated explicitly by an owner-run script.
"""

from decimal import Decimal, InvalidOperation
import re

from lib.marketdata import nyse_holidays


_BUCKETS = ("core", "growth", "speculative")
_KNOWN_BROAD_CORE_ETFS = frozenset({"SCHD", "VOO", "VTI", "VXUS"})
_TICKER = re.compile(r"^[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)*$")
_POLICY_KEYS = frozenset({
    "version",
    "allocation_bps",
    "max_position_bps_of_bucket",
    "max_trade_risk_bps",
    "min_reward_risk_milli",
    "max_actionable_quote_age_minutes",
    "alert_near_bps",
    "daily_loss_limit_bps",
    "circuit_breaker_consecutive_losses",
    "speculative_go_live_bucket_micros",
    "monthly_investment_micros",
    "broad_core_etfs",
    "self_tuning_enabled",
    "market_calendar_year",
    "nyse_holidays",
    "request_limits",
    "alerts_v3",
})


def _mapping(parent, key):
    try:
        value = parent[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"missing required setting: {key}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _required(parent, key):
    try:
        return parent[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"missing required setting: {key}") from exc


def _decimal(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError(f"{name} must be a number, not boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _scaled_positive_int(value, scale, name):
    decimal = _decimal(value, name)
    if decimal <= 0:
        raise ValueError(f"{name} must be positive")
    scaled = decimal * scale
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{name} has unsupported precision")
    return int(scaled)


def _positive_int(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer, not boolean")
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be positive integer")
    return value


def _bucket_projection(source, scale, name):
    if not isinstance(source, dict) or set(source) != set(_BUCKETS):
        raise ValueError(f"{name} must contain exactly core, growth, speculative")
    return {
        bucket: _scaled_positive_int(source[bucket], scale, f"{name}.{bucket}")
        for bucket in _BUCKETS
    }


def _validate_etfs(value):
    if not isinstance(value, list) or not value:
        raise ValueError("broad_core_etfs must be a non-empty array")
    if any(not isinstance(symbol, str) or not _TICKER.fullmatch(symbol) for symbol in value):
        raise ValueError("broad_core_etfs must contain canonical ticker symbols")
    if len(value) != len(set(value)):
        raise ValueError("broad_core_etfs must be unique")
    unknown = sorted(set(value) - _KNOWN_BROAD_CORE_ETFS)
    if unknown:
        raise ValueError(f"unknown broad core ETF: {unknown[0]}")
    return sorted(value)


def build_policy_config(settings: dict) -> dict:
    """Project checked-in settings into the exact gateway PolicyConfig contract."""
    strategy = _mapping(settings, "strategy")
    risk = _mapping(settings, "risk")
    capital = _mapping(settings, "capital")
    data = _mapping(settings, "data")
    learning = _mapping(settings, "learning")
    alerts = _mapping(settings, "alerts_v3")
    access = _mapping(settings, "access")
    guardrails = _mapping(settings, "guardrails")

    if access != {"mode": "owner_only", "friend_invitations_enabled": False}:
        raise ValueError("access must remain owner-only with friend invitations disabled")
    if _required(guardrails, "execution_allowed") is not False:
        raise ValueError("execution_allowed must remain false")
    if _required(alerts, "allowed_profiles") != ["long_term", "balanced", "active"]:
        raise ValueError("alerts_v3.allowed_profiles must match the reviewed hybrid profiles")
    if _required(alerts, "max_conditions") != 5:
        raise ValueError("alerts_v3.max_conditions must be 5")
    if _required(alerts, "monitor_interval_minutes") != 15:
        raise ValueError("alerts_v3.monitor_interval_minutes must remain the inactive reviewed option")

    allocation = _bucket_projection(
        _required(strategy, "allocation"), Decimal("10000"), "allocation"
    )
    if sum(allocation.values()) != 10_000:
        raise ValueError("allocation must total 10000 bps")

    require_stop = _required(risk, "always_require_stop_loss")
    if require_stop is not True:
        raise ValueError("always_require_stop_loss must be true")
    self_tuning = _required(learning, "self_tuning_enabled")
    if self_tuning is not False:
        raise ValueError("self_tuning_enabled must be false")

    calendar_year = 2026
    policy = {
        "version": 2,
        "allocation_bps": allocation,
        "max_position_bps_of_bucket": _bucket_projection(
            _required(risk, "max_position_pct_of_bucket"),
            Decimal("100"),
            "max_position_pct_of_bucket",
        ),
        "max_trade_risk_bps": _bucket_projection(
            _required(risk, "max_trade_risk_pct_of_portfolio"),
            Decimal("100"),
            "max_trade_risk_pct_of_portfolio",
        ),
        "min_reward_risk_milli": _scaled_positive_int(
            _required(risk, "min_reward_to_risk"),
            Decimal("1000"),
            "min_reward_to_risk",
        ),
        "max_actionable_quote_age_minutes": _positive_int(
            _required(data, "max_actionable_quote_age_minutes"),
            "max_actionable_quote_age_minutes",
        ),
        "alert_near_bps": _scaled_positive_int(
            _required(risk, "alert_near_pct"), Decimal("100"), "alert_near_pct"
        ),
        "daily_loss_limit_bps": _scaled_positive_int(
            _required(risk, "daily_loss_limit_pct"),
            Decimal("100"),
            "daily_loss_limit_pct",
        ),
        "circuit_breaker_consecutive_losses": _positive_int(
            _required(risk, "circuit_breaker_consecutive_losses"),
            "circuit_breaker_consecutive_losses",
        ),
        "speculative_go_live_bucket_micros": str(_scaled_positive_int(
            _required(capital, "speculative_go_live_when_bucket_usd"),
            Decimal("1000000"),
            "speculative_go_live_when_bucket_usd",
        )),
        "monthly_investment_micros": str(_scaled_positive_int(
            _required(capital, "monthly_investment_usd_current"),
            Decimal("1000000"),
            "monthly_investment_usd_current",
        )),
        "broad_core_etfs": _validate_etfs(_required(risk, "broad_core_etfs")),
        "self_tuning_enabled": False,
        "market_calendar_year": calendar_year,
        "nyse_holidays": list(nyse_holidays(calendar_year)),
        "request_limits": {
            "max_body_bytes": 262_144,
            "max_candidates": {
                "pre-market": 80,
                "intraday": 20,
                "post-market": 80,
                "on-demand": 10,
            },
            "max_requests_per_run": 20,
            "max_authenticated_requests_per_hour": 100,
        },
        "alerts_v3": {
            "enabled": _required(alerts, "enabled"),
            "shadow": _required(alerts, "shadow"),
            "profile": _required(alerts, "default_profile"),
            "draft_ttl_hours": _required(alerts, "draft_ttl_hours"),
            "drafts_per_hour": _required(alerts, "drafts_per_hour"),
        },
    }
    validate_policy_config(policy)
    return policy


def validate_policy_config(policy: dict) -> None:
    """Reject any policy that is not the exact version-1 immutable contract."""
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    unexpected = set(policy) - _POLICY_KEYS
    missing = _POLICY_KEYS - set(policy)
    if unexpected:
        raise ValueError(f"policy has unexpected keys: {sorted(unexpected)}")
    if missing:
        raise ValueError(f"policy is missing keys: {sorted(missing)}")
    if policy["version"] != 2 or isinstance(policy["version"], bool):
        raise ValueError("version must be integer 2")

    for key in (
        "allocation_bps",
        "max_position_bps_of_bucket",
        "max_trade_risk_bps",
    ):
        value = policy[key]
        if not isinstance(value, dict) or set(value) != set(_BUCKETS):
            raise ValueError(f"{key} must contain exact bucket keys")
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in value.values()):
            raise ValueError(f"{key} values must be positive integers")
    if sum(policy["allocation_bps"].values()) != 10_000:
        raise ValueError("allocation must total 10000 bps")

    for key in (
        "min_reward_risk_milli",
        "max_actionable_quote_age_minutes",
        "alert_near_bps",
        "daily_loss_limit_bps",
        "circuit_breaker_consecutive_losses",
        "market_calendar_year",
    ):
        if isinstance(policy[key], bool) or not isinstance(policy[key], int) or policy[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("speculative_go_live_bucket_micros", "monthly_investment_micros"):
        if not isinstance(policy[key], str) or not policy[key].isdigit() or int(policy[key]) <= 0:
            raise ValueError(f"{key} must be a positive integer string")
    if policy["self_tuning_enabled"] is not False:
        raise ValueError("self_tuning_enabled must be false")
    if policy["broad_core_etfs"] != _validate_etfs(policy["broad_core_etfs"]):
        raise ValueError("broad_core_etfs must be sorted")
    if policy["nyse_holidays"] != list(nyse_holidays(policy["market_calendar_year"])):
        raise ValueError("nyse_holidays must match the reviewed static calendar")

    limits = policy["request_limits"]
    expected_limits = {
        "max_body_bytes": 262_144,
        "max_candidates": {
            "pre-market": 80,
            "intraday": 20,
            "post-market": 80,
            "on-demand": 10,
        },
        "max_requests_per_run": 20,
        "max_authenticated_requests_per_hour": 100,
    }
    if limits != expected_limits:
        raise ValueError("request_limits must match reviewed gateway limits")

    alerts = policy["alerts_v3"]
    expected_alert_keys = {
        "enabled", "shadow", "profile", "draft_ttl_hours", "drafts_per_hour"
    }
    if not isinstance(alerts, dict) or set(alerts) != expected_alert_keys:
        raise ValueError("alerts_v3 must contain exact reviewed keys")
    if type(alerts["enabled"]) is not bool:
        raise ValueError("alerts_v3.enabled must be boolean")
    if type(alerts["shadow"]) is not bool:
        raise ValueError("alerts_v3.shadow must be boolean")
    if alerts["enabled"] and alerts["shadow"]:
        raise ValueError("alerts_v3 enabled and shadow cannot both be true")
    if alerts["profile"] not in {"long_term", "balanced", "active"}:
        raise ValueError("alerts_v3.profile must be reviewed")
    if alerts["draft_ttl_hours"] != 24 or isinstance(alerts["draft_ttl_hours"], bool):
        raise ValueError("alerts_v3.draft_ttl_hours must be 24")
    if alerts["drafts_per_hour"] != 5 or isinstance(alerts["drafts_per_hour"], bool):
        raise ValueError("alerts_v3.drafts_per_hour must be 5")
