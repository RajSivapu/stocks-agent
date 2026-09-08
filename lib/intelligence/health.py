"""Credential-safe capability health planning with injected network probes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from urllib.parse import urlencode

from lib.intelligence.planner import load_source_capabilities
from lib.intelligence.types import SourceCapability


_SEC_CONTACT = "SEC_USER_AGENT_CONTACT"


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    """A reviewed health probe whose secret-bearing inputs are never rendered."""

    capability_id: str
    provider: str
    url: str
    allowed_final_urls: tuple[str, ...]
    headers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({}), repr=False
    )
    query: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def resolved_url(self) -> str:
        if not self.query:
            return self.url
        return f"{self.url}?{urlencode(self.query)}"


def _request(
    capability_id: str,
    provider: str,
    url: str,
    *,
    final_urls: tuple[str, ...] | None = None,
    headers: Mapping[str, str] | None = None,
    query: tuple[tuple[str, str], ...] = (),
) -> ProbeRequest:
    return ProbeRequest(
        capability_id=capability_id,
        provider=provider,
        url=url,
        allowed_final_urls=final_urls or (url,),
        headers=MappingProxyType(dict(headers or {})),
        query=query,
    )


def _static_probe(
    capability_id: str,
    configured_values: Mapping[str, str],
) -> ProbeRequest | None:
    contact = configured_values.get(_SEC_CONTACT, "").strip()
    user_agent = f"stocks-agent owner research contact={contact}"
    routes: dict[str, ProbeRequest] = {
        "sec_company_tickers_universe": _request(
            capability_id,
            "sec_edgar",
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        ),
        "gdelt_theme_search": _request(
            capability_id,
            "gdelt",
            "https://api.gdeltproject.org/api/v2/doc/doc",
            query=(("query", "economic policy"), ("mode", "ArtList"),
                   ("format", "json"), ("maxrecords", "1")),
        ),
        "federal_register_document_search": _request(
            capability_id,
            "federal_register",
            "https://www.federalregister.gov/api/v1/documents.json",
            query=(("per_page", "1"), ("conditions[term]", "economic policy")),
        ),
        "white_house_fact_sheets": _request(
            capability_id, "white_house", "https://www.whitehouse.gov/fact-sheets/"
        ),
        "white_house_presidential_actions": _request(
            capability_id,
            "white_house",
            "https://www.whitehouse.gov/presidential-actions/",
        ),
        "white_house_briefings_statements": _request(
            capability_id,
            "white_house",
            "https://www.whitehouse.gov/briefings-statements/",
        ),
        "white_house_sitemap": _request(
            capability_id,
            "white_house",
            "https://www.whitehouse.gov/sitemap_index.xml",
        ),
        "doe_energy_news_rss": _request(
            capability_id, "doe", "https://www.energy.gov/rss/energygov/2193718"
        ),
        "eia_today_in_energy_rss": _request(
            capability_id, "eia", "https://www.eia.gov/rss/todayinenergy.xml"
        ),
        "eia_press_releases_rss": _request(
            capability_id, "eia", "https://www.eia.gov/rss/press_rss.xml"
        ),
        "defense_releases_rss": _defense_request(capability_id, content_type=9),
        "defense_news_rss": _defense_request(capability_id, content_type=1),
    }
    if capability_id == "alpha_vantage_topic_news":
        key = configured_values.get("ALPHAVANTAGE_API_KEY", "")
        return _request(
            capability_id,
            "alpha_vantage",
            "https://www.alphavantage.co/query",
            query=(("function", "NEWS_SENTIMENT"), ("topics", "technology"),
                   ("limit", "1"), ("apikey", key)),
        )
    return routes.get(capability_id)


def _defense_request(capability_id: str, *, content_type: int) -> ProbeRequest:
    suffix = (
        "/DesktopModules/ArticleCS/RSS.ashx"
        f"?ContentType={content_type}&Site=945&max=10"
    )
    source = f"https://www.defense.gov{suffix}"
    destination = f"https://www.war.gov{suffix}"
    return _request(
        capability_id,
        "dod",
        source,
        final_urls=(source, destination),
    )


def _configuration_name(capability: SourceCapability) -> str | None:
    if capability.provider == "sec_edgar":
        return _SEC_CONTACT
    return capability.required_credential


def _capability_record(
    capability: SourceCapability,
    *,
    status: str,
    configuration_name: str | None,
    configured: bool,
    request: ProbeRequest | None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "provider": capability.provider,
        "enabled": capability.enabled,
        "declared_health": capability.health,
        "allowed_hosts": sorted(capability.allowed_hosts),
        "allowed_path_patterns": list(capability.allowed_path_patterns),
        "required_configuration": configuration_name,
        "configuration_status": (
            "not_required"
            if configuration_name is None
            else "present" if configured else "missing"
        ),
        "status": status,
    }
    if request is not None:
        record["route_host"] = request.url.split("/", 3)[2]
        record["route_path"] = "/" + request.url.split("/", 3)[3].split("?", 1)[0]
    return record


def build_capability_health(
    *,
    configured_values: Mapping[str, str],
    probe: Callable[[ProbeRequest], str],
) -> dict[str, object]:
    """Probe reviewed static routes without returning configuration values."""

    capabilities = load_source_capabilities()
    records: dict[str, dict[str, object]] = {}
    for capability_id, capability in capabilities.items():
        configuration_name = _configuration_name(capability)
        configured = (
            configuration_name is None
            or bool(configured_values.get(configuration_name, "").strip())
        )
        request: ProbeRequest | None = None
        if not capability.enabled:
            status = capability.health
        elif not configured:
            status = "configuration_missing"
        elif capability.query_kind in {"issuer_submissions", "filing_document", "quote"}:
            status = "ready_requires_identifier"
        else:
            request = _static_probe(capability_id, configured_values)
            if request is None:
                status = "unsupported"
            else:
                try:
                    final_url = probe(request)
                    status = "ok" if final_url in request.allowed_final_urls else "source_failed"
                except Exception:
                    status = "source_failed"
        records[capability_id] = _capability_record(
            capability,
            status=status,
            configuration_name=configuration_name,
            configured=configured,
            request=request,
        )

    baseline_statuses = [
        records[capability_id]["status"]
        for capability_id in (
            "sec_company_tickers_universe",
            "gdelt_theme_search",
        )
    ]
    if all(status == "ok" for status in baseline_statuses):
        baseline = "ok"
    elif "configuration_missing" in baseline_statuses:
        baseline = "configuration_missing"
    else:
        baseline = "source_failed"
    return {
        "zero_key_baseline": baseline,
        "capabilities": records,
    }
