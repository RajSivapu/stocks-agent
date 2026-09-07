"""Deterministic entity and dated security resolution for market events."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import re
import unicodedata

from lib.intelligence.normalize import SourceItem
from lib.intelligence.universe import IssuerIdentity, ReferenceSnapshot, SecurityIdentity


_US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA", "BATS", "IEX"})
_PUBLIC_ALIAS_RELATIONSHIPS = frozenset({"issuer", "former_name", "subsidiary", "parent"})


@dataclass(frozen=True, slots=True)
class ReviewedAlias:
    alias: str
    entity_id: str
    relationship: str
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class EntityResolution:
    mention: str
    entity_id: str | None
    security_id: str | None
    ticker: str | None
    status: str
    matched_by: str
    eligible: bool
    limitations: tuple[str, ...] = ()


def _normalized_name(value: object) -> str:
    return re.sub(
        r"[^a-z0-9]+", " ", unicodedata.normalize("NFKC", str(value)).casefold()
    ).strip()


def _as_of(item: SourceItem) -> date:
    value = item.published_at or item.effective_at or item.retrieved_at
    return value.date()


def _active(row: SecurityIdentity, as_of: date) -> bool:
    return row.valid_from <= as_of and (row.valid_to is None or as_of <= row.valid_to)


def _security_status(row: SecurityIdentity) -> tuple[str, bool, tuple[str, ...]]:
    foreign = row.exchange is not None and row.exchange not in _US_EXCHANGES
    foreign = foreign or "foreign_only" in row.exclusion_reasons
    if foreign:
        return "foreign_only", False, tuple(dict.fromkeys((*row.exclusion_reasons, "foreign_only")))
    if not row.eligible:
        return "resolved", False, row.exclusion_reasons or ("security_ineligible",)
    return "resolved", True, ()


def _resolved(mention: str, matched_by: str, row: SecurityIdentity) -> EntityResolution:
    status, eligible, limitations = _security_status(row)
    return EntityResolution(
        mention=mention,
        entity_id=row.entity_id,
        security_id=row.security_id,
        ticker=row.ticker,
        status=status,
        matched_by=matched_by,
        eligible=eligible,
        limitations=limitations,
    )


def _mentions(item: SourceItem, issuers: Sequence[IssuerIdentity]) -> tuple[str, ...]:
    metadata = item.metadata if isinstance(item.metadata, Mapping) else {}
    values: list[object] = []
    for key in (
        "organization_names", "organizations", "mentioned_organizations", "organization_name",
    ):
        value = metadata.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values.extend(value)
    if values:
        return tuple(dict.fromkeys(
            " ".join(str(value).split())[:300] for value in values if str(value).strip()
        ))
    text = _normalized_name(f"{item.title} {item.summary}")
    discovered: list[str] = []
    for issuer in issuers:
        for name in (
            issuer.canonical_name,
            *issuer.observed_names,
            *issuer.former_names,
            *(row.name for row in issuer.dated_former_names),
        ):
            normalized = _normalized_name(name)
            if len(normalized) >= 4 and re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", text
            ):
                discovered.append(name)
    return tuple(dict.fromkeys(discovered))


def _cik_token(value: str) -> str | None:
    match = re.fullmatch(r"(?:sec-)?cik:([0-9]{1,10})", value.casefold())
    return match.group(1).zfill(10) if match else None


def _rows_for_entity(
    entity_id: str,
    securities: Sequence[SecurityIdentity],
    as_of: date,
) -> tuple[SecurityIdentity, ...]:
    current = [row for row in securities if row.entity_id == entity_id and _active(row, as_of)]
    unique = {row.security_id: row for row in current}
    return tuple(unique[key] for key in sorted(unique))


def _ambiguous(mention: str, matched_by: str) -> EntityResolution:
    return EntityResolution(
        mention, None, None, None, "ambiguous", matched_by, False,
        ("multiple_reference_matches",),
    )


def resolve_entities(
    item: SourceItem,
    reference: ReferenceSnapshot,
    *,
    aliases: Sequence[ReviewedAlias] = (),
) -> tuple[EntityResolution, ...]:
    """Resolve explicit IDs, unique names, then reviewed aliases without guessing."""
    if not isinstance(item, SourceItem) or not isinstance(reference, ReferenceSnapshot):
        raise TypeError("entity resolution requires a canonical item and reference snapshot")
    as_of = _as_of(item)
    securities = reference.securities
    issuers = reference.issuers
    results: list[EntityResolution] = []
    seen_security: set[str] = set()
    seen_mentions: set[str] = set()

    def add_resolution(row: EntityResolution) -> None:
        if row.security_id is not None:
            if row.security_id in seen_security:
                return
            seen_security.add(row.security_id)
        else:
            key = f"{row.status}:{_normalized_name(row.mention)}"
            if key in seen_mentions:
                return
            seen_mentions.add(key)
        results.append(row)

    # Explicit stable security IDs or dated ticker aliases are authoritative inputs.
    for token in item.security_ids:
        stable = [row for row in securities if row.security_id.casefold() == token.casefold() and _active(row, as_of)]
        matches = stable or [
            row for row in securities
            if _active(row, as_of)
            and token.upper() in {row.ticker.upper(), *(alias.upper() for alias in row.aliases)}
        ]
        unique = {row.security_id: row for row in matches}
        if len(unique) > 1:
            add_resolution(_ambiguous(token, "explicit_security_id"))
        elif unique:
            add_resolution(_resolved(token, "explicit_security_id", next(iter(unique.values()))))
        else:
            add_resolution(EntityResolution(
                token, None, None, None, "unresolved", "explicit_security_id", False,
                ("security_not_in_dated_reference",),
            ))

    # Explicit entity IDs and CIKs come before prose names.
    issuer_by_id = {row.entity_id: row for row in issuers}
    issuer_by_cik = {row.cik: row for row in issuers if row.cik is not None}
    for token in item.entity_ids:
        cik = _cik_token(token)
        issuer = issuer_by_cik.get(cik) if cik is not None else issuer_by_id.get(token)
        if issuer is None:
            add_resolution(EntityResolution(
                token, None, None, None, "unresolved", "explicit_cik" if cik else "explicit_entity_id",
                False, ("entity_not_in_dated_reference",),
            ))
            continue
        rows = _rows_for_entity(issuer.entity_id, securities, as_of)
        if not rows:
            add_resolution(EntityResolution(
                token, issuer.entity_id, None, None, "unresolved",
                "explicit_cik" if cik else "explicit_entity_id", False,
                ("no_active_security",),
            ))
        for row in rows:
            add_resolution(_resolved(token, "explicit_cik" if cik else "explicit_entity_id", row))

    canonical: dict[str, list[IssuerIdentity]] = defaultdict(list)
    observed: dict[str, list[IssuerIdentity]] = defaultdict(list)
    former: dict[str, list[IssuerIdentity]] = defaultdict(list)
    for issuer in issuers:
        canonical[_normalized_name(issuer.canonical_name)].append(issuer)
        for name in issuer.observed_names:
            key = _normalized_name(name)
            if key != _normalized_name(issuer.canonical_name):
                observed[key].append(issuer)
        for name in issuer.former_names:
            key = _normalized_name(name)
            dated = tuple(
                row for row in issuer.dated_former_names
                if _normalized_name(row.name) == key
            )
            if not dated or any(
                row.valid_from <= as_of <= row.valid_to for row in dated
            ):
                former[key].append(issuer)
        for row in issuer.dated_former_names:
            key = _normalized_name(row.name)
            if row.valid_from <= as_of <= row.valid_to \
                    and issuer not in former[key]:
                former[key].append(issuer)
    reviewed: dict[str, list[ReviewedAlias]] = defaultdict(list)
    for alias in aliases:
        if not isinstance(alias, ReviewedAlias):
            raise TypeError("reviewed aliases must be typed")
        if alias.valid_from <= as_of and (alias.valid_to is None or as_of <= alias.valid_to):
            reviewed[_normalized_name(alias.alias)].append(alias)

    for mention in _mentions(item, issuers):
        key = _normalized_name(mention)
        matched_by = "canonical_name"
        candidates = canonical.get(key, [])
        if not candidates:
            matched_by = "observed_name"
            candidates = observed.get(key, [])
        if not candidates:
            matched_by = "former_name"
            candidates = former.get(key, [])
        candidates = list({row.entity_id: row for row in candidates}.values())
        if len(candidates) > 1:
            add_resolution(_ambiguous(mention, matched_by))
            continue
        if len(candidates) == 1:
            rows = _rows_for_entity(candidates[0].entity_id, securities, as_of)
            if not rows:
                add_resolution(EntityResolution(
                    mention, candidates[0].entity_id, None, None, "unresolved",
                    matched_by, False, ("no_active_security",),
                ))
            for row in rows:
                add_resolution(_resolved(mention, matched_by, row))
            continue
        alias_rows = reviewed.get(key, [])
        if len(alias_rows) > 1:
            add_resolution(_ambiguous(mention, "reviewed_alias"))
            continue
        if len(alias_rows) == 1:
            alias = alias_rows[0]
            if alias.relationship == "private_issuer" or alias.entity_id.startswith("private:"):
                add_resolution(EntityResolution(
                    mention, alias.entity_id, None, None, "private", "reviewed_alias",
                    False, ("private_entity_has_no_eligible_security",),
                ))
                continue
            if alias.relationship not in _PUBLIC_ALIAS_RELATIONSHIPS:
                add_resolution(EntityResolution(
                    mention, None, None, None, "unresolved", "reviewed_alias",
                    False, ("alias_relationship_does_not_establish_identity",),
                ))
                continue
            rows = _rows_for_entity(alias.entity_id, securities, as_of)
            if rows:
                for row in rows:
                    add_resolution(_resolved(mention, "reviewed_alias", row))
                continue
        add_resolution(EntityResolution(
            mention, None, None, None, "unresolved", "name", False,
            ("entity_not_in_dated_reference",),
        ))

    return tuple(sorted(
        results,
        key=lambda row: (
            0 if row.security_id is not None else 1,
            row.security_id or "",
            row.status,
            _normalized_name(row.mention),
        ),
    ))


__all__ = ["EntityResolution", "ReviewedAlias", "resolve_entities"]
