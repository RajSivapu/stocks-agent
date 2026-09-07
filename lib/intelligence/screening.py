"""Offline, bounded screen semantics and explicit feasibility coverage.

Configured V1 screens are discovery aids only.  This module deliberately has no
network client: an inactive definition produces a zero-transport coverage receipt,
and a future reviewed collector must pass a bounded payload in explicitly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlsplit

from lib import config
from lib.intelligence.providers import SourceItem
from lib.intelligence.universe import ReferenceSnapshot, SecurityIdentity


ScreenState = Literal[
    "enabled",
    "unavailable",
    "disabled",
    "degraded",
    "unsupported",
]
ScreenOutcome = Literal[
    "parsed_empty",
    "parsed_nonempty",
    "partial",
    "malformed",
    "overbound",
    "unavailable",
    "disabled",
    "degraded",
    "unsupported",
]

_INACTIVE_STATES = frozenset({"unavailable", "disabled", "degraded", "unsupported"})
_OUTCOMES = frozenset({
    "parsed_empty", "parsed_nonempty", "partial", "malformed", "overbound",
    *_INACTIVE_STATES,
})
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_CIK = re.compile(r"[0-9]{10}")
_MAX_SCREEN_BYTES = 500_000


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("screen timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value: object, *, allow_none: bool = True) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool):
        raise ValueError("screen numeric value is invalid")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("screen numeric value is invalid") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("screen numeric value is invalid")
    return parsed


def _positive_integer_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("screen integer value is invalid")
    return value


def _safe_https(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 2_048:
        raise ValueError("screen source URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("screen source URL is invalid") from None
    if (
        parsed.scheme != "https" or not parsed.hostname or port not in (None, 443)
        or parsed.username is not None or parsed.password is not None
    ):
        raise ValueError("screen source URL is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ScreenDefinition:
    screen_id: str
    capability_id: str
    provider: str
    state: ScreenState
    reasons: tuple[str, ...]
    reviewed_at: date
    endpoint_version: str
    parser_version: str
    retention_class: str
    max_rows: int = 50
    execution_allowed: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.screen_id):
            raise ValueError("screen ID is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.capability_id):
            raise ValueError("screen capability ID is invalid")
        if self.state not in {"enabled", *_INACTIVE_STATES}:
            raise ValueError("screen state is invalid")
        if not self.provider or len(self.provider) > 80:
            raise ValueError("screen provider is invalid")
        if self.state != "enabled" and not self.reasons:
            raise ValueError("inactive screen must retain reasons")
        if (
            not isinstance(self.reasons, tuple)
            or len(self.reasons) > 12
            or len(set(self.reasons)) != len(self.reasons)
            or any(not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", value) for value in self.reasons)
        ):
            raise ValueError("screen reasons are invalid")
        if not isinstance(self.reviewed_at, date) or isinstance(self.reviewed_at, datetime):
            raise ValueError("screen review date is invalid")
        if any(
            not isinstance(value, str) or not value or len(value) > 120
            for value in (self.endpoint_version, self.parser_version, self.retention_class)
        ):
            raise ValueError("screen version or retention contract is invalid")
        if isinstance(self.max_rows, bool) or not isinstance(self.max_rows, int) \
                or not 1 <= self.max_rows <= 100:
            raise ValueError("screen row bound is invalid")
        if self.execution_allowed is not False:
            raise ValueError("screen definitions cannot authorize execution")


@dataclass(frozen=True, slots=True)
class ScreenObservation:
    screen_id: str
    symbol: str
    sector: str | None
    rank: int
    source_url: str
    observed_at: datetime
    issuer_cik: str | None = None
    displayed_price: Decimal | None = None
    displayed_average_volume: int | None = None
    displayed_market_cap: int | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.screen_id):
            raise ValueError("screen observation ID is invalid")
        if _SYMBOL.fullmatch(self.symbol) is None:
            raise ValueError("screen symbol is invalid")
        if self.sector is not None and (
            not isinstance(self.sector, str) or not self.sector.strip() or len(self.sector) > 80
        ):
            raise ValueError("screen sector is invalid")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or not 1 <= self.rank <= 100:
            raise ValueError("screen rank is invalid")
        _safe_https(self.source_url)
        _utc(self.observed_at)
        if self.issuer_cik is not None and _CIK.fullmatch(self.issuer_cik) is None:
            raise ValueError("screen issuer CIK is invalid")
        for value in (self.displayed_price,):
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite() or value < 0):
                raise ValueError("screen displayed price is invalid")
        for value in (self.displayed_average_volume, self.displayed_market_cap):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError("screen displayed market value is invalid")


@dataclass(frozen=True, slots=True)
class ScreenPayload:
    screen_id: str
    state: Literal["parsed_empty", "parsed_nonempty", "partial", "malformed", "overbound"]
    rows: tuple[ScreenObservation, ...]
    reasons: tuple[str, ...]

    @classmethod
    def from_json(
        cls,
        definition: ScreenDefinition,
        raw: bytes,
        retrieved_at: datetime,
    ) -> "ScreenPayload":
        _utc(retrieved_at)
        if not isinstance(raw, bytes) or len(raw) > _MAX_SCREEN_BYTES:
            return cls(definition.screen_id, "overbound", (), ("payload_byte_bound_exceeded",))
        if not raw:
            return cls(definition.screen_id, "malformed", (), ("payload_contract_invalid",))
        try:
            document = json.loads(
                raw,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
            if not isinstance(document, Mapping) or set(document) != {
                "schema_version", "screen_id", "as_of", "truncated", "rows"
            }:
                raise ValueError
            if document["schema_version"] != 1 or document["screen_id"] != definition.screen_id \
                    or not isinstance(document["truncated"], bool):
                raise ValueError
            as_of = datetime.fromisoformat(str(document["as_of"]).replace("Z", "+00:00"))
            as_of = _utc(as_of)
            raw_rows = document["rows"]
            if not isinstance(raw_rows, list):
                raise ValueError
            if len(raw_rows) > definition.max_rows:
                return cls(definition.screen_id, "overbound", (), ("payload_row_bound_exceeded",))
            rows: list[ScreenObservation] = []
            for index, value in enumerate(raw_rows):
                if not isinstance(value, Mapping) or set(value) != {
                    "symbol", "sector", "rank", "source_url", "displayed_price",
                    "displayed_average_volume", "displayed_market_cap",
                }:
                    raise ValueError
                symbol = str(value["symbol"]).strip().upper()
                sector = value["sector"]
                rows.append(ScreenObservation(
                    screen_id=definition.screen_id,
                    symbol=symbol,
                    sector=None if sector is None else str(sector).strip(),
                    rank=value["rank"],
                    source_url=_safe_https(value["source_url"]),
                    observed_at=as_of,
                    displayed_price=_decimal(value["displayed_price"]),
                    displayed_average_volume=_positive_integer_or_none(
                        value["displayed_average_volume"]
                    ),
                    displayed_market_cap=_positive_integer_or_none(value["displayed_market_cap"]),
                ))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return cls(definition.screen_id, "malformed", (), ("payload_contract_invalid",))
        state = "partial" if document["truncated"] else \
            "parsed_nonempty" if rows else "parsed_empty"
        reasons = ("payload_truncated",) if state == "partial" else ()
        return cls(definition.screen_id, state, tuple(rows), reasons)


@dataclass(frozen=True, slots=True)
class GatewayMarketInputs:
    price: Decimal | None
    average_volume: int | None
    market_cap: int | None

    def __post_init__(self) -> None:
        if self.price is not None and (
            not isinstance(self.price, Decimal) or not self.price.is_finite() or self.price <= 0
        ):
            raise ValueError("gateway price is invalid")
        for value, label in (
            (self.average_volume, "average volume"), (self.market_cap, "market cap")
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"gateway {label} is invalid")


@dataclass(frozen=True, slots=True)
class MarketFilterPolicy:
    min_price: Decimal
    min_average_volume: int
    min_market_cap: int
    max_candidates: int = 10
    execution_allowed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.min_price, Decimal) or not self.min_price.is_finite() \
                or self.min_price <= 0:
            raise ValueError("minimum price is invalid")
        for value, label in (
            (self.min_average_volume, "average volume"),
            (self.min_market_cap, "market cap"),
            (self.max_candidates, "candidate limit"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"minimum {label} is invalid")
        if self.max_candidates > 10:
            raise ValueError("screen candidate limit exceeds V1 bound")
        if self.execution_allowed is not False:
            raise ValueError("screen filters cannot authorize execution")


@dataclass(frozen=True, slots=True)
class ScreenEligibility:
    research_visible: bool
    action_eligible: bool
    reasons: tuple[str, ...]
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ScreenLead:
    symbol: str
    sector: str
    rank: int
    screen_ids: tuple[str, ...]
    primary_screen_id: str
    provider: str
    source_url: str
    observed_at: datetime
    entity_id: str | None
    security_id: str | None
    security_revision_id: str | None
    reference_manifest_id: str | None
    reference_state: str
    research_visible: bool
    action_eligible: bool
    reasons: tuple[str, ...]
    issuer_cik: str | None = None
    displayed_price: Decimal | None = None
    displayed_average_volume: int | None = None
    displayed_market_cap: int | None = None
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class ScreenReceipt:
    screen_id: str
    capability_id: str
    state: ScreenOutcome
    request_cost: int
    returned_count: int
    accepted_count: int
    reasons: tuple[str, ...]
    reviewed_at: date
    endpoint_version: str
    parser_version: str
    retention_class: str
    execution_allowed: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_count": self.accepted_count,
            "capability_id": self.capability_id,
            "endpoint_version": self.endpoint_version,
            "execution_allowed": False,
            "outcome": self.state,
            "parser_version": self.parser_version,
            "reasons": list(self.reasons),
            "request_cost": self.request_cost,
            "retention_class": self.retention_class,
            "returned_count": self.returned_count,
            "reviewed_at": self.reviewed_at.isoformat(),
            "sampled_output": True,
            "screen_id": self.screen_id,
        }


@dataclass(frozen=True, slots=True)
class ScreenResult:
    screen_id: str
    state: ScreenOutcome
    leads: tuple[ScreenLead, ...]
    source_items: tuple[SourceItem, ...]
    receipt: ScreenReceipt
    reasons: tuple[str, ...]

    @property
    def coverage(self) -> Mapping[str, object]:
        return MappingProxyType(self.receipt.to_mapping())


@dataclass(frozen=True, slots=True)
class BoundedScreenRun:
    results: tuple[ScreenResult, ...]
    leads: tuple[ScreenLead, ...]
    source_items: tuple[SourceItem, ...]
    coverage: Mapping[str, object]
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class InsiderPurchaseCluster:
    entity_id: str
    security_id: str
    reference_manifest_id: str
    reporting_person_ciks: tuple[str, ...]
    transaction_dates: tuple[date, ...]
    filing_ids: tuple[str, ...]
    window_days: int = 7
    limitations: tuple[str, ...] = ()
    execution_allowed: bool = False


def parse_market_scan_policy(settings: Mapping[str, object] | None = None) -> MarketFilterPolicy:
    document = config.load_settings() if settings is None else settings
    scan = document.get("market_scan") if isinstance(document, Mapping) else None
    if not isinstance(scan, Mapping) or set(scan) != {
        "enabled", "universe", "method", "universe_filters", "screens",
        "max_candidates_surfaced", "sectors",
    } or scan.get("enabled") is not True or scan.get("sectors") != "all":
        raise ValueError("market scan policy is invalid")
    filters = scan.get("universe_filters")
    if not isinstance(filters, Mapping) or set(filters) != {
        "min_price", "min_avg_daily_volume", "min_market_cap", "note"
    }:
        raise ValueError("market scan filters are invalid")
    price = _decimal(filters["min_price"], allow_none=False)
    return MarketFilterPolicy(
        min_price=price,  # type: ignore[arg-type]
        min_average_volume=_positive_integer_or_none(filters["min_avg_daily_volume"]),  # type: ignore[arg-type]
        min_market_cap=_positive_integer_or_none(filters["min_market_cap"]),  # type: ignore[arg-type]
        max_candidates=_positive_integer_or_none(scan["max_candidates_surfaced"]),  # type: ignore[arg-type]
    )


def load_screen_definitions() -> tuple[ScreenDefinition, ...]:
    from lib.intelligence.planner import load_source_capabilities

    settings = config.load_settings()
    scan = settings.get("market_scan")
    if not isinstance(scan, Mapping) or not isinstance(scan.get("screens"), list):
        raise ValueError("configured screen order is invalid")
    capabilities = load_source_capabilities()
    by_screen = {
        capability.screen_id: capability
        for capability in capabilities.values()
        if capability.query_kind == "screener" and capability.screen_id is not None
    }
    definitions: list[ScreenDefinition] = []
    for screen_id in scan["screens"]:
        capability = by_screen.get(screen_id)
        if capability is None:
            raise ValueError(f"configured screen {screen_id} has no capability")
        try:
            reviewed_at = date.fromisoformat(capability.reviewed_at or "")
        except ValueError:
            raise ValueError("screen review date is invalid") from None
        definitions.append(ScreenDefinition(
            screen_id=screen_id,
            capability_id=capability.capability_id,
            provider=capability.provider,
            state=capability.health,
            reasons=capability.status_reasons,
            reviewed_at=reviewed_at,
            endpoint_version=capability.endpoint_version or "unavailable",
            parser_version=capability.parser_version or "unavailable",
            retention_class=capability.retention_class,
            max_rows=capability.max_items_per_request,
        ))
    return tuple(definitions)


def apply_market_filters(
    _lead: ScreenObservation | ScreenLead,
    policy: MarketFilterPolicy,
    *,
    market_inputs: GatewayMarketInputs | None,
    primary_exposure_supported: bool = False,
    reference_resolved: bool = True,
) -> ScreenEligibility:
    if not isinstance(policy, MarketFilterPolicy):
        raise TypeError("screen filters require typed policy")
    reasons: list[str] = []
    if market_inputs is None or market_inputs.price is None:
        reasons.append("gateway_price_missing")
    elif market_inputs.price < policy.min_price:
        reasons.append("below_min_price")
    if market_inputs is None or market_inputs.average_volume is None:
        reasons.append("gateway_average_volume_missing")
    elif market_inputs.average_volume < policy.min_average_volume:
        reasons.append("below_min_average_volume")
    if market_inputs is None or market_inputs.market_cap is None:
        reasons.append("gateway_market_cap_missing")
    elif market_inputs.market_cap < policy.min_market_cap:
        reasons.append("below_min_market_cap")
    if not reference_resolved:
        reasons.append("current_reference_resolution_required")
    if not primary_exposure_supported:
        reasons.append("primary_exposure_required")
    return ScreenEligibility(
        research_visible=True,
        action_eligible=not reasons,
        reasons=tuple(reasons),
        execution_allowed=False,
    )


def _active_reference_matches(
    observation: ScreenObservation,
    reference: ReferenceSnapshot | None,
    *,
    reference_status: str,
    reference_manifest_id: str | None,
    as_of: date,
) -> tuple[str, SecurityIdentity | None]:
    if reference_status == "reference_unavailable" or reference is None \
            or reference_manifest_id is None:
        return "reference_unavailable", None
    matches = []
    for row in reference.securities:
        active = row.valid_from <= as_of and (row.valid_to is None or as_of <= row.valid_to)
        named = observation.symbol == row.ticker or observation.symbol in row.aliases
        pinned = row.reference_manifest_id == reference_manifest_id and row.revision_id is not None
        cik_matches = observation.issuer_cik is None or observation.issuer_cik == row.cik
        if active and named and pinned and cik_matches:
            matches.append(row)
    if len(matches) > 1:
        return "ambiguous", None
    if not matches:
        return "unresolved", None
    return "resolved", matches[0]


def _receipt(
    definition: ScreenDefinition,
    state: ScreenOutcome,
    *,
    returned: int,
    accepted: int,
    reasons: tuple[str, ...],
) -> ScreenReceipt:
    return ScreenReceipt(
        screen_id=definition.screen_id,
        capability_id=definition.capability_id,
        state=state,
        request_cost=0,
        returned_count=returned,
        accepted_count=accepted,
        reasons=reasons,
        reviewed_at=definition.reviewed_at,
        endpoint_version=definition.endpoint_version,
        parser_version=definition.parser_version,
        retention_class=definition.retention_class,
        execution_allowed=False,
    )


def inactive_screen_result(
    definition: ScreenDefinition,
    *,
    observed_at: datetime,
) -> ScreenResult:
    _utc(observed_at)
    state: ScreenOutcome = definition.state if definition.state in _INACTIVE_STATES else "unavailable"
    reasons = definition.reasons or ("screen_transport_unavailable",)
    receipt = _receipt(definition, state, returned=0, accepted=0, reasons=reasons)
    return ScreenResult(definition.screen_id, state, (), (), receipt, reasons)


def _fair_leads(
    leads: Sequence[ScreenLead], definitions: Sequence[ScreenDefinition], maximum: int
) -> tuple[ScreenLead, ...]:
    order = {row.screen_id: index for index, row in enumerate(definitions)}
    grouped: dict[str, dict[str, list[ScreenLead]]] = defaultdict(lambda: defaultdict(list))
    for lead in leads:
        grouped[lead.primary_screen_id][lead.sector].append(lead)
    for sectors in grouped.values():
        for values in sectors.values():
            values.sort(key=lambda row: (row.rank, row.symbol, row.security_id or ""))
    output: list[ScreenLead] = []
    screen_ids = sorted(grouped, key=lambda value: (order.get(value, 10_000), value))
    sector_offsets = {screen_id: 0 for screen_id in screen_ids}
    while len(output) < maximum and any(grouped.values()):
        progressed = False
        for screen_id in screen_ids:
            sectors = grouped.get(screen_id)
            if not sectors:
                continue
            names = sorted(sectors, key=lambda value: (value == "unknown", value.casefold()))
            offset = sector_offsets[screen_id] % len(names)
            sector = names[offset]
            output.append(sectors[sector].pop(0))
            progressed = True
            if not sectors[sector]:
                del sectors[sector]
            else:
                sector_offsets[screen_id] += 1
            if not sectors:
                del grouped[screen_id]
            if len(output) >= maximum:
                break
        if not progressed:
            break
    return tuple(output)


def _source_item(lead: ScreenLead) -> SourceItem:
    metadata = {
        "action_eligible": lead.action_eligible,
        "execution_allowed": False,
        "primary_screen_id": lead.primary_screen_id,
        "reasons": list(lead.reasons),
        "reference_manifest_id": lead.reference_manifest_id,
        "reference_state": lead.reference_state,
        "screen_ids": list(lead.screen_ids),
        "sector": lead.sector,
        "signal_type": "screen_signal",
    }
    canonical = json.dumps({
        "provider": lead.provider,
        "screen_ids": list(lead.screen_ids),
        "symbol": lead.symbol,
        "source_url": lead.source_url,
        "observed_at": _utc(lead.observed_at).isoformat(),
    }, sort_keys=True, separators=(",", ":"))
    return SourceItem(
        provider=lead.provider,
        upstream_item_id=f"screen:{lead.primary_screen_id}:{lead.symbol}",
        source_url=lead.source_url,
        title=f"{lead.symbol} appeared in {lead.primary_screen_id}",
        normalized_text=(
            f"Sampled discovery lead for {lead.symbol}; requires current reference, "
            "primary exposure, and gateway-owned market inputs."
        ),
        canonical_content=canonical,
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        published_at=lead.observed_at,
        effective_at=None,
        retrieved_at=lead.observed_at,
        authority="discovery_lead",
        metadata=MappingProxyType(metadata),
        request_url=None,
        entity_ids=(lead.entity_id,) if lead.entity_id is not None else (),
        security_ids=(lead.security_id,) if lead.security_id is not None else (),
    )


def run_bounded_screens(
    definitions: Sequence[ScreenDefinition],
    *,
    payloads: Mapping[str, ScreenPayload] | None = None,
    reference: ReferenceSnapshot | None,
    reference_status: str,
    reference_manifest_id: str | None,
    as_of: date,
    observed_at: datetime,
    policy: MarketFilterPolicy | None = None,
    gateway_market_inputs: Mapping[str, GatewayMarketInputs] | None = None,
    primary_exposure_security_ids: frozenset[str] = frozenset(),
) -> BoundedScreenRun:
    """Normalize explicit payloads; never discover or invoke transport implicitly."""
    if not isinstance(definitions, Sequence) or any(
        not isinstance(value, ScreenDefinition) for value in definitions
    ):
        raise TypeError("bounded screens require typed definitions")
    if len({value.screen_id for value in definitions}) != len(definitions):
        raise ValueError("screen definitions contain duplicates")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("screen reference date is invalid")
    _utc(observed_at)
    payloads = payloads or {}
    policy = policy or parse_market_scan_policy()
    gateway_market_inputs = gateway_market_inputs or {}
    definition_by_id = {value.screen_id: value for value in definitions}
    observations: list[tuple[ScreenObservation, ScreenDefinition]] = []
    preliminary: list[tuple[ScreenDefinition, ScreenOutcome, tuple[str, ...], int]] = []
    for definition in definitions:
        if definition.state != "enabled":
            preliminary.append((definition, definition.state, definition.reasons, 0))
            continue
        payload = payloads.get(definition.screen_id)
        if payload is None:
            preliminary.append((definition, "unavailable", ("screen_transport_unavailable",), 0))
            continue
        if payload.screen_id != definition.screen_id or payload.state not in _OUTCOMES:
            raise ValueError("screen payload does not match definition")
        preliminary.append((definition, payload.state, payload.reasons, len(payload.rows)))
        if payload.state in {"parsed_nonempty", "partial"}:
            observations.extend((row, definition) for row in payload.rows)

    order = {value.screen_id: index for index, value in enumerate(definitions)}
    merged: dict[tuple[str, str], list[tuple[ScreenObservation, ScreenDefinition, str, SecurityIdentity | None]]] = defaultdict(list)
    for observation, definition in observations:
        if observation.screen_id != definition.screen_id:
            raise ValueError("screen observation does not match payload")
        reference_state, security = _active_reference_matches(
            observation, reference,
            reference_status=reference_status,
            reference_manifest_id=reference_manifest_id,
            as_of=as_of,
        )
        key = (
            "security", security.security_id
        ) if security is not None else (
            "unresolved", f"{observation.symbol}:{observation.issuer_cik or ''}"
        )
        merged[key].append((observation, definition, reference_state, security))

    candidates: list[ScreenLead] = []
    for entries in merged.values():
        entries.sort(key=lambda value: (
            order.get(value[1].screen_id, 10_000), value[0].rank,
            value[0].symbol, value[0].source_url,
        ))
        observation, definition, reference_state, security = entries[0]
        screen_ids = tuple(sorted(
            {value[1].screen_id for value in entries},
            key=lambda value: (order.get(value, 10_000), value),
        ))
        market = gateway_market_inputs.get(
            security.security_id if security is not None else observation.symbol
        )
        eligibility = apply_market_filters(
            observation, policy, market_inputs=market,
            primary_exposure_supported=(
                security is not None and security.security_id in primary_exposure_security_ids
            ),
            reference_resolved=security is not None,
        )
        reasons = list(eligibility.reasons)
        if security is not None and not security.eligible:
            reasons.append("reference_ineligible")
        candidates.append(ScreenLead(
            symbol=observation.symbol,
            sector=observation.sector or "unknown",
            rank=min(value[0].rank for value in entries),
            screen_ids=screen_ids,
            primary_screen_id=definition.screen_id,
            provider=definition.provider,
            source_url=observation.source_url,
            observed_at=observation.observed_at,
            entity_id=security.entity_id if security is not None else None,
            security_id=security.security_id if security is not None else None,
            security_revision_id=security.revision_id if security is not None else None,
            reference_manifest_id=reference_manifest_id if security is not None else None,
            reference_state=reference_state,
            research_visible=True,
            action_eligible=eligibility.action_eligible and security is not None and security.eligible,
            reasons=tuple(dict.fromkeys(reasons)),
            issuer_cik=observation.issuer_cik,
            displayed_price=observation.displayed_price,
            displayed_average_volume=observation.displayed_average_volume,
            displayed_market_cap=observation.displayed_market_cap,
            execution_allowed=False,
        ))
    selected = _fair_leads(candidates, definitions, policy.max_candidates)
    items = tuple(_source_item(value) for value in selected)
    leads_by_screen = defaultdict(list)
    items_by_screen = defaultdict(list)
    for lead, item in zip(selected, items, strict=True):
        for screen_id in lead.screen_ids:
            leads_by_screen[screen_id].append(lead)
            items_by_screen[screen_id].append(item)
    results: list[ScreenResult] = []
    coverage_rows: list[dict[str, object]] = []
    for definition, state, reasons, returned in preliminary:
        accepted = tuple(leads_by_screen[definition.screen_id])
        receipt = _receipt(
            definition, state, returned=returned, accepted=len(accepted), reasons=reasons,
        )
        result = ScreenResult(
            definition.screen_id, state, accepted,
            tuple(items_by_screen[definition.screen_id]), receipt, reasons,
        )
        results.append(result)
        coverage_rows.append(receipt.to_mapping())
    coverage = MappingProxyType({
        "complete_market_coverage": False,
        "execution_allowed": False,
        "max_unique_leads": policy.max_candidates,
        "screen_definition_count": len(definitions),
        "screen_receipts": coverage_rows,
        "surfaced_unique_leads": len(selected),
    })
    return BoundedScreenRun(tuple(results), selected, items, coverage, False)


def cluster_form4_purchases(
    documents: Sequence[object],
    *,
    reference: ReferenceSnapshot | None,
    reference_manifest_id: str | None,
    as_of: date,
) -> tuple[InsiderPurchaseCluster, ...]:
    """Build only CIK-bound two-person open-market clusters within seven days."""
    if reference is None or reference_manifest_id is None:
        return ()
    from lib.intelligence.providers.sec import Form4ParseResult

    if any(not isinstance(value, Form4ParseResult) for value in documents):
        raise TypeError("Form 4 clustering requires typed parser results")
    by_cik = defaultdict(list)
    for row in reference.securities:
        if row.cik and row.reference_manifest_id == reference_manifest_id \
                and row.revision_id is not None and row.valid_from <= as_of \
                and (row.valid_to is None or as_of <= row.valid_to):
            by_cik[row.cik].append(row)
    transactions = defaultdict(list)
    for document in documents:
        if document.state != "parsed_nonempty":
            continue
        for transaction in document.transactions:
            if transaction.qualifies_for_cluster:
                transactions[transaction.issuer_cik].append(transaction)
    clusters: list[InsiderPurchaseCluster] = []
    for issuer_cik, rows in sorted(transactions.items()):
        securities = by_cik.get(issuer_cik, ())
        if len(securities) != 1:
            continue
        rows.sort(key=lambda row: (
            row.transaction_date, row.reporting_person_ciks, row.filing_id
        ))
        best: tuple[object, ...] | None = None
        for start in range(len(rows)):
            window = tuple(
                row for row in rows[start:]
                if (row.transaction_date - rows[start].transaction_date).days <= 6
            )
            independent = {
                row.reporting_person_ciks[0]
                for row in window if len(row.reporting_person_ciks) == 1
            }
            if len(independent) >= 2:
                best = window
                break
        if best is None:
            continue
        people = tuple(sorted({
            row.reporting_person_ciks[0] for row in best
            if len(row.reporting_person_ciks) == 1
        }))
        clusters.append(InsiderPurchaseCluster(
            entity_id=securities[0].entity_id,
            security_id=securities[0].security_id,
            reference_manifest_id=reference_manifest_id,
            reporting_person_ciks=people,
            transaction_dates=tuple(sorted({row.transaction_date for row in best})),
            filing_ids=tuple(sorted({row.filing_id for row in best})),
            window_days=7,
            limitations=tuple(sorted({
                limitation for row in best for limitation in row.limitations
            })),
            execution_allowed=False,
        ))
    return tuple(clusters)


__all__ = [
    "BoundedScreenRun",
    "GatewayMarketInputs",
    "InsiderPurchaseCluster",
    "MarketFilterPolicy",
    "ScreenDefinition",
    "ScreenEligibility",
    "ScreenLead",
    "ScreenObservation",
    "ScreenPayload",
    "ScreenReceipt",
    "ScreenResult",
    "apply_market_filters",
    "cluster_form4_purchases",
    "inactive_screen_result",
    "load_screen_definitions",
    "parse_market_scan_policy",
    "run_bounded_screens",
]
