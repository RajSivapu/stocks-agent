"""Typed, bounded exposure facts derived from validated primary filing passages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Literal, Mapping, Sequence
from urllib.parse import unquote, urlsplit
import uuid


_HASH = re.compile(r"[0-9a-f]{64}")
_CIK = re.compile(r"[0-9]{10}")
_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_OPERATIONAL = re.compile(
    r"\b(manufactur(?:e|es|ed|ing)|produc(?:e|es|ed|ing)|suppl(?:y|ies|ied)|"
    r"operat(?:e|es|ed|ing)|own(?:s|ed|ing)?|sell(?:s|ing)?|provid(?:e|es|ed|ing))\b",
    re.I,
)
_NEGATION = re.compile(
    r"\b(?:do(?:es)?\s+not|did\s+not|no\s+longer|never|not|discontinued|ceased)\b",
    re.I,
)
_PLANNED = re.compile(
    r"\b(?:plan(?:s|ned)?\s+to|intend(?:s|ed)?\s+to|propos(?:e|es|ed)|"
    r"future\s+facility|expects?\s+to|will\s+(?:begin|build|develop|manufacture|produce))\b",
    re.I,
)
_FORECAST = re.compile(r"\b(?:forecast|outlook|project(?:s|ed)?|estimate(?:s|d)?|demand\s+will)\b", re.I)
_CUSTOMER = re.compile(r"\b(?:customer|client|third[- ]party|supplier)\b", re.I)
_CONDITIONAL = re.compile(r"\b(?:may|might|could|if|subject\s+to)\b", re.I)
_REVIEWED_ROLES = frozenset({
    "mining", "separation", "metals_alloys", "magnet_manufacturing", "recycling",
    "substitutes", "downstream_motors", "extraction", "refining_smelting",
    "semifabrication", "conductors", "cooling", "structures",
    "data_center_electricity_cost_smelting", "material_substitution", "generation",
    "fuel", "transmission", "transformers_switchgear", "power_electronics", "storage",
    "construction", "efficiency_demand_reduction", "physical_holdings", "conversion",
    "enrichment_swu", "haleu", "fuel_fabrication", "reactor_equipment", "operators",
    "waste", "decommissioning", "oems", "actuators_motors", "gears_reducers",
    "sensors_vision", "controls", "chips_compute", "integrators", "batteries",
    "magnets", "adoption_constraints", "issuer_operations",
})


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("exposure timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _date(value: date | None, label: str) -> date | None:
    if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
        raise ValueError(f"{label} must be a date")
    return value


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    result = " ".join(value.split())
    if not result or len(result) > maximum:
        raise ValueError(f"{label} is invalid")
    return result


def _canonical(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_archive_url(value: str, cik: str, accession: str, document: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("filing source URL is unsafe") from exc
    decoded = unquote(parsed.path)
    expected = f"/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"
    if (
        parsed.scheme != "https" or parsed.hostname != "www.sec.gov" or port not in (None, 443)
        or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.path != decoded or "\\" in decoded or "/../" in decoded or decoded != expected
    ):
        raise ValueError("filing source URL is unsafe")
    return value


@dataclass(frozen=True, slots=True)
class IssuerExposureBinding:
    entity_id: str
    security_id: str
    security_revision_id: str
    reference_manifest_id: str
    cik: str
    canonical_name: str
    ticker: str
    reference_status: Literal["current"] = "current"

    def __post_init__(self) -> None:
        _text(self.entity_id, "entity ID", 160)
        _text(self.security_id, "security ID", 160)
        _text(self.security_revision_id, "security revision ID", 160)
        _text(self.reference_manifest_id, "reference manifest ID", 160)
        if _CIK.fullmatch(self.cik) is None or int(self.cik) == 0:
            raise ValueError("issuer CIK is invalid")
        _text(self.canonical_name, "issuer name", 300)
        if _TICKER.fullmatch(self.ticker) is None:
            raise ValueError("issuer ticker is invalid")
        if self.reference_status != "current":
            raise ValueError("exposure requires current pinned reference membership")


@dataclass(frozen=True, slots=True)
class FilingEvidence:
    issuer_cik: str
    accession_number: str
    form: str
    primary_document: str
    source_url: str
    source_response_hash: str
    submissions_response_hash: str
    passage: str
    source_locator: str
    normalized_passage_hash: str
    parser_version: str
    filing_rule_version: str
    schema_version: int
    filing_date: date
    accepted_at: datetime | None
    reporting_period_end: date | None
    retrieved_at: datetime
    source_item_id: str
    source_item_content_hash: str
    source_receipt_id: str
    source_cache_key: str

    def __post_init__(self) -> None:
        if _CIK.fullmatch(self.issuer_cik) is None or int(self.issuer_cik) == 0:
            raise ValueError("filing issuer CIK is invalid")
        if re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", self.accession_number) is None:
            raise ValueError("filing accession is invalid")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", self.primary_document) is None:
            raise ValueError("filing primary document is invalid")
        _safe_archive_url(self.source_url, self.issuer_cik, self.accession_number, self.primary_document)
        if _HASH.fullmatch(self.source_response_hash) is None \
                or _HASH.fullmatch(self.submissions_response_hash) is None:
            raise ValueError("filing response hash is invalid")
        passage = _text(self.passage, "filing passage", 2_000)
        locator = _text(self.source_locator, "filing locator", 256)
        object.__setattr__(self, "passage", passage)
        object.__setattr__(self, "source_locator", locator)
        if hashlib.sha256(passage.encode()).hexdigest() != self.normalized_passage_hash:
            raise ValueError("filing passage hash mismatch")
        _text(self.parser_version, "parser version", 80)
        _text(self.filing_rule_version, "filing rule version", 80)
        if self.schema_version != 1:
            raise ValueError("filing schema version is invalid")
        _date(self.filing_date, "filing date")
        _date(self.reporting_period_end, "reporting period end")
        if self.accepted_at is not None:
            _utc(self.accepted_at)
        _utc(self.retrieved_at)
        try:
            for value in (self.source_item_id, self.source_receipt_id):
                if str(uuid.UUID(value)) != value:
                    raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise ValueError("filing source identity is invalid") from None
        if _HASH.fullmatch(self.source_item_content_hash) is None \
                or _HASH.fullmatch(self.source_cache_key) is None:
            raise ValueError("filing source checkpoint binding is invalid")


ExposureStatus = Literal["supported", "contradicted", "superseded", "insufficient"]
ClaimState = Literal["operational", "planned", "forecast", "customer", "contradicted", "insufficient"]

_FACT_VALUE_FIELDS = frozenset({
    "accepted_at", "accession_number", "business_exposure", "claim_state",
    "effective_at", "entity_id", "event_ids", "execution_allowed", "filing_date",
    "filing_rule_version", "financial_materiality", "form", "is_amendment",
    "hypothesis_ids", "issuer_cik", "limitations", "metric",
    "normalized_passage_hash", "parser_version", "passage", "period_end",
    "period_start", "primary_document", "reference_manifest_id",
    "reporting_period_end", "retrieved_at", "role", "schema_version",
    "security_id", "security_revision_id", "source_cache_key",
    "source_item_content_hash", "source_item_id", "source_locator",
    "source_receipt_id", "source_response_hash", "source_url", "status",
    "submissions_response_hash", "ticker", "unit", "value",
})
_PERSISTENCE_FIELDS = frozenset({
    "id", "security_revision_id", "theme_episode_revision_id", "exposure_kind",
    "fact", "source_ids", "valid_from", "valid_to", "content_hash",
})
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")


@dataclass(frozen=True, slots=True)
class ExposureFact:
    entity_id: str
    security_id: str
    security_revision_id: str
    reference_manifest_id: str
    issuer_cik: str
    ticker: str
    role: str
    event_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    metric: str
    value: str | None
    unit: str | None
    period_start: date | None
    period_end: date | None
    passage: str
    source_locator: str
    source_item_id: str
    source_url: str
    source_response_hash: str
    accession_number: str
    form: str
    primary_document: str
    submissions_response_hash: str
    source_item_content_hash: str
    source_receipt_id: str
    source_cache_key: str
    normalized_passage_hash: str
    parser_version: str
    filing_rule_version: str
    schema_version: int
    filing_date: date
    accepted_at: datetime | None
    reporting_period_end: date | None
    effective_at: datetime | None
    retrieved_at: datetime
    claim_state: ClaimState
    business_exposure: Literal["supported", "contradicted", "unresolved"]
    financial_materiality: Literal["supported", "contradicted", "unknown"]
    status: ExposureStatus
    limitations: tuple[str, ...]
    is_amendment: bool
    execution_allowed: bool = False

    def __post_init__(self) -> None:
        if _CIK.fullmatch(self.issuer_cik) is None or int(self.issuer_cik) == 0 \
                or self.entity_id != f"sec-cik:{self.issuer_cik}":
            raise ValueError("exposure issuer identity is invalid")
        for value, label in (
            (self.security_id, "security ID"),
            (self.security_revision_id, "security revision ID"),
            (self.reference_manifest_id, "reference manifest ID"),
        ):
            _text(value, label, 160)
        if _TICKER.fullmatch(self.ticker) is None:
            raise ValueError("exposure ticker is invalid")
        role = _text(self.role, "exposure role", 80)
        if role not in _REVIEWED_ROLES:
            raise ValueError("exposure role is invalid")
        for values, label, maximum in (
            (self.event_ids, "exposure event IDs", 64),
            (self.hypothesis_ids, "exposure hypothesis IDs", 64),
            (self.limitations, "exposure limitations", 16),
        ):
            if not isinstance(values, tuple) or len(values) > maximum \
                    or any(_text(value, label, 160) != value for value in values):
                raise ValueError(f"{label} are invalid")
        if tuple(sorted(set(self.event_ids))) != self.event_ids \
                or tuple(sorted(set(self.hypothesis_ids))) != self.hypothesis_ids:
            raise ValueError("exposure evidence identities are invalid")
        if self.metric not in {"business_exposure", "revenue_share"}:
            raise ValueError("exposure metric is invalid")
        passage = _text(self.passage, "exposure passage", 2_000)
        locator = _text(self.source_locator, "exposure locator", 256)
        object.__setattr__(self, "passage", passage)
        object.__setattr__(self, "source_locator", locator)
        if hashlib.sha256(passage.encode()).hexdigest() != self.normalized_passage_hash:
            raise ValueError("exposure passage hash mismatch")
        for value in (
            self.source_response_hash, self.submissions_response_hash,
            self.source_item_content_hash, self.source_cache_key,
            self.normalized_passage_hash,
        ):
            if _HASH.fullmatch(value) is None:
                raise ValueError("exposure source hash is invalid")
        try:
            for value in (
                self.security_revision_id, self.reference_manifest_id,
                self.source_item_id, self.source_receipt_id,
            ):
                if str(uuid.UUID(value)) != value:
                    raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise ValueError("exposure source identity is invalid") from None
        if re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", self.accession_number) is None \
                or re.fullmatch(r"(?:10-K|10-Q|8-K|20-F|40-F)(?:/A)?", self.form) is None \
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", self.primary_document) is None:
            raise ValueError("exposure filing identity is invalid")
        _safe_archive_url(
            self.source_url, self.issuer_cik, self.accession_number, self.primary_document,
        )
        _text(self.parser_version, "parser version", 80)
        _text(self.filing_rule_version, "filing rule version", 80)
        if self.schema_version != 1 or not isinstance(self.is_amendment, bool) \
                or self.execution_allowed is not False:
            raise ValueError("exposure schema is invalid")
        _date(self.filing_date, "filing date")
        _date(self.period_start, "metric period start")
        _date(self.period_end, "metric period end")
        _date(self.reporting_period_end, "reporting period end")
        if self.accepted_at is not None:
            _utc(self.accepted_at)
        if self.effective_at is not None:
            _utc(self.effective_at)
        _utc(self.retrieved_at)
        if self.claim_state not in {
            "operational", "planned", "forecast", "customer", "contradicted", "insufficient",
        } or self.business_exposure not in {"supported", "contradicted", "unresolved"} \
                or self.financial_materiality not in {"supported", "contradicted", "unknown"} \
                or self.status not in {"supported", "contradicted", "superseded", "insufficient"}:
            raise ValueError("exposure state is invalid")
        if self.status == "supported" and (
            self.claim_state != "operational" or self.business_exposure != "supported"
        ):
            raise ValueError("exposure state is invalid")
        if self.status == "contradicted" and (
            self.claim_state != "contradicted" or self.business_exposure != "contradicted"
        ):
            raise ValueError("exposure state is invalid")
        if self.status == "insufficient" and (
            self.claim_state not in {"planned", "forecast", "customer", "insufficient"}
            or self.business_exposure != "unresolved"
        ):
            raise ValueError("exposure state is invalid")
        supported_materiality = self.financial_materiality == "supported"
        valid_percent = False
        if isinstance(self.value, str) and len(self.value) <= 32 \
                and _DECIMAL.fullmatch(self.value) is not None:
            try:
                decimal = Decimal(self.value)
                valid_percent = decimal.is_finite() and Decimal("0") <= decimal <= Decimal("100")
            except InvalidOperation:
                valid_percent = False
        if supported_materiality and not (
            self.metric == "revenue_share" and valid_percent
            and self.unit == "percent_of_revenue"
            and self.period_end is not None
            and self.period_end == self.reporting_period_end
            and self.claim_state == "operational"
            and self.business_exposure == "supported"
            and self.status == "supported"
        ):
            raise ValueError("financial materiality support is invalid")
        if self.metric == "business_exposure" and (
            self.value is not None or self.unit is not None
            or self.financial_materiality != "unknown"
        ):
            raise ValueError("financial materiality support is invalid")
        if self.metric == "revenue_share" and (
            not valid_percent or self.unit != "percent_of_revenue"
        ):
            raise ValueError("financial materiality support is invalid")

    def semantic_document(self) -> dict[str, object]:
        return {
            "kind": "exposure_fact",
            "semantic_encoding_version": 1,
            "value": {
                "accepted_at": _timestamp(self.accepted_at),
                "business_exposure": self.business_exposure,
                "claim_state": self.claim_state,
                "effective_at": _timestamp(self.effective_at),
                "entity_id": self.entity_id,
                "event_ids": list(self.event_ids),
                "execution_allowed": self.execution_allowed,
                "filing_date": self.filing_date.isoformat(),
                "filing_rule_version": self.filing_rule_version,
                "financial_materiality": self.financial_materiality,
                "is_amendment": self.is_amendment,
                "hypothesis_ids": list(self.hypothesis_ids),
                "issuer_cik": self.issuer_cik,
                "limitations": list(self.limitations),
                "metric": self.metric,
                "normalized_passage_hash": self.normalized_passage_hash,
                "parser_version": self.parser_version,
                "passage": self.passage,
                "period_end": self.period_end.isoformat() if self.period_end else None,
                "period_start": self.period_start.isoformat() if self.period_start else None,
                "reference_manifest_id": self.reference_manifest_id,
                "reporting_period_end": self.reporting_period_end.isoformat() if self.reporting_period_end else None,
                "retrieved_at": _timestamp(self.retrieved_at),
                "role": self.role,
                "schema_version": self.schema_version,
                "security_id": self.security_id,
                "security_revision_id": self.security_revision_id,
                "source_item_id": self.source_item_id,
                "source_locator": self.source_locator,
                "source_response_hash": self.source_response_hash,
                "accession_number": self.accession_number,
                "form": self.form,
                "primary_document": self.primary_document,
                "submissions_response_hash": self.submissions_response_hash,
                "source_item_content_hash": self.source_item_content_hash,
                "source_receipt_id": self.source_receipt_id,
                "source_cache_key": self.source_cache_key,
                "source_url": self.source_url,
                "status": self.status,
                "ticker": self.ticker,
                "unit": self.unit,
                "value": self.value,
            },
        }

    @property
    def semantic_hash(self) -> str:
        return hashlib.sha256(_canonical(self.semantic_document()).encode()).hexdigest()

    @property
    def fact_id(self) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"market-exposure:{self.semantic_hash}"))

    def to_persistence_row(self) -> dict[str, object]:
        return {
            "id": self.fact_id,
            "security_revision_id": self.security_revision_id,
            "theme_episode_revision_id": None,
            "exposure_kind": "filing",
            "fact": self.semantic_document(),
            "source_ids": [self.source_item_id],
            "valid_from": self.filing_date.isoformat(),
            "valid_to": None,
            "content_hash": self.semantic_hash,
        }


def _parsed_timestamp(value: object, label: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} is invalid") from None
    return _utc(parsed)


def _parsed_date(value: object, label: str, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} is invalid") from None
    if parsed.isoformat() != value:
        raise ValueError(f"{label} is invalid")
    return parsed


def exposure_fact_from_persistence(row: object) -> ExposureFact:
    """Validate and rebuild one immutable typed exposure fact from protected storage."""
    if not isinstance(row, Mapping):
        raise ValueError("persisted exposure fact is invalid")
    source = dict(row)
    optional = {"task_id", "run_id", "created_at"}
    if not _PERSISTENCE_FIELDS <= source.keys() or set(source) - _PERSISTENCE_FIELDS - optional:
        raise ValueError("persisted exposure fact is invalid")
    if source.get("theme_episode_revision_id") is not None \
            or source.get("exposure_kind") != "filing" \
            or source.get("valid_to") is not None:
        raise ValueError("persisted exposure fact is invalid")
    semantic = source.get("fact")
    if not isinstance(semantic, Mapping):
        raise ValueError("persisted exposure fact is invalid")
    semantic = dict(semantic)
    if set(semantic) != {"kind", "semantic_encoding_version", "value"} \
            or semantic.get("kind") != "exposure_fact" \
            or semantic.get("semantic_encoding_version") != 1:
        raise ValueError("persisted exposure fact is invalid")
    value = semantic.get("value")
    if not isinstance(value, Mapping):
        raise ValueError("persisted exposure fact is invalid")
    value = dict(value)
    if set(value) != _FACT_VALUE_FIELDS:
        raise ValueError("persisted exposure fact is invalid")
    try:
        fact = ExposureFact(
            entity_id=value["entity_id"], security_id=value["security_id"],
            security_revision_id=value["security_revision_id"],
            reference_manifest_id=value["reference_manifest_id"],
            issuer_cik=value["issuer_cik"], ticker=value["ticker"], role=value["role"],
            event_ids=tuple(value["event_ids"]), hypothesis_ids=tuple(value["hypothesis_ids"]),
            metric=value["metric"], value=value["value"], unit=value["unit"],
            period_start=_parsed_date(value["period_start"], "metric period start", nullable=True),
            period_end=_parsed_date(value["period_end"], "metric period end", nullable=True),
            passage=value["passage"], source_locator=value["source_locator"],
            source_item_id=value["source_item_id"], source_url=value["source_url"],
            source_response_hash=value["source_response_hash"],
            accession_number=value["accession_number"], form=value["form"],
            primary_document=value["primary_document"],
            submissions_response_hash=value["submissions_response_hash"],
            source_item_content_hash=value["source_item_content_hash"],
            source_receipt_id=value["source_receipt_id"],
            source_cache_key=value["source_cache_key"],
            normalized_passage_hash=value["normalized_passage_hash"],
            parser_version=value["parser_version"],
            filing_rule_version=value["filing_rule_version"],
            schema_version=value["schema_version"],
            filing_date=_parsed_date(value["filing_date"], "filing date"),
            accepted_at=_parsed_timestamp(value["accepted_at"], "accepted at", nullable=True),
            reporting_period_end=_parsed_date(
                value["reporting_period_end"], "reporting period end", nullable=True,
            ),
            effective_at=_parsed_timestamp(value["effective_at"], "effective at", nullable=True),
            retrieved_at=_parsed_timestamp(value["retrieved_at"], "retrieved at"),
            claim_state=value["claim_state"], business_exposure=value["business_exposure"],
            financial_materiality=value["financial_materiality"], status=value["status"],
            limitations=tuple(value["limitations"]), is_amendment=value["is_amendment"],
            execution_allowed=value["execution_allowed"],
        )
    except (KeyError, TypeError):
        raise ValueError("persisted exposure fact is invalid") from None
    if fact.semantic_document() != semantic \
            or source.get("content_hash") != fact.semantic_hash \
            or source.get("id") != fact.fact_id \
            or source.get("security_revision_id") != fact.security_revision_id \
            or source.get("source_ids") != [fact.source_item_id]:
        raise ValueError("persisted exposure fact semantic hash is invalid")
    if not fact.event_ids or not fact.hypothesis_ids \
            or tuple(sorted(set(fact.limitations))) != fact.limitations:
        raise ValueError("persisted exposure fact evidence binding is invalid")
    valid_from = source.get("valid_from")
    if isinstance(valid_from, str) and "T" in valid_from:
        parsed_valid_from = _parsed_timestamp(valid_from, "exposure valid from")
        if parsed_valid_from is None or parsed_valid_from.time() != datetime.min.time():
            raise ValueError("persisted exposure fact is invalid")
        valid_date = parsed_valid_from.date()
    else:
        valid_date = _parsed_date(valid_from, "exposure valid from")
    if valid_date != fact.filing_date:
        raise ValueError("persisted exposure fact is invalid")
    if "task_id" in source:
        try:
            if str(uuid.UUID(str(source["task_id"]))) != source["task_id"]:
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise ValueError("persisted exposure fact task identity is invalid") from None
    if "created_at" in source:
        _parsed_timestamp(source["created_at"], "exposure created at")
    return fact


@dataclass(frozen=True, slots=True)
class ExposureEvaluation:
    business_exposure: Literal["supported", "contradicted", "unresolved"]
    financial_materiality: Literal["supported", "contradicted", "unknown"]
    supported_fact_ids: tuple[str, ...]
    limitations: tuple[str, ...]
    execution_allowed: bool = False


def _classification(passage: str, issuer_name: str) -> ClaimState:
    text = passage.casefold()
    if _NEGATION.search(text) and _OPERATIONAL.search(text):
        return "contradicted"
    if _PLANNED.search(text):
        return "planned"
    if _FORECAST.search(text):
        return "forecast"
    if re.search(
        r"\b(?:our\s+)?(?:customer|client|third[- ]party|supplier)s?\b[^.!?]{0,160}"
        r"(?:manufactur|produc|suppl|operat|own|sell|provid)",
        text,
    ):
        return "customer"
    if _CONDITIONAL.search(text) and not _OPERATIONAL.search(text):
        return "insufficient"
    issuer_tokens = [token for token in re.findall(r"[a-z0-9]+", issuer_name.casefold()) if len(token) >= 3]
    attributed = bool(re.search(r"\b(?:we|our|the company)\b", text)) or (
        issuer_tokens and all(token in text for token in issuer_tokens[:2])
    )
    if attributed and _OPERATIONAL.search(text):
        return "operational"
    if _CUSTOMER.search(text):
        return "customer"
    return "insufficient"


def _materiality(
    passage: str, state: ClaimState, reporting_period_end: date | None,
) -> tuple[str, str | None, str | None]:
    if state != "operational" or reporting_period_end is None:
        return "unknown", None, None
    percent = re.search(
        r"(?P<value>[0-9]+(?:\.[0-9]+)?)\s*%\s+of\s+(?:our\s+|consolidated\s+)?revenue",
        passage,
        re.I,
    )
    if percent:
        value = Decimal(percent.group("value"))
        if value.is_finite() and Decimal("0") <= value <= Decimal("100"):
            canonical = format(value.normalize(), "f")
            return "supported", "0" if canonical == "-0" else canonical, "percent_of_revenue"
    return "unknown", None, None


def extract_exposure_facts(
    document: object,
    *,
    issuer: IssuerExposureBinding,
    role: str | None = None,
    event_ids: Sequence[str] = (),
    hypothesis_ids: Sequence[str] = (),
) -> tuple[ExposureFact, ...]:
    """Return one typed claim for a validated passage; metadata by itself returns none."""
    if not isinstance(issuer, IssuerExposureBinding):
        raise TypeError("exposure issuer binding is required")
    if not isinstance(document, FilingEvidence):
        return ()
    if document.issuer_cik != issuer.cik:
        raise ValueError("filing issuer does not match current reference binding")
    role_value = _text(role or "issuer_operations", "exposure role", 80)
    if re.fullmatch(r"[a-z][a-z0-9_]{2,79}", role_value) is None or role_value not in _REVIEWED_ROLES:
        raise ValueError("exposure role is invalid")
    claim_state = _classification(document.passage, issuer.canonical_name)
    events = tuple(sorted({_text(value, "exposure event ID", 160) for value in event_ids}))
    hypotheses = tuple(sorted({_text(value, "exposure hypothesis ID", 160) for value in hypothesis_ids}))
    if claim_state == "operational":
        status: ExposureStatus = "supported"
        business = "supported"
    elif claim_state == "contradicted":
        status = "contradicted"
        business = "contradicted"
    else:
        status = "insufficient"
        business = "unresolved"
    financial, value, unit = _materiality(
        document.passage, claim_state, document.reporting_period_end,
    )
    limitations: list[str] = []
    if financial == "unknown":
        limitations.append("financial_materiality_unknown")
    if claim_state != "operational":
        limitations.append(f"claim_state_{claim_state}")
    is_amendment = document.form.upper().endswith("/A")
    if is_amendment:
        limitations.append("automatic_supersession_forbidden")
    fact = ExposureFact(
        entity_id=issuer.entity_id,
        security_id=issuer.security_id,
        security_revision_id=issuer.security_revision_id,
        reference_manifest_id=issuer.reference_manifest_id,
        issuer_cik=issuer.cik,
        ticker=issuer.ticker,
        role=role_value,
        event_ids=events,
        hypothesis_ids=hypotheses,
        metric="revenue_share" if unit == "percent_of_revenue" else "business_exposure",
        value=value,
        unit=unit,
        period_start=None,
        period_end=document.reporting_period_end,
        passage=document.passage,
        source_locator=document.source_locator,
        source_item_id=document.source_item_id,
        source_url=document.source_url,
        source_response_hash=document.source_response_hash,
        accession_number=document.accession_number,
        form=document.form,
        primary_document=document.primary_document,
        submissions_response_hash=document.submissions_response_hash,
        source_item_content_hash=document.source_item_content_hash,
        source_receipt_id=document.source_receipt_id,
        source_cache_key=document.source_cache_key,
        normalized_passage_hash=document.normalized_passage_hash,
        parser_version=document.parser_version,
        filing_rule_version=document.filing_rule_version,
        schema_version=document.schema_version,
        filing_date=document.filing_date,
        accepted_at=document.accepted_at,
        reporting_period_end=document.reporting_period_end,
        effective_at=None,
        retrieved_at=document.retrieved_at,
        claim_state=claim_state,
        business_exposure=business,
        financial_materiality=financial,  # type: ignore[arg-type]
        status=status,
        limitations=tuple(sorted(limitations)),
        is_amendment=is_amendment,
        execution_allowed=False,
    )
    return (fact,)


def evaluate_exposure(facts: Sequence[ExposureFact]) -> ExposureEvaluation:
    if any(not isinstance(fact, ExposureFact) for fact in facts):
        raise TypeError("exposure evaluation requires typed facts")
    contradicted = any(fact.status == "contradicted" for fact in facts)
    supported_facts = tuple(fact for fact in facts if fact.status == "supported")
    comparable: dict[tuple[object, ...], set[tuple[object, ...]]] = {}
    for fact in supported_facts:
        key = (
            fact.entity_id, fact.security_id, fact.role, fact.metric,
            fact.period_start, fact.period_end, fact.reporting_period_end,
        )
        comparable.setdefault(key, set()).add((
            fact.value, fact.unit, fact.business_exposure,
            fact.financial_materiality, fact.claim_state,
        ))
    conflicting_keys = {key for key, values in comparable.items() if len(values) > 1}
    eligible = () if contradicted else tuple(
        fact for fact in supported_facts
        if (
            fact.entity_id, fact.security_id, fact.role, fact.metric,
            fact.period_start, fact.period_end, fact.reporting_period_end,
        ) not in conflicting_keys
    )
    supported = tuple(sorted(fact.fact_id for fact in eligible))
    business = "contradicted" if contradicted else "supported" if supported else "unresolved"
    materiality = "supported" if any(
        fact.financial_materiality == "supported" for fact in eligible
    ) else "contradicted" if any(
        fact.financial_materiality == "contradicted" for fact in facts
    ) else "unknown"
    limitations_set = {item for fact in facts for item in fact.limitations}
    if conflicting_keys:
        limitations_set.add("unresolved_comparable_claim_conflict")
    limitations = tuple(sorted(limitations_set))
    if not facts:
        limitations = ("primary_exposure_evidence_missing",)
    return ExposureEvaluation(
        business_exposure=business,  # type: ignore[arg-type]
        financial_materiality=materiality,  # type: ignore[arg-type]
        supported_fact_ids=supported,
        limitations=limitations,
        execution_allowed=False,
    )


__all__ = [
    "ExposureEvaluation",
    "ExposureFact",
    "FilingEvidence",
    "IssuerExposureBinding",
    "evaluate_exposure",
    "exposure_fact_from_persistence",
    "extract_exposure_facts",
]
