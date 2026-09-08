"""Credential-safe Routine healthcheck for reviewed source capabilities."""

from __future__ import annotations

import json
import pathlib
import ssl
import sys
import urllib.request
from urllib.error import HTTPError
from urllib.parse import urljoin
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import config, gateway
from lib.intelligence.health import ProbeRequest, build_capability_health


_CONNECTION_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {"User-Agent": "stocks-agent owner healthcheck"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _probe(request: ProbeRequest) -> str:
    headers = dict(_DEFAULT_HEADERS)
    headers.update(request.headers)
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())
    )
    initial = request.resolved_url()
    current = initial
    for attempt in range(2):
        try:
            response = opener.open(
                urllib.request.Request(current, headers=headers),
                timeout=_CONNECTION_TIMEOUT_SECONDS,
            )
        except HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308} or attempt != 0:
                raise
            response = exc
        try:
            status = int(getattr(response, "status", getattr(response, "code", 200)))
            response_url = response.geturl() or current
            if response_url != current:
                raise ValueError("health probe transport followed an unchecked redirect")
            if status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                destination = urljoin(current, location or "")
                if not location or destination not in request.allowed_final_urls:
                    raise ValueError("health probe redirect is not reviewed")
                current = destination
                headers = {
                    name: value for name, value in headers.items()
                    if name.lower() not in {"authorization", "cookie", "proxy-authorization"}
                    and "token" not in name.lower() and "api" not in name.lower()
                }
                continue
            if not 200 <= status < 300:
                raise ValueError("health probe source failed")
            if current != initial and current not in request.allowed_final_urls:
                raise ValueError("health probe destination is not reviewed")
            return current
        finally:
            response.close()
    raise ValueError("health probe redirect limit exceeded")


def _gateway_health() -> dict[str, str]:
    results: dict[str, str] = {}
    try:
        market_date = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
        started = gateway.call(
            "start_run", {"phase": "on-demand", "market_date": market_date}, dry_run=True
        )
        gateway.call("read_context", {}, run_id=started["data"]["run_id"], dry_run=True)
        results["gateway"] = "ok"
    except Exception as exc:
        results["gateway"] = f"FAIL {type(exc).__name__}"
    try:
        gateway.call("evaluate_alert_rules", {}, dry_run=True)
        results["alerts"] = "ok"
    except Exception as exc:
        results["alerts"] = f"FAIL {type(exc).__name__}"
    return results


def main() -> None:
    configured_values = {
        name: config.optional_secret(key)
        for name, key in (
            ("SEC_USER_AGENT_CONTACT", "sec_user_agent_contact"),
            ("FINNHUB_API_KEY", "finnhub_api_key"),
            ("ALPHAVANTAGE_API_KEY", "alphavantage_api_key"),
            ("EIA_API_KEY", "eia_api_key"),
            ("FRED_API_KEY", "fred_api_key"),
            ("BEA_API_KEY", "bea_api_key"),
        )
    }
    results: dict[str, object] = _gateway_health()
    results.update(
        build_capability_health(configured_values=configured_values, probe=_probe)
    )
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
