"""Dated, immutable security identities from bounded reference sources.

The SEC company-ticker document is the only enabled reference input in V1.
Symbol-directory parsing is kept offline for fixtures and a future capability
review; it is deliberately not wired to transport.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal
from uuid import UUID, uuid5

from lib.intelligence.http import BoundedHttpClient, HttpRequest, SourceFailure


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_REFERENCE_USER_AGENT = "stocks-agent security-reference/1.0 (rupesh.sivapu@gmail.com)"
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
class IssuerIdentity:
    entity_id: str
    cik: str | None
    canonical_name: str
    former_names: tuple[str, ...]
    valid_from: date
    valid_to: date | None
    source_ids: tuple[str, ...]


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
            )
        elif title != current.canonical_name and title not in current.former_names:
            issuers[entity_id] = replace(
                current, former_names=tuple(sorted((*current.former_names, title)))
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
    predecessor_securities: Sequence[SecurityIdentity],
) -> ReferenceSnapshot:
    """Carry forward only unambiguous predecessor identities into fresh SEC data."""
    marker = "prior-manifest:validated"
    marked = tuple(
        replace(row, source_ids=(*row.source_ids, marker))
        for row in predecessor_securities
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
    return replace(current, securities=cleaned, conflicts=merged.conflicts)


def refresh_sec_reference(
    client: BoundedHttpClient,
    *,
    previous: ReferenceSnapshot | None = None,
) -> ReferenceRefresh:
    """Perform one bounded SEC request and retain a prior snapshot on failure."""
    request = HttpRequest(
        SEC_COMPANY_TICKERS_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": SEC_REFERENCE_USER_AGENT,
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
    """Exclude transfer identities while authenticating every revision field."""
    value = {key: row[key] for key in (
        "revision", "security_id", "entity_id", "ticker", "exchange",
        "instrument_type", "eligible", "exclusion_reasons", "aliases",
        "source_ids", "valid_from", "valid_to",
    )}
    value["valid_from"] = _semantic_timestamp(value["valid_from"])
    value["valid_to"] = _semantic_timestamp(value["valid_to"])
    return {
        "kind": "security_revision",
        "semantic_encoding_version": REFERENCE_SEMANTIC_ENCODING_VERSION,
        "value": value,
    }


def _security_transfer_row(
    row: SecurityIdentity,
    *,
    manifest_id: str,
    run_id: str,
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
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", capability_id):
        raise ValueError("capability_id is invalid")
    if not 1 <= len(snapshot.securities) <= MAX_REFERENCE_SECURITIES:
        raise ValueError("reference snapshot exceeds item bound")
    ordered = tuple(sorted(snapshot.securities, key=lambda row: row.security_id))
    if len({row.security_id for row in ordered}) != len(ordered):
        raise ValueError("reference snapshot has duplicate security identities")
    version = (
        f"sec:{snapshot.manifest.retrieved_at.date().isoformat()}:"
        f"{snapshot.manifest.source_hash[:16]}:{run_id.split('-', 1)[0]}"
    )
    manifest_id = str(uuid5(run_uuid, f"reference-manifest:{version}"))
    entries = tuple(
        _security_transfer_row(row, manifest_id=manifest_id, run_id=run_id)
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
        if len(candidate) > 200 or len(_canonical_json(probe)) > 192 * 1024:
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


__all__ = [
    "Eligibility",
    "IssuerIdentity",
    "MAX_REFERENCE_SECURITIES",
    "REFERENCE_SEMANTIC_ENCODING_VERSION",
    "ReferenceConflict",
    "ReferenceManifest",
    "ReferenceRefresh",
    "ReferenceSnapshot",
    "ReferenceTransfer",
    "SEC_COMPANY_TICKERS_URL",
    "SEC_REFERENCE_USER_AGENT",
    "SecurityIdentity",
    "build_reference_transfer",
    "eligible_for_research",
    "merge_reference_sources",
    "merge_reference_snapshot",
    "parse_sec_company_tickers",
    "parse_symbol_directory",
    "refresh_sec_reference",
    "reference_manifest_semantic_document",
    "security_revision_semantic_document",
]
