"""Bounded SEC issuer and filing routes with a configured contact identity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import unquote, urlsplit

from lib.edgar import sec_user_agent
from lib.intelligence.http import HttpRequest, HttpResult, SourceFailure

from . import CollectionQuery, SourceAdapter, bounded_text, entity_ids, security_ids


_CIK = re.compile(r"[0-9]{1,10}")
_ACCESSION = re.compile(r"([0-9]{10})-([0-9]{2})-([0-9]{6})")
_DOCUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K", "20-F", "40-F"})
_MAX_FILING_BYTES = 2_000_000
_VISIBLE_BLOCKS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"})
_IGNORED_TAGS = frozenset({
    "script", "style", "noscript", "nav", "iframe", "object", "embed", "svg",
    "ix:hidden", "ix:header", "xbrli:context", "xbrli:unit", "link:schemaRef",
})


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SourceFailure("INVALID_RESPONSE")
    return value.astimezone(timezone.utc)


def _response_hash(payload: object) -> str:
    try:
        canonical = json.dumps(
            payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        )
    except (TypeError, ValueError):
        raise SourceFailure("INVALID_RESPONSE") from None
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe_filename(value: object) -> str:
    if not isinstance(value, str) or _DOCUMENT.fullmatch(value) is None:
        raise SourceFailure("INVALID_RESPONSE")
    if unquote(value) != value or "/" in value or "\\" in value or value in {".", ".."}:
        raise SourceFailure("INVALID_RESPONSE")
    return value


def _safe_exact_url(value: str, *, host: str, path: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise SourceFailure("UNSAFE_URL") from None
    if (
        parsed.scheme != "https" or parsed.hostname != host or port not in (None, 443)
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path != path
        or unquote(parsed.path) != parsed.path or "\\" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        raise SourceFailure("UNSAFE_URL")
    return value


def _parse_date(value: object, *, required: bool) -> date | None:
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str):
        raise SourceFailure("INVALID_RESPONSE")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SourceFailure("INVALID_RESPONSE") from None
    if parsed.isoformat() != value:
        raise SourceFailure("INVALID_RESPONSE")
    return parsed


def _parse_accepted(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SourceFailure("INVALID_RESPONSE")
    try:
        if re.fullmatch(r"[0-9]{14}", value):
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _utc(parsed)
    except (ValueError, TypeError):
        raise SourceFailure("INVALID_RESPONSE") from None


@dataclass(frozen=True, slots=True)
class SecFilingDescriptor:
    issuer_cik: str
    accession_number: str
    form: str
    filing_date: date
    accepted_at: datetime | None
    reporting_period_end: date | None
    primary_document: str
    submissions_url: str
    submissions_response_hash: str
    archive_url: str


@dataclass(frozen=True, slots=True)
class FilingPassage:
    passage: str
    locator: str
    raw_response_hash: str
    normalized_passage_hash: str
    parser_version: str = "sec-visible-passage-v1"


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style", "noscript", "iframe", "object", "embed"}:
            self._ignored += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style", "noscript", "iframe", "object", "embed"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data):
        if not self._ignored and data.strip():
            self.values.append(data)


class _PassageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.ancestors: list[tuple[str, str | None]] = []
        self.active: tuple[str, str, list[str]] | None = None
        self.blocks: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.casefold()
        identity = next((str(value) for key, value in attrs if key.casefold() == "id" and value), None)
        self.ancestors.append((lowered, identity))
        if self.ignored or lowered in _IGNORED_TAGS or lowered.startswith(("xbrli:", "link:")):
            self.ignored += 1
            return
        if lowered in _VISIBLE_BLOCKS:
            locator = ":".join(value for _name, value in self.ancestors if value)[:256]
            self.active = (lowered, locator or lowered, [])

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if self.ignored:
            self.ignored -= 1
        elif self.active is not None and self.active[0] == lowered:
            block_tag, locator, values = self.active
            text = " ".join(" ".join(values).split())
            if text:
                self.blocks.append((block_tag, locator, text))
            self.active = None
        if self.ancestors:
            self.ancestors.pop()

    def handle_data(self, data: str) -> None:
        if not self.ignored and self.active is not None and data.strip():
            self.active[2].append(data)


def validate_sec_submissions(
    payload: object,
    *,
    issuer_cik: str,
    retrieved_at: datetime,
    response_url: str,
) -> tuple[SecFilingDescriptor, ...]:
    """Validate exact current issuer submissions without accession-prefix guessing."""
    cik = _cik(issuer_cik)
    _utc(retrieved_at)
    submissions_path = f"/submissions/CIK{cik}.json"
    _safe_exact_url(response_url, host="data.sec.gov", path=submissions_path)
    if not isinstance(payload, dict):
        raise SourceFailure("INVALID_RESPONSE")
    try:
        response_cik = _cik(str(payload["cik"]))
        filings = payload["filings"]
        recent = filings["recent"]
    except (KeyError, TypeError, SourceFailure):
        raise SourceFailure("INVALID_RESPONSE") from None
    if response_cik != cik or not isinstance(filings, dict) or not isinstance(recent, dict):
        raise SourceFailure("INVALID_RESPONSE")
    required = ("accessionNumber", "filingDate", "reportDate", "form", "primaryDocument")
    optional = ("acceptanceDateTime",)
    if any(not isinstance(recent.get(name), list) for name in required):
        raise SourceFailure("INVALID_RESPONSE")
    lengths = {len(recent[name]) for name in required}
    lengths.update(len(recent[name]) for name in optional if name in recent and isinstance(recent[name], list))
    if len(lengths) != 1:
        raise SourceFailure("INVALID_RESPONSE")
    if any(name in recent and not isinstance(recent[name], list) for name in optional):
        raise SourceFailure("INVALID_RESPONSE")
    count = next(iter(lengths), 0)
    if count > 10_000:
        raise SourceFailure("INVALID_RESPONSE")
    digest = _response_hash(payload)
    rows: list[SecFilingDescriptor] = []
    seen: set[str] = set()
    for index in range(count):
        accession = recent["accessionNumber"][index]
        form = recent["form"][index]
        if not isinstance(accession, str) or _ACCESSION.fullmatch(accession) is None:
            raise SourceFailure("INVALID_RESPONSE")
        if accession in seen:
            raise SourceFailure("INVALID_RESPONSE")
        seen.add(accession)
        if not isinstance(form, str):
            raise SourceFailure("INVALID_RESPONSE")
        base_form = form[:-2] if form.endswith("/A") else form
        if base_form not in _SUPPORTED_FORMS:
            continue
        document = _safe_filename(recent["primaryDocument"][index])
        compact = accession.replace("-", "")
        archive_path = f"/Archives/edgar/data/{int(cik)}/{compact}/{document}"
        archive_url = _safe_exact_url(
            f"https://www.sec.gov{archive_path}", host="www.sec.gov", path=archive_path,
        )
        rows.append(SecFilingDescriptor(
            issuer_cik=cik,
            accession_number=accession,
            form=form,
            filing_date=_parse_date(recent["filingDate"][index], required=True),  # type: ignore[arg-type]
            accepted_at=_parse_accepted(
                recent["acceptanceDateTime"][index] if "acceptanceDateTime" in recent else None
            ),
            reporting_period_end=_parse_date(recent["reportDate"][index], required=False),
            primary_document=document,
            submissions_url=response_url,
            submissions_response_hash=digest,
            archive_url=archive_url,
        ))
    return tuple(sorted(rows, key=lambda row: (row.filing_date, row.accepted_at or datetime.min.replace(tzinfo=timezone.utc), row.accession_number), reverse=True))


def extract_filing_passage(
    raw: bytes,
    *,
    source_url: str,
    content_type: str,
    terms: tuple[str, ...],
) -> FilingPassage:
    """Parse one complete bounded direct HTML document and retain one passage."""
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_FILING_BYTES or b"\x00" in raw:
        raise SourceFailure("INVALID_RESPONSE")
    mime = content_type.split(";", 1)[0].strip().casefold()
    if mime not in {"text/html", "application/xhtml+xml", "application/ixbrl+xml"}:
        raise SourceFailure("INVALID_CONTENT_TYPE")
    upper = raw[:4096].upper()
    if b"%PDF" in upper or b"<SEC-DOCUMENT" in upper or b"<!ENTITY" in raw.upper() or b"SYSTEM " in upper:
        raise SourceFailure("INVALID_RESPONSE")
    try:
        parsed_url = urlsplit(source_url)
        port = parsed_url.port
    except ValueError:
        raise SourceFailure("UNSAFE_URL") from None
    if (
        parsed_url.scheme != "https" or parsed_url.hostname != "www.sec.gov"
        or port not in (None, 443) or parsed_url.username is not None
        or parsed_url.password is not None or parsed_url.query or parsed_url.fragment
        or not parsed_url.path.startswith("/Archives/edgar/data/")
        or unquote(parsed_url.path) != parsed_url.path
    ):
        raise SourceFailure("UNSAFE_URL")
    if not isinstance(terms, tuple) or not terms or any(not isinstance(term, str) or not term.strip() for term in terms):
        raise SourceFailure("INVALID_QUERY")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise SourceFailure("INVALID_RESPONSE") from None
    if not re.search(r"<\s*(?:html|body|div|section|p|table)\b", text, re.I):
        raise SourceFailure("INVALID_RESPONSE")
    parser = _PassageParser()
    try:
        parser.feed(text)
        parser.close()
    except (ValueError, AssertionError):
        raise SourceFailure("INVALID_RESPONSE") from None
    normalized_terms = tuple(" ".join(term.casefold().split()) for term in terms)
    scored = []
    for index, (_tag, _locator, block) in enumerate(parser.blocks):
        folded = block.casefold()
        matches = sum(term in folded for term in normalized_terms)
        operational = int(bool(re.search(r"\b(?:we|our|company)\b", folded) and re.search(
            r"\b(?:manufactur|produc|suppl|operat|own|sell|provide)", folded,
        )))
        if matches:
            scored.append((-operational, -matches, index))
    if not scored:
        raise SourceFailure("PASSAGE_UNRESOLVED")
    selected = min(scored)[2]
    start = selected
    while start > 0 and parser.blocks[start - 1][0].startswith("h"):
        start -= 1
    parts: list[str] = []
    for _tag, _locator, block in parser.blocks[start : min(len(parser.blocks), selected + 5)]:
        candidate = " ".join((*parts, block))
        if len(candidate) > 2_000:
            if not parts:
                raise SourceFailure("PASSAGE_UNRESOLVED")
            break
        parts.append(block)
    passage = " ".join(parts).strip()
    if not passage or len(passage) > 2_000:
        raise SourceFailure("PASSAGE_UNRESOLVED")
    locator = parser.blocks[selected][1][:256]
    return FilingPassage(
        passage=passage,
        locator=locator,
        raw_response_hash=hashlib.sha256(raw).hexdigest(),
        normalized_passage_hash=hashlib.sha256(passage.encode()).hexdigest(),
    )


def _cik(value: object) -> str:
    if (
        not isinstance(value, str)
        or _CIK.fullmatch(value) is None
        or int(value) == 0
    ):
        raise SourceFailure("INVALID_QUERY")
    return value.zfill(10)


def _filing_url(query: CollectionQuery) -> str:
    cik = _cik(query.cik)
    accession = query.accession_number
    document = query.primary_document
    match = _ACCESSION.fullmatch(accession or "")
    if match is None or _DOCUMENT.fullmatch(document or "") is None:
        raise SourceFailure("INVALID_QUERY")
    _safe_filename(document)
    compact_accession = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{compact_accession}/{document}"
    )
    return _safe_exact_url(url, host="www.sec.gov", path=urlsplit(url).path)


class SecEdgarAdapter(SourceAdapter):
    provider = "sec_edgar"
    authority = "official"
    allowed_hosts = frozenset({"www.sec.gov", "data.sec.gov"})
    max_items_per_request = 100

    def _authority(self, query: CollectionQuery) -> str:
        return self.authority

    def _user_agent(self) -> str:
        try:
            contact = self.secret_getter("sec_user_agent_contact")
        except (KeyError, OSError, ValueError):
            raise SourceFailure("CONFIGURATION_MISSING") from None
        try:
            return sec_user_agent(contact)
        except ValueError:
            raise SourceFailure("CONFIGURATION_MISSING")

    def _request(self, query: CollectionQuery) -> HttpRequest:
        if query.capability_id in {None, "sec_issuer_submissions"}:
            cik = _cik(query.cik)
            headers = {"User-Agent": self._user_agent()}
            return HttpRequest(
                f"https://data.sec.gov/submissions/CIK{cik}.json",
                headers=headers,
                max_bytes=2_000_000,
                expected_document="json",
                allowed_redirect_urls=frozenset(),
            )
        if query.capability_id == "sec_filing_document":
            filing_url = _filing_url(query)
            headers = {"User-Agent": self._user_agent()}
            return HttpRequest(
                filing_url,
                headers=headers,
                max_bytes=2_000_000,
                expected_document="html",
                allowed_redirect_urls=frozenset(),
            )
        raise SourceFailure("UNSUPPORTED_QUERY")

    def _decode_response(self, response: HttpResult) -> object:
        content_type = str(response.headers.get("content-type", "")).split(";", 1)[0].lower()
        if content_type in {"application/json", "application/edgar+json"}:
            return super()._decode_response(response)
        if content_type not in {"text/html", "application/xhtml+xml", "application/ixbrl+xml"}:
            raise SourceFailure("INVALID_CONTENT_TYPE")
        if len(response.body) > 2_000_000 or b"\x00" in response.body:
            raise SourceFailure("INVALID_RESPONSE")
        return response.body

    def _request_reference(self, query: CollectionQuery) -> str:
        if query.capability_id == "sec_filing_document":
            return _filing_url(query)
        return f"https://data.sec.gov/submissions/CIK{_cik(query.cik)}.json"

    def _records(self, payload, query, response):
        request_url = self._request_reference(query)
        if response.url != request_url:
            raise SourceFailure("UNSAFE_URL")
        cik = _cik(query.cik)
        if query.capability_id in {None, "sec_issuer_submissions"}:
            try:
                payload_cik = _cik(str(payload.get("cik")))
            except (AttributeError, SourceFailure):
                raise SourceFailure("INVALID_RESPONSE") from None
            if payload_cik != cik:
                raise SourceFailure("INVALID_RESPONSE")
            records = validate_sec_submissions(
                payload, issuer_cik=cik, retrieved_at=response.retrieved_at,
                response_url=request_url,
            )[:min(query.limit, self.max_items_per_request)]
            validated = []
            for record in records:
                validated.append({
                    "upstream_item_id": record.accession_number,
                    "request_url": request_url,
                    "item_url": record.archive_url,
                    "title": f"{record.form} filing {record.accession_number}",
                    "text": f"{record.form} filed {record.filing_date.isoformat()}",
                    "published_at": record.filing_date.isoformat(),
                    "effective_at": record.reporting_period_end.isoformat() if record.reporting_period_end else None,
                    "reporting_at": record.reporting_period_end.isoformat() if record.reporting_period_end else None,
                    "entity_ids": entity_ids((f"cik:{cik}",)),
                    "security_ids": security_ids(query.symbols),
                    "metadata": {
                        "accession_number": record.accession_number,
                        "accepted_at": record.accepted_at.isoformat() if record.accepted_at else None,
                        "filing_date": record.filing_date.isoformat(),
                        "form": record.form,
                        "issuer_cik": record.issuer_cik,
                        "primary_document": record.primary_document,
                        "reporting_period_end": record.reporting_period_end.isoformat() if record.reporting_period_end else None,
                        "submissions_response_hash": record.submissions_response_hash,
                    },
                })
            return validated
        if not isinstance(payload, bytes):
            raise SourceFailure("INVALID_RESPONSE")
        content_type = str(response.headers.get("content-type", ""))
        terms = tuple(dict.fromkeys(
            part for part in re.findall(r"[A-Za-z][A-Za-z0-9 -]{2,80}", query.text)
            if part.strip()
        ))[:16] or (query.text,)
        parsed = extract_filing_passage(
            payload, source_url=request_url, content_type=content_type, terms=terms,
        )
        return [{
            "upstream_item_id": f"{query.accession_number}:{query.primary_document}",
            "request_url": request_url,
            "item_url": request_url,
            "title": f"SEC filing {query.accession_number}",
            "text": parsed.passage,
            "published_at": None,
            "entity_ids": entity_ids((f"cik:{cik}",)),
            "security_ids": security_ids(query.symbols),
            "metadata": {
                "accession_number": query.accession_number,
                "filing_rule_version": "sec-submissions-binding-v1",
                "exposure_kind": "filing",
                "normalized_passage_hash": parsed.normalized_passage_hash,
                "parser_version": parsed.parser_version,
                "primary_document": query.primary_document,
                "raw_response_hash": parsed.raw_response_hash,
                "source_locator": parsed.locator,
            },
        }]


__all__ = [
    "FilingPassage",
    "SecEdgarAdapter",
    "SecFilingDescriptor",
    "extract_filing_passage",
    "validate_sec_submissions",
]
