"""Credential-safe Routine healthcheck for reviewed source capabilities."""

from __future__ import annotations

import json
import pathlib
import ssl
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lib import config, gateway
from lib.intelligence.health import ProbeRequest, build_capability_health


_CONNECTION_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {"User-Agent": "stocks-agent owner healthcheck"}


def _probe(request: ProbeRequest) -> str:
    headers = dict(_DEFAULT_HEADERS)
    headers.update(request.headers)
    response = urllib.request.urlopen(
        urllib.request.Request(request.resolved_url(), headers=headers),
        timeout=_CONNECTION_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        return response.geturl()
    finally:
        response.close()


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
