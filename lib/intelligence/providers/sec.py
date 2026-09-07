"""Bounded SEC issuer and filing routes with a configured contact identity."""

from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import quote, urlsplit

from lib.edgar import parse_submissions, sec_user_agent
from lib.intelligence.http import HttpRequest, HttpResult, SourceFailure

from . import CollectionQuery, SourceAdapter, bounded_text, entity_ids, security_ids


_CIK = re.compile(r"[0-9]{1,10}")
_ACCESSION = re.compile(r"([0-9]{10})-([0-9]{2})-([0-9]{6})")
_DOCUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}")


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
    if match is None or match.group(1) != cik or _DOCUMENT.fullmatch(document or "") is None:
        raise SourceFailure("INVALID_QUERY")
    compact_accession = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{quote(compact_accession)}/{quote(document)}"
    )


class SecEdgarAdapter(SourceAdapter):
    provider = "sec_edgar"
    authority = "official_issuer_filing"
    allowed_hosts = frozenset({"www.sec.gov", "data.sec.gov"})
    max_items_per_request = 100

    def _authority(self, query: CollectionQuery) -> str:
        if query.capability_id in {None, "sec_issuer_submissions"}:
            return "official_issuer_filing_index"
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
        if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
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
            records = parse_submissions(payload, min(query.limit, self.max_items_per_request))
            validated = []
            for record in records:
                accession = str(record.get("upstream_item_id") or "")
                document = str(record.get("source_url") or "").rsplit("/", 1)[-1]
                match = _ACCESSION.fullmatch(accession)
                if match is None or match.group(1) != cik or _DOCUMENT.fullmatch(document) is None:
                    raise SourceFailure("INVALID_RESPONSE")
                expected = _filing_url(CollectionQuery(
                    text=query.text, symbols=query.symbols, start=query.start, end=query.end,
                    limit=query.limit, cik=cik, capability_id="sec_filing_document",
                    accession_number=accession, primary_document=document,
                ))
                if record.get("source_url") != expected:
                    raise SourceFailure("INVALID_RESPONSE")
                validated.append({
                    **record,
                    "request_url": request_url,
                    "item_url": expected,
                    "reporting_at": record.get("effective_at"),
                    "entity_ids": entity_ids((f"cik:{cik}",)),
                    "security_ids": security_ids(query.symbols),
                })
            return validated
        if not isinstance(payload, bytes):
            raise SourceFailure("INVALID_RESPONSE")
        parser = _VisibleText()
        try:
            parser.feed(payload.decode("utf-8", errors="strict"))
            parser.close()
        except (UnicodeDecodeError, ValueError):
            raise SourceFailure("INVALID_RESPONSE") from None
        text = bounded_text(" ".join(parser.values))
        if not text:
            raise SourceFailure("INVALID_RESPONSE")
        return [{
            "upstream_item_id": f"{query.accession_number}:{query.primary_document}",
            "request_url": request_url,
            "item_url": request_url,
            "title": f"SEC filing {query.accession_number}",
            "text": text,
            "published_at": None,
            "entity_ids": entity_ids((f"cik:{cik}",)),
            "security_ids": security_ids(query.symbols),
            "metadata": {
                "accession_number": query.accession_number,
                "primary_document": query.primary_document,
            },
        }]


__all__ = ["SecEdgarAdapter"]
