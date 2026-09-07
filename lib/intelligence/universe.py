"""Dated, immutable security identities from bounded reference sources.

The SEC company-ticker document is the only enabled reference input in V1.
Symbol-directory parsing is kept offline for fixtures and a future capability
review; it is deliberately not wired to transport.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid5

from lib.intelligence.http import BoundedHttpClient, HttpRequest, SourceFailure
from lib.edgar import sec_user_agent


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
REFERENCE_PARSER_VERSION = 1
REFERENCE_SEMANTIC_ENCODING_VERSION = 1
MAX_REFERENCE_SOURCE_BYTES = 5_000_000
MAX_REFERENCE_SECURITIES = 15_000
_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_CIK = re.compile(r"[0-9]{1,10}")
_ELIGIBLE_INSTRUMENTS = frozenset({"COMMON_STOCK", "ADR", "ETF"})
_SUPPORTED_EXCHANGES = frozenset({"NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA", "BATS", "IEX"})
_DIRECTORY_EXCHANGES = {
    "A": "NYSEAMERICAN",
    "N": "NYSE",
    "P": "NYSEARCA",
    "Q": "NASDAQ",
    "V": "IEX",
    "Z": "BATS",
    "U": "OTC",
}


@dataclass(frozen=True, slots=True)
class FormerIssuerName:
    name: str
    valid_from: date
    valid_to: date


@dataclass(frozen=True, slots=True)
class IssuerIdentity:
    entity_id: str
    cik: str | None
    canonical_name: str
    former_names: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    source_ids: tuple[str, ...]
    observed_names: tuple[str, ...] = ()
    dated_former_names: tuple[FormerIssuerName, ...] = ()


@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    security_id: str
    entity_id: str
    ticker: str
    exchange: str | None
    instrument_type: str
    valid_from: date
    valid_to: date | None
    aliases: tuple[str, ...]
    source_ids: tuple[str, ...]
    eligible: bool
    exclusion_reasons: tuple[str, ...]

    @property
    def cik(self) -> str | None:
        prefix = "sec-cik:"
        if not self.entity_id.startswith(prefix):
            return None
        value = self.entity_id[len(prefix) :]
        return value if re.fullmatch(r"[0-9]{10}", value) else None


@dataclass(frozen=True, slots=True)
class Eligibility:
    eligible: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class ReferenceConflict:
    code: str
    ticker: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceManifest:
    reference_version: str
    source_hash: str
    source_url: str
    retrieved_at: datetime
    source_timestamp: datetime | None
    parser_version: int
    coverage_status: Literal["scope_not_guaranteed"]
    reference_status: Literal["healthy", "reference_stale"]
    security_count: int


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    manifest: ReferenceManifest
    issuers: tuple[IssuerIdentity, ...]
    securities: tuple[SecurityIdentity, ...]
    conflicts: tuple[ReferenceConflict, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def by_ticker(self) -> Mapping[str, SecurityIdentity]:
        return MappingProxyType({row.ticker: row for row in self.securities})

    @property
    def issuers_by_id(self) -> Mapping[str, IssuerIdentity]:
        return MappingProxyType({row.entity_id: row for row in self.issuers})


@dataclass(frozen=True, slots=True)
class ReferenceRefresh:
    snapshot: ReferenceSnapshot | None
    status: Literal["healthy", "reference_stale", "reference_unavailable"]
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ReferenceTransfer:
    begin: dict[str, object]
    chunks: tuple[dict[str, object], ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("reference timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _source_bytes(source: bytes | bytearray | memoryview | str | Path) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        raw = bytes(source)
    elif isinstance(source, (str, Path)):
        raw = Path(source).read_bytes()
    else:
        raise TypeError("reference source must be bytes or a path")
    if not raw or len(raw) > MAX_REFERENCE_SOURCE_BYTES:
        raise ValueError("reference source exceeds byte bound")
    return raw


def _bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{label} is invalid")
    return normalized


def _ticker(value: object, label: str) -> str:
    ticker = _bounded_text(value, label, 15).upper()
    if not _TICKER.fullmatch(ticker):
        raise ValueError(f"{label} is invalid")
    return ticker


def _instrument_type(name: str, *, etf: bool = False, exchange: str | None = None) -> str:
    upper = f" {name.upper()} "
    if exchange == "OTC":
        return "OTC_COMMON"
    if etf or re.search(r"\bETF\b", upper):
        return "ETF"
    if "PREFERRED" in upper or "PREFERENCE" in upper:
        return "PREFERRED"
    if "AMERICAN DEPOSITARY" in upper or "DEPOSITARY SHARES" in upper or re.search(
        r"\bADS\b", upper
    ):
        return "ADR"
    if "WARRANT" in upper:
        return "WARRANT"
    if re.search(r"\b(RIGHT|RIGHTS|UNIT|UNITS)\b", upper):
        return "OTHER"
    return "COMMON_STOCK"


def eligible_for_research(security: SecurityIdentity) -> Eligibility:
    """Return the first explicit reason a security is outside research scope."""
    if security.exclusion_reasons:
        return Eligibility(False, security.exclusion_reasons[0])
    if security.instrument_type == "OTC_COMMON" or security.exchange == "OTC":
        return Eligibility(False, "market_excluded")
    if security.exchange is not None and security.exchange not in _SUPPORTED_EXCHANGES:
        return Eligibility(False, "market_excluded")
    if security.instrument_type not in _ELIGIBLE_INSTRUMENTS:
        return Eligibility(False, "instrument_type_excluded")
    return Eligibility(True, None)


def _with_eligibility(row: SecurityIdentity) -> SecurityIdentity:
    result = eligible_for_research(row)
    reasons = row.exclusion_reasons
    if not result.eligible and not reasons and result.reason is not None:
        reasons = (result.reason,)
    return replace(row, eligible=result.eligible, exclusion_reasons=reasons)


def _manifest(
    raw: bytes,
    *,
    source_url: str,
    retrieved_at: datetime,
    source_timestamp: datetime | None,
    source_name: str,
    security_count: int,
) -> ReferenceManifest:
    digest = hashlib.sha256(raw).hexdigest()
    timestamp = _utc(retrieved_at)
    return ReferenceManifest(
        reference_version=f"{source_name}:{timestamp.date().isoformat()}:{digest[:16]}",
        source_hash=digest,
        source_url=source_url,
        retrieved_at=timestamp,
        source_timestamp=_utc(source_timestamp) if source_timestamp is not None else None,
        parser_version=REFERENCE_PARSER_VERSION,
        coverage_status="scope_not_guaranteed",
        reference_status="healthy",
        security_count=security_count,
    )


def parse_sec_company_tickers(
    source: bytes | bytearray | memoryview | str | Path,
    *,
    retrieved_at: datetime,
) -> ReferenceSnapshot:
    """Parse the exact bounded SEC bytes without claiming complete listing scope."""
    raw = _source_bytes(source)
    retrieved = _utc(retrieved_at)
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("SEC company ticker document is invalid") from None
    if not isinstance(document, dict) or not 1 <= len(document) <= MAX_REFERENCE_SECURITIES:
        raise ValueError("SEC company ticker document is invalid")

    issuers: dict[str, IssuerIdentity] = {}
    securities: list[SecurityIdentity] = []
    ticker_entities: dict[str, str] = {}
    security_ids: set[str] = set()
    for key in sorted(document, key=lambda item: int(item) if str(item).isdigit() else -1):
        row = document[key]
        if not str(key).isdigit() or not isinstance(row, dict) or set(row) != {
            "cik_str",
            "ticker",
            "title",
        }:
            raise ValueError("SEC company ticker row is invalid")
        cik_value = row["cik_str"]
        if isinstance(cik_value, bool):
            raise ValueError("SEC company ticker row is invalid")
        cik_text = str(cik_value)
        if not _CIK.fullmatch(cik_text) or int(cik_text) < 1:
            raise ValueError("SEC company ticker row is invalid")
        cik = cik_text.zfill(10)
        try:
            ticker = _ticker(row["ticker"], "SEC company ticker row")
            title = _bounded_text(row["title"], "SEC company ticker row", 300)
        except ValueError:
            raise ValueError("SEC company ticker row is invalid") from None
        entity_id = f"sec-cik:{cik}"
        other_entity = ticker_entities.setdefault(ticker, entity_id)
        if other_entity != entity_id:
            raise ValueError(f"conflicting SEC ticker: {ticker}")
        source_id = f"sec-company-tickers:{cik}"
        current = issuers.get(entity_id)
        if current is None:
            issuers[entity_id] = IssuerIdentity(
                entity_id=entity_id,
                cik=cik,
                canonical_name=title,
                former_names=(),
                valid_from=retrieved.date(),
                valid_to=None,
                source_ids=(source_id,),
                observed_names=(title,),
            )
        elif title not in current.observed_names:
            issuers[entity_id] = replace(
                current,
                observed_names=tuple(sorted((*current.observed_names, title))),
            )
        instrument = _instrument_type(title)
        security_id = f"{entity_id}:listing-origin:{ticker}"
        if security_id in security_ids:
            raise ValueError(f"duplicate SEC security identity: {ticker}")
        security_ids.add(security_id)
        securities.append(
            _with_eligibility(
                SecurityIdentity(
                    security_id=security_id,
                    entity_id=entity_id,
                    ticker=ticker,
                    exchange=None,
                    instrument_type=instrument,
                    valid_from=retrieved.date(),
                    valid_to=None,
                    aliases=(ticker,),
                    source_ids=(source_id,),
                    eligible=True,
                    exclusion_reasons=(),
                )
            )
        )
    ordered = tuple(sorted(securities, key=lambda row: (row.ticker, row.security_id)))
    return ReferenceSnapshot(
        manifest=_manifest(
            raw,
            source_url=SEC_COMPANY_TICKERS_URL,
            retrieved_at=retrieved,
            source_timestamp=None,
            source_name="sec-company-tickers",
            security_count=len(ordered),
        ),
        issuers=tuple(sorted(issuers.values(), key=lambda row: row.entity_id)),
        securities=ordered,
    )


def _directory_timestamp(line: str) -> datetime | None:
    match = re.fullmatch(r"File Creation Time: (\d{8})(\d{2}):(\d{2})(?:\|.*)?", line)
    if match is None:
        return None
    value = datetime.strptime("".join(match.groups()), "%Y%m%d%H%M")
    return value.replace(tzinfo=timezone.utc)


def parse_symbol_directory(
    source: bytes | bytearray | memoryview | str | Path,
    *,
    retrieved_at: datetime,
) -> ReferenceSnapshot:
    """Parse saved Nasdaq-format fixtures; no live symbol-directory route is enabled."""
    raw = _source_bytes(source)
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("symbol directory is invalid") from None
    lines = [line.rstrip("\r") for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        raise ValueError("symbol directory is invalid")
    header = lines[0].split("|")
    if header[:2] == ["Symbol", "Security Name"]:
        kind = "nasdaq-listed"
        expected = header
        symbol_field = "Symbol"
        exchange_field = None
        fixed_exchange = "NASDAQ"
    elif header[:2] == ["ACT Symbol", "Security Name"]:
        kind = "other-listed"
        expected = header
        symbol_field = "ACT Symbol"
        exchange_field = "Exchange"
        fixed_exchange = None
    else:
        raise ValueError("symbol directory is invalid")

    timestamps = [
        value for value in (_directory_timestamp(line) for line in lines[1:])
        if value is not None
    ]
    if len(timestamps) != 1:
        raise ValueError("symbol directory must have one creation time")
    created_at = timestamps[0]
    issuers: list[IssuerIdentity] = []
    securities: list[SecurityIdentity] = []
    seen: set[tuple[str, str, str]] = set()
    for line in lines[1:]:
        timestamp = _directory_timestamp(line)
        if timestamp is not None:
            continue
        fields = line.split("|")
        if len(fields) != len(expected):
            raise ValueError("symbol directory row is invalid")
        row = dict(zip(expected, fields, strict=True))
        try:
            ticker = _ticker(row[symbol_field], "symbol directory row")
            name = _bounded_text(row["Security Name"], "symbol directory row", 300)
        except ValueError:
            raise ValueError("symbol directory row is invalid") from None
        exchange = fixed_exchange or _DIRECTORY_EXCHANGES.get(row[exchange_field or ""])
        test_issue = row.get("Test Issue")
        etf = row.get("ETF")
        if test_issue not in {"Y", "N"} or etf not in {"Y", "N"}:
            raise ValueError("symbol directory row is invalid")
        if exchange is None:
            exchange = "UNSUPPORTED"
        instrument = _instrument_type(name, etf=etf == "Y", exchange=exchange)
        source_id = f"{kind}:{ticker}"
        entity_id = f"symbol-directory:{ticker}"
        reasons = ("test_issue",) if test_issue == "Y" else ()
        item = _with_eligibility(
            SecurityIdentity(
                security_id=f"{entity_id}:{kind}",
                entity_id=entity_id,
                ticker=ticker,
                exchange=exchange,
                instrument_type=instrument,
                valid_from=created_at.date(),
                valid_to=None,
                aliases=(ticker,),
                source_ids=(source_id,),
                eligible=not reasons,
                exclusion_reasons=reasons,
            )
        )
        identity = (ticker, exchange, instrument)
        if identity in seen:
            raise ValueError("symbol directory has a duplicate listing")
        seen.add(identity)
        securities.append(item)
        issuers.append(
            IssuerIdentity(
                entity_id=entity_id,
                cik=None,
                canonical_name=name,
                former_names=(),
                valid_from=item.valid_from,
                valid_to=None,
                source_ids=(source_id,),
                observed_names=(name,),
            )
        )
    if not securities or len(securities) > MAX_REFERENCE_SECURITIES:
        raise ValueError("symbol directory is invalid")
    ordered = tuple(sorted(securities, key=lambda row: (row.ticker, row.security_id)))
    return ReferenceSnapshot(
        manifest=_manifest(
            raw,
            source_url=f"fixture://{kind}",
            retrieved_at=_utc(retrieved_at),
            source_timestamp=created_at,
            source_name=kind,
            security_count=len(ordered),
        ),
        issuers=tuple(sorted(issuers, key=lambda row: row.entity_id)),
        securities=ordered,
    )


def _aliases(current: str, values: Sequence[str]) -> tuple[str, ...]:
    return (current, *sorted({value for value in values if value != current}))


def _ambiguous(rows: Sequence[SecurityIdentity], ticker: str) -> SecurityIdentity:
    first = sorted(rows, key=lambda row: (row.source_ids, row.security_id))[0]
    return replace(
        first,
        ticker=ticker,
        exchange=None,
        aliases=_aliases(ticker, tuple(alias for row in rows for alias in row.aliases)),
        source_ids=tuple(sorted({source for row in rows for source in row.source_ids})),
        eligible=False,
        exclusion_reasons=("ambiguous_listing",),
    )


def merge_reference_sources(
    sec_rows: Sequence[SecurityIdentity],
    listing_rows: Sequence[SecurityIdentity],
    *,
    as_of: date,
) -> ReferenceSnapshot:
    """Merge exact identities and symbols; surface disagreement without guessing by name."""
    if not isinstance(as_of, date):
        raise TypeError("as_of must be a date")
    primary_by_ticker: dict[str, SecurityIdentity] = {}
    for row in sec_rows:
        existing = primary_by_ticker.get(row.ticker)
        if existing is not None and existing != row:
            raise ValueError(f"conflicting primary ticker: {row.ticker}")
        primary_by_ticker[row.ticker] = row

    prior_by_entity: dict[str, list[SecurityIdentity]] = defaultdict(list)
    directory_by_ticker: dict[str, list[SecurityIdentity]] = defaultdict(list)
    for row in listing_rows:
        if any(source.startswith("prior-manifest:") for source in row.source_ids):
            prior_by_entity[row.entity_id].append(row)
        else:
            directory_by_ticker[row.ticker].append(row)

    current_entity_counts: dict[str, int] = defaultdict(int)
    for row in sec_rows:
        current_entity_counts[row.entity_id] += 1

    merged: list[SecurityIdentity] = []
    conflicts: list[ReferenceConflict] = []
    for ticker, original in sorted(primary_by_ticker.items()):
        row = original
        prior = [item for item in prior_by_entity.get(row.entity_id, ()) if item.valid_to is None]
        if len(prior) == 1 and current_entity_counts[row.entity_id] == 1:
            old = prior[0]
            row = replace(
                row,
                security_id=old.security_id,
                valid_from=old.valid_from,
                aliases=_aliases(row.ticker, (*old.aliases, *row.aliases)),
                source_ids=tuple(sorted(set((*old.source_ids, *row.source_ids)))),
            )
        matches = directory_by_ticker.pop(ticker, [])
        if len(matches) > 1 and len(
            {(item.exchange, item.instrument_type, item.exclusion_reasons) for item in matches}
        ) > 1:
            combined = _ambiguous((row, *matches), ticker)
            merged.append(combined)
            conflicts.append(
                ReferenceConflict(
                    "conflicting_listing_class", ticker, combined.source_ids
                )
            )
            continue
        if matches:
            listing = matches[0]
            instrument = (
                listing.instrument_type
                if row.instrument_type == "COMMON_STOCK"
                else row.instrument_type
            )
            if (
                row.instrument_type != "COMMON_STOCK"
                and listing.instrument_type != row.instrument_type
            ):
                combined = _ambiguous((row, listing), ticker)
                merged.append(combined)
                conflicts.append(
                    ReferenceConflict(
                        "conflicting_listing_class", ticker, combined.source_ids
                    )
                )
                continue
            row = replace(
                row,
                exchange=listing.exchange,
                instrument_type=instrument,
                aliases=_aliases(ticker, (*row.aliases, *listing.aliases)),
                source_ids=tuple(sorted(set((*row.source_ids, *listing.source_ids)))),
                exclusion_reasons=tuple(
                    sorted(set((*row.exclusion_reasons, *listing.exclusion_reasons)))
                ),
            )
            row = _with_eligibility(replace(row, eligible=True))
        merged.append(row)

    for ticker, rows in sorted(directory_by_ticker.items()):
        if len(rows) == 1:
            merged.append(rows[0])
            continue
        combined = _ambiguous(rows, ticker)
        merged.append(combined)
        conflicts.append(
            ReferenceConflict("conflicting_listing_class", ticker, combined.source_ids)
        )
    for entity_id, prior_rows in sorted(prior_by_entity.items()):
        if current_entity_counts.get(entity_id, 0) != 0:
            continue
        for prior in prior_rows:
            if prior.valid_to is not None:
                continue
            if as_of <= prior.valid_from:
                raise ValueError("delisting date must follow listing date")
            merged.append(replace(
                prior,
                valid_to=as_of,
                eligible=False,
                exclusion_reasons=tuple(sorted(set((*prior.exclusion_reasons, "delisted")))),
            ))
    if len(merged) > MAX_REFERENCE_SECURITIES:
        raise ValueError("merged security reference exceeds item bound")
    ordered = tuple(sorted(merged, key=lambda row: (row.ticker, row.security_id)))
    canonical = json.dumps(
        [
            [
                row.security_id,
                row.entity_id,
                row.ticker,
                row.exchange,
                row.instrument_type,
                list(row.aliases),
                list(row.source_ids),
                row.eligible,
                list(row.exclusion_reasons),
            ]
            for row in ordered
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    midnight = datetime.combine(as_of, datetime.min.time(), tzinfo=timezone.utc)
    return ReferenceSnapshot(
        manifest=_manifest(
            canonical,
            source_url="derived://merged-reference",
            retrieved_at=midnight,
            source_timestamp=None,
            source_name="merged-reference",
            security_count=len(ordered),
        ),
        issuers=(),
        securities=ordered,
        conflicts=tuple(conflicts),
    )


def merge_reference_snapshot(
    current: ReferenceSnapshot,
    predecessor_securities: Sequence[SecurityIdentity] | ReferenceSnapshot,
) -> ReferenceSnapshot:
    """Carry forward only unambiguous predecessor identities into fresh SEC data."""
    predecessor_snapshot = (
        predecessor_securities
        if isinstance(predecessor_securities, ReferenceSnapshot)
        else None
    )
    predecessor_rows = (
        predecessor_snapshot.securities
        if predecessor_snapshot is not None
        else predecessor_securities
    )
    marker = "prior-manifest:validated"
    marked = tuple(
        replace(row, source_ids=(*row.source_ids, marker))
        for row in predecessor_rows
    )
    merged = merge_reference_sources(
        current.securities, marked, as_of=current.manifest.retrieved_at.date()
    )
    cleaned = tuple(
        replace(
            row,
            source_ids=tuple(source for source in row.source_ids if source != marker),
        )
        for row in merged.securities
    )
    if predecessor_snapshot is None or not predecessor_snapshot.issuers:
        return replace(current, securities=cleaned, conflicts=merged.conflicts)
    previous = predecessor_snapshot.issuers_by_id
    issuers: list[IssuerIdentity] = []
    for issuer in current.issuers:
        prior = previous.get(issuer.entity_id)
        if prior is None:
            issuers.append(issuer)
            continue
        current_observed = issuer.observed_names or (issuer.canonical_name,)
        dated = list(prior.dated_former_names)
        former = set(prior.former_names)
        for name in prior.observed_names or (prior.canonical_name,):
            if name in current_observed or name in former:
                continue
            end = current.manifest.retrieved_at.date() - timedelta(days=1)
            start = prior.valid_from
            if end >= start:
                dated.append(FormerIssuerName(name, start, end))
                former.add(name)
        issuers.append(replace(
            issuer,
            former_names=tuple(sorted(former)),
            valid_from=min(prior.valid_from, issuer.valid_from),
            source_ids=tuple(sorted(set((*prior.source_ids, *issuer.source_ids)))),
            dated_former_names=tuple(sorted(
                { (row.name, row.valid_from, row.valid_to): row for row in dated }.values(),
                key=lambda row: (row.name, row.valid_from, row.valid_to),
            )),
        ))
    return replace(
        current,
        issuers=tuple(sorted(issuers, key=lambda row: row.entity_id)),
        securities=cleaned,
        conflicts=merged.conflicts,
    )


def refresh_sec_reference(
    client: BoundedHttpClient,
    *,
    previous: ReferenceSnapshot | None = None,
    contact: str | None = None,
) -> ReferenceRefresh:
    """Perform one bounded SEC request and retain a prior snapshot on failure."""
    try:
        user_agent = sec_user_agent(contact)
    except ValueError:
        if previous is None:
            return ReferenceRefresh(None, "reference_unavailable", "CONFIGURATION_MISSING")
        return ReferenceRefresh(previous, "reference_stale", "CONFIGURATION_MISSING")
    request = HttpRequest(
        SEC_COMPANY_TICKERS_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
        timeout_seconds=10,
        max_bytes=MAX_REFERENCE_SOURCE_BYTES,
    )
    try:
        result = client.get(request)
        snapshot = parse_sec_company_tickers(result.body, retrieved_at=result.retrieved_at)
        snapshot = replace(
            snapshot,
            manifest=replace(snapshot.manifest, source_timestamp=result.observed_at),
        )
        return ReferenceRefresh(snapshot, "healthy", None)
    except SourceFailure as exc:
        error_code = exc.code
    except (TypeError, ValueError):
        error_code = "INVALID_REFERENCE"
    if previous is None:
        return ReferenceRefresh(None, "reference_unavailable", error_code)
    return ReferenceRefresh(previous, "reference_stale", error_code)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp_for_date(value: date) -> str:
    return _canonical_timestamp(
        datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    )


def _semantic_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("reference semantic timestamp is invalid") from None
    else:
        raise TypeError("reference semantic timestamp is invalid")
    if parsed.tzinfo is None:
        raise ValueError("reference semantic timestamp must include a timezone")
    return _canonical_timestamp(parsed)


def reference_manifest_semantic_document(row: Mapping[str, object]) -> dict[str, object]:
    """Return the one versioned semantic document hashed by every protocol layer."""
    value = {key: row[key] for key in (
        "id", "reference_version", "revision", "capability_version",
        "taxonomy_version", "source_hash", "valid_from", "valid_to", "manifest",
    )}
    value["valid_from"] = _semantic_timestamp(value["valid_from"])
    value["valid_to"] = _semantic_timestamp(value["valid_to"])
    return {
        "kind": "reference_manifest",
        "semantic_encoding_version": REFERENCE_SEMANTIC_ENCODING_VERSION,
        "value": value,
    }


def security_revision_semantic_document(row: Mapping[str, object]) -> dict[str, object]:
    """Authenticate legacy v1 rows unchanged and bind issuer names in v2."""
    version = row.get("semantic_encoding_version", 1)
    if isinstance(version, bool) or version not in {1, 2}:
        raise ValueError("security semantic encoding version is invalid")
    value = {key: row[key] for key in (
        "revision", "security_id", "entity_id", "ticker", "exchange",
        "instrument_type", "eligible", "exclusion_reasons", "aliases",
        "source_ids", "valid_from", "valid_to",
    )}
    value["valid_from"] = _semantic_timestamp(value["valid_from"])
    value["valid_to"] = _semantic_timestamp(value["valid_to"])
    if version == 2:
        names = row.get("issuer_names")
        if not isinstance(names, Mapping):
            raise ValueError("v2 security revision requires issuer names")
        value["issuer_names"] = _canonical_issuer_names(names)
    return {
        "kind": "security_revision",
        "semantic_encoding_version": version,
        "value": value,
    }


def _name_text(value: object, label: str) -> str:
    return _bounded_text(value, label, 300)


def _name_date(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} is invalid") from None
    return parsed.isoformat()


def _canonical_issuer_names(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != {"canonical_name", "observed_names", "former_names"}:
        raise ValueError("issuer names are invalid")
    canonical = _name_text(value["canonical_name"], "canonical issuer name")
    observed_raw = value["observed_names"]
    former_raw = value["former_names"]
    if not isinstance(observed_raw, (list, tuple)) or not 1 <= len(observed_raw) <= 16 \
            or not isinstance(former_raw, (list, tuple)) or len(former_raw) > 32:
        raise ValueError("issuer names are invalid")
    observed_values = tuple(
        _name_text(name, "observed issuer name") for name in observed_raw
    )
    if len(set(observed_values)) != len(observed_values):
        raise ValueError("observed issuer name is duplicated")
    observed = tuple(sorted(observed_values))
    if canonical not in observed:
        raise ValueError("canonical issuer name must be observed")
    former: list[dict[str, str]] = []
    for row in former_raw:
        if not isinstance(row, Mapping) or set(row) != {"name", "valid_from", "valid_to"}:
            raise ValueError("former issuer name is invalid")
        start = _name_date(row["valid_from"], "former issuer name start")
        end = _name_date(row["valid_to"], "former issuer name end")
        if start > end:
            raise ValueError("former issuer name interval is invalid")
        name = _name_text(row["name"], "former issuer name")
        if name in observed:
            raise ValueError("former issuer name is currently observed")
        former.append({"name": name, "valid_from": start, "valid_to": end})
    if len({(row["name"], row["valid_from"], row["valid_to"]) for row in former}) != len(former):
        raise ValueError("former issuer name is duplicated")
    return {
        "canonical_name": canonical,
        "observed_names": list(observed),
        "former_names": sorted(former, key=lambda row: (
            row["name"], row["valid_from"], row["valid_to"]
        )),
    }


def _issuer_names(row: IssuerIdentity) -> dict[str, object]:
    observed = row.observed_names or (row.canonical_name,)
    dated = row.dated_former_names
    if row.former_names and {value.name for value in dated} != set(row.former_names):
        raise ValueError("v2 issuer former names require dated intervals")
    return _canonical_issuer_names({
        "canonical_name": row.canonical_name,
        "observed_names": list(observed),
        "former_names": [{
            "name": value.name,
            "valid_from": value.valid_from.isoformat(),
            "valid_to": value.valid_to.isoformat(),
        } for value in dated],
    })


def _security_transfer_row(
    row: SecurityIdentity,
    *,
    issuer: IssuerIdentity | None,
    manifest_id: str,
    run_id: str,
    semantic_encoding_version: int,
) -> dict[str, object]:
    content: dict[str, object] = {
        "revision": 1,
        "security_id": row.security_id,
        "entity_id": row.entity_id,
        "ticker": row.ticker,
        "exchange": row.exchange,
        "instrument_type": row.instrument_type,
        "eligible": row.eligible,
        "exclusion_reasons": list(row.exclusion_reasons),
        "aliases": list(row.aliases),
        "source_ids": list(row.source_ids),
        "valid_from": _timestamp_for_date(row.valid_from),
        "valid_to": _timestamp_for_date(row.valid_to) if row.valid_to is not None else None,
    }
    if semantic_encoding_version == 2:
        if issuer is None or issuer.entity_id != row.entity_id:
            raise ValueError("v2 security revision requires its issuer identity")
        content["semantic_encoding_version"] = 2
        content["issuer_names"] = _issuer_names(issuer)
    content_hash = hashlib.sha256(
        _canonical_json(security_revision_semantic_document(content))
    ).hexdigest()
    revision_id = str(
        uuid5(UUID(run_id), f"reference-security:{row.security_id}:{content_hash}")
    )
    return {
        "id": revision_id,
        "manifest_id": manifest_id,
        **content,
        "content_hash": content_hash,
    }


def _chunk_hash(entries: Sequence[Mapping[str, object]]) -> str:
    lines = [
        "\x1f".join(
            (str(row["security_id"]), str(row["id"]), str(row["content_hash"]))
        )
        for row in entries
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def build_reference_transfer(
    snapshot: ReferenceSnapshot,
    *,
    run_id: str,
    capability_version: int,
    taxonomy_version: int,
    predecessor_manifest_id: str | None = None,
    capability_id: str = "sec_company_tickers_universe",
    reference_status: Literal["healthy", "reference_stale"] = "healthy",
    semantic_encoding_version: int = 1,
) -> ReferenceTransfer:
    """Build deterministic bounded calls for the protected transfer protocol."""
    try:
        run_uuid = UUID(run_id)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("run_id must be a UUID") from None
    if str(run_uuid) != run_id:
        raise ValueError("run_id must be a canonical UUID")
    if predecessor_manifest_id is not None:
        try:
            predecessor_uuid = UUID(predecessor_manifest_id)
        except (TypeError, ValueError, AttributeError):
            raise ValueError("predecessor_manifest_id must be a UUID") from None
        if str(predecessor_uuid) != predecessor_manifest_id:
            raise ValueError("predecessor_manifest_id must be a canonical UUID")
    if (
        isinstance(capability_version, bool)
        or not isinstance(capability_version, int)
        or not 1 <= capability_version <= 10_000
        or isinstance(taxonomy_version, bool)
        or not isinstance(taxonomy_version, int)
        or not 1 <= taxonomy_version <= 10_000
    ):
        raise ValueError("reference versions must be bounded positive integers")
    if isinstance(semantic_encoding_version, bool) or semantic_encoding_version not in {1, 2}:
        raise ValueError("reference semantic encoding version is invalid")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", capability_id):
        raise ValueError("capability_id is invalid")
    if not 1 <= len(snapshot.securities) <= MAX_REFERENCE_SECURITIES:
        raise ValueError("reference snapshot exceeds item bound")
    ordered = tuple(sorted(snapshot.securities, key=lambda row: row.security_id))
    if len({row.security_id for row in ordered}) != len(ordered):
        raise ValueError("reference snapshot has duplicate security identities")
    issuer_by_id = {row.entity_id: row for row in snapshot.issuers}
    if len(issuer_by_id) != len(snapshot.issuers):
        raise ValueError("reference snapshot has duplicate issuer identities")
    if semantic_encoding_version == 2 and set(row.entity_id for row in ordered) - set(issuer_by_id):
        raise ValueError("v2 reference snapshot is missing issuer identities")
    version = (
        f"sec:{snapshot.manifest.retrieved_at.date().isoformat()}:"
        f"{snapshot.manifest.source_hash[:16]}:{run_id.split('-', 1)[0]}"
    )
    manifest_id = str(uuid5(run_uuid, f"reference-manifest:{version}"))
    entries = tuple(
        _security_transfer_row(
            row,
            issuer=issuer_by_id.get(row.entity_id),
            manifest_id=manifest_id,
            run_id=run_id,
            semantic_encoding_version=semantic_encoding_version,
        )
        for row in ordered
    )

    groups: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for entry in entries:
        candidate = [*current, entry]
        probe = {
            "manifest_id": manifest_id,
            "chunk_index": 511,
            "chunk_count": 512,
            "entries": candidate,
            "chunk_hash": _chunk_hash(candidate),
        }
        maximum_rows = 88 if semantic_encoding_version == 2 else 200
        if len(candidate) > maximum_rows or len(_canonical_json(probe)) > 192 * 1024:
            if not current:
                raise ValueError("one reference entry exceeds the chunk byte bound")
            groups.append(current)
            current = [entry]
        else:
            current = candidate
    if current:
        groups.append(current)
    if not 1 <= len(groups) <= 512:
        raise ValueError("reference transfer exceeds chunk bound")

    chunks: list[dict[str, object]] = []
    for index, group in enumerate(groups):
        chunk = {
            "manifest_id": manifest_id,
            "chunk_index": index,
            "chunk_count": len(groups),
            "entries": group,
            "chunk_hash": _chunk_hash(group),
        }
        if len(_canonical_json(chunk)) > 192 * 1024:
            raise ValueError("reference chunk exceeds encoded byte bound")
        chunks.append(chunk)
    root_hash = hashlib.sha256(
        "".join(str(chunk["chunk_hash"]) for chunk in chunks).encode()
    ).hexdigest()
    manifest_body = {
        "coverage_status": snapshot.manifest.coverage_status,
        "reference_status": reference_status,
        "source_url": snapshot.manifest.source_url,
        "source_retrieved_at": _canonical_timestamp(snapshot.manifest.retrieved_at),
        "source_timestamp": _canonical_timestamp(snapshot.manifest.source_timestamp)
        if snapshot.manifest.source_timestamp is not None else None,
        "parser_version": snapshot.manifest.parser_version,
        "security_count": len(ordered),
        "conflict_count": len(snapshot.conflicts),
        "symbol_directory_status": "disabled_pending_https_and_terms_review",
    }
    if semantic_encoding_version == 2:
        manifest_body["format_version"] = 2
    manifest = {
        "id": manifest_id,
        "reference_version": version,
        "revision": 1,
        "capability_version": capability_version,
        "taxonomy_version": taxonomy_version,
        "source_hash": snapshot.manifest.source_hash,
        "valid_from": _canonical_timestamp(snapshot.manifest.retrieved_at),
        "valid_to": None,
        "manifest": manifest_body,
    }
    manifest["content_hash"] = hashlib.sha256(
        _canonical_json(reference_manifest_semantic_document(manifest))
    ).hexdigest()
    begin = {
        "manifest": manifest,
        "capability_id": capability_id,
        "chunk_count": len(chunks),
        "security_count": len(ordered),
        "root_hash": root_hash,
        "predecessor_manifest_id": predecessor_manifest_id,
    }
    if len(_canonical_json(begin)) > 192 * 1024:
        raise ValueError("reference begin payload exceeds encoded byte bound")
    return ReferenceTransfer(begin=begin, chunks=tuple(chunks))


def _reference_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} is invalid")
    return parsed.date()


def _reference_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError(f"{label} is invalid")
    return parsed.astimezone(timezone.utc)


def reference_snapshot_from_rows(
    manifest: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
) -> ReferenceSnapshot:
    """Hydrate one complete, pinned reference page set without recontacting SEC."""
    if not isinstance(manifest, Mapping) or not isinstance(rows, Sequence):
        raise TypeError("reference hydration requires manifest and security rows")
    body = manifest.get("manifest")
    if not isinstance(body, Mapping):
        raise ValueError("reference manifest body is invalid")
    format_version = body.get("format_version", 1)
    if isinstance(format_version, bool) or format_version not in {1, 2}:
        raise ValueError("reference format version is invalid")
    if not 1 <= len(rows) <= MAX_REFERENCE_SECURITIES:
        raise ValueError("reference rows exceed item bound")
    securities: list[SecurityIdentity] = []
    name_documents: dict[str, dict[str, object]] = {}
    entity_sources: dict[str, set[str]] = defaultdict(set)
    entity_dates: dict[str, list[date]] = defaultdict(list)
    seen_security_ids: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("reference security row is invalid")
        version = raw.get("semantic_encoding_version", 1)
        if version != format_version:
            raise ValueError("reference row format does not match manifest")
        document = security_revision_semantic_document(raw)
        expected_hash = hashlib.sha256(_canonical_json(document)).hexdigest()
        if raw.get("content_hash") not in (None, expected_hash):
            raise ValueError("reference security content hash mismatch")
        security_id = _bounded_text(raw.get("security_id"), "security ID", 160)
        entity_id = _bounded_text(raw.get("entity_id"), "entity ID", 160)
        if security_id in seen_security_ids:
            raise ValueError("reference security identity is duplicated")
        seen_security_ids.add(security_id)
        aliases = raw.get("aliases")
        sources = raw.get("source_ids")
        exclusions = raw.get("exclusion_reasons")
        if not all(isinstance(value, (list, tuple)) for value in (aliases, sources, exclusions)):
            raise ValueError("reference security row arrays are invalid")
        valid_from = _reference_date(raw.get("valid_from"), "security valid_from")
        valid_to_raw = raw.get("valid_to")
        valid_to = _reference_date(valid_to_raw, "security valid_to") if valid_to_raw is not None else None
        if valid_to is not None and valid_to < valid_from:
            raise ValueError("reference security interval is invalid")
        eligible = raw.get("eligible")
        if not isinstance(eligible, bool):
            raise ValueError("reference security eligibility is invalid")
        securities.append(SecurityIdentity(
            security_id=security_id,
            entity_id=entity_id,
            ticker=_ticker(raw.get("ticker"), "security ticker"),
            exchange=(
                _bounded_text(raw.get("exchange"), "security exchange", 40)
                if raw.get("exchange") is not None else None
            ),
            instrument_type=_bounded_text(raw.get("instrument_type"), "security instrument", 40),
            valid_from=valid_from,
            valid_to=valid_to,
            aliases=tuple(_bounded_text(value, "security alias", 32) for value in aliases),
            source_ids=tuple(_bounded_text(value, "security source", 256) for value in sources),
            eligible=eligible,
            exclusion_reasons=tuple(_bounded_text(value, "security exclusion", 128) for value in exclusions),
        ))
        entity_sources[entity_id].update(str(value) for value in sources)
        entity_dates[entity_id].append(valid_from)
        if format_version == 2:
            names = raw.get("issuer_names")
            if not isinstance(names, Mapping):
                raise ValueError("v2 reference row lacks issuer names")
            canonical_names = _canonical_issuer_names(names)
            previous = name_documents.setdefault(entity_id, canonical_names)
            if previous != canonical_names:
                raise ValueError("same entity must have identical issuer names")
    issuers: list[IssuerIdentity] = []
    if format_version == 2:
        for entity_id in sorted(name_documents):
            names = name_documents[entity_id]
            former = tuple(
                FormerIssuerName(
                    str(value["name"]),
                    date.fromisoformat(str(value["valid_from"])),
                    date.fromisoformat(str(value["valid_to"])),
                )
                for value in names["former_names"]  # type: ignore[index]
            )
            cik = entity_id.removeprefix("sec-cik:") if re.fullmatch(
                r"sec-cik:[0-9]{10}", entity_id
            ) else None
            issuers.append(IssuerIdentity(
                entity_id=entity_id,
                cik=cik,
                canonical_name=str(names["canonical_name"]),
                former_names=tuple(value.name for value in former),
                valid_from=min(entity_dates[entity_id]),
                valid_to=None,
                source_ids=tuple(sorted(entity_sources[entity_id])),
                observed_names=tuple(names["observed_names"]),  # type: ignore[arg-type]
                dated_former_names=former,
            ))
    retrieved = _reference_datetime(manifest.get("valid_from"), "manifest valid_from")
    source_timestamp_value = body.get("source_timestamp")
    reference_manifest = ReferenceManifest(
        reference_version=_bounded_text(
            manifest.get("reference_version"), "reference version", 128
        ),
        source_hash=_bounded_text(manifest.get("source_hash"), "reference source hash", 64),
        source_url=_bounded_text(body.get("source_url"), "reference source URL", 2_048),
        retrieved_at=retrieved,
        source_timestamp=(
            _reference_datetime(source_timestamp_value, "reference source timestamp")
            if source_timestamp_value is not None else None
        ),
        parser_version=int(body.get("parser_version", 1)),
        coverage_status="scope_not_guaranteed",
        reference_status=str(body.get("reference_status", "healthy")),  # type: ignore[arg-type]
        security_count=len(securities),
    )
    return ReferenceSnapshot(
        manifest=reference_manifest,
        issuers=tuple(issuers),
        securities=tuple(sorted(securities, key=lambda row: (row.ticker, row.security_id))),
        limitations=() if format_version == 2 else ("issuer_names_unavailable",),
    )


__all__ = [
    "Eligibility",
    "FormerIssuerName",
    "IssuerIdentity",
    "MAX_REFERENCE_SECURITIES",
    "REFERENCE_SEMANTIC_ENCODING_VERSION",
    "ReferenceConflict",
    "ReferenceManifest",
    "ReferenceRefresh",
    "ReferenceSnapshot",
    "ReferenceTransfer",
    "SEC_COMPANY_TICKERS_URL",
    "SecurityIdentity",
    "build_reference_transfer",
    "eligible_for_research",
    "merge_reference_sources",
    "merge_reference_snapshot",
    "parse_sec_company_tickers",
    "parse_symbol_directory",
    "refresh_sec_reference",
    "reference_snapshot_from_rows",
    "reference_manifest_semantic_document",
    "security_revision_semantic_document",
]
