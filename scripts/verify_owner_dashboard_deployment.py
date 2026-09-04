#!/usr/bin/env python3
"""GET-only production canary for the owner-only dashboard API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


CANARY_METHOD = "GET"
CANARY_ROUTES = (
    "/v1/today",
    "/v1/portfolio",
    "/v1/companion",
    "/v1/alerts",
    "/v1/runs",
    "/v1/system",
)
BOUNDARIES = {
    "owner_only": True,
    "suggestion_only": True,
    "friend_invitations": "disabled",
    "brokerage_authority": "none",
}


def validate_api_boundary(api_url: str, origin: str) -> tuple[str, str]:
    parsed_api = urlparse(api_url)
    parsed_origin = urlparse(origin)
    if (
        parsed_api.scheme != "https"
        or not parsed_api.hostname
        or not parsed_api.hostname.endswith(".supabase.co")
        or parsed_api.path != "/functions/v1/owner-dashboard-api"
        or parsed_api.params
        or parsed_api.query
        or parsed_api.fragment
    ):
        raise ValueError("dashboard API URL is not canonical")
    if (
        parsed_origin.scheme != "https"
        or not parsed_origin.hostname
        or parsed_origin.path
        or parsed_origin.params
        or parsed_origin.query
        or parsed_origin.fragment
        or origin != f"https://{parsed_origin.netloc}"
    ):
        raise ValueError("dashboard origin is not exact")
    return api_url, origin


def validate_owner_payloads(payloads: Mapping[str, Mapping[str, object]]) -> dict[str, object]:
    if set(payloads) != set(CANARY_ROUTES):
        raise RuntimeError("owner canary route set is incomplete")
    observed_boundaries = []
    for route, payload in payloads.items():
        if payload.get("contract_version") != 1:
            raise RuntimeError(f"{route} contract receipt is invalid")
        if payload.get("freshness") not in {"fresh", "stale", "partial", "unavailable"}:
            raise RuntimeError(f"{route} freshness receipt is invalid")
        if "data_as_of" not in payload or not isinstance(payload.get("data"), dict):
            raise RuntimeError(f"{route} receipt metadata is incomplete")
        data = payload["data"]
        boundaries = data.get("boundaries")
        if boundaries is not None:
            if boundaries != BOUNDARIES:
                raise RuntimeError("immutable product boundaries changed")
            observed_boundaries.append(boundaries)
    if not observed_boundaries:
        raise RuntimeError("immutable product boundary receipt is missing")

    today = payloads["/v1/today"]["data"]
    portfolio = today.get("portfolio") if isinstance(today, dict) else None
    if not isinstance(portfolio, dict) or not {"data_as_of", "market_state", "price_sources"} <= set(portfolio):
        raise RuntimeError("portfolio price context receipt is missing")
    runs = payloads["/v1/runs"]["data"]
    completed_runs = runs.get("runs", []) if isinstance(runs, dict) else []
    if not any(isinstance(run, dict) and run.get("status") == "completed" for run in completed_runs):
        raise RuntimeError("a completed run receipt is unavailable")
    alerts = payloads["/v1/alerts"]["data"]
    if isinstance(alerts, dict):
        for alert in alerts.get("alerts", []):
            if isinstance(alert, dict) and alert.get("state") == "delivered" and not alert.get("telegram_message_ids"):
                raise RuntimeError("Telegram delivery claim has no message receipt")
    return {
        "route_count": len(payloads),
        "financial_write_routes": 0,
        "friend_invitations": "disabled",
        "brokerage_authority": "none",
    }


def _request(method: str, url: str, headers: Mapping[str, str]):
    request = Request(url, method=method, headers=dict(headers))
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers.items()), error.read()


def run_http_canary(
    api_url: str,
    origin: str,
    owner_access_token: str,
    *,
    requester: Callable[[str, str, Mapping[str, str]], tuple[int, Mapping[str, str], bytes]] = _request,
) -> dict[str, object]:
    validate_api_boundary(api_url, origin)
    if not owner_access_token or "\n" in owner_access_token or "\r" in owner_access_token:
        raise ValueError("an owner access token is required")
    denied_status, denied_headers, denied_body = requester(CANARY_METHOD, f"{api_url}/v1/meta", {"origin": origin})
    denied_headers = {key.lower(): value for key, value in denied_headers.items()}
    try:
        denied = json.loads(denied_body)
    except json.JSONDecodeError as error:
        raise RuntimeError("unauthenticated denial body is malformed") from error
    if denied_status != 401 or denied.get("error", {}).get("code") != "unauthorized":
        raise RuntimeError("unauthenticated request was not denied")
    if denied_headers.get("access-control-allow-origin") != origin:
        raise RuntimeError("unauthenticated response CORS is not exact")

    payloads = {}
    for route in CANARY_ROUTES:
        status, headers, body = requester(
            CANARY_METHOD,
            f"{api_url}{route}",
            {"origin": origin, "authorization": f"Bearer {owner_access_token}"},
        )
        headers = {key.lower(): value for key, value in headers.items()}
        if status != 200:
            raise RuntimeError(f"owner GET failed for {route}")
        if headers.get("access-control-allow-origin") != origin or headers.get("cache-control") != "no-store":
            raise RuntimeError(f"owner headers are unsafe for {route}")
        try:
            payloads[route] = json.loads(body)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"owner payload is malformed for {route}") from error
    validated = validate_owner_payloads(payloads)
    runs = payloads["/v1/runs"]["data"]["runs"]
    completed = next(run for run in runs if isinstance(run, dict) and run.get("status") == "completed")
    run_id = completed.get("id")
    if not isinstance(run_id, str) or len(run_id) > 64:
        raise RuntimeError("completed run identifier is malformed")
    detail_status, detail_headers, detail_body = requester(
        CANARY_METHOD,
        f"{api_url}/v1/runs/{run_id}",
        {"origin": origin, "authorization": f"Bearer {owner_access_token}"},
    )
    detail_headers = {key.lower(): value for key, value in detail_headers.items()}
    if (
        detail_status != 200
        or detail_headers.get("access-control-allow-origin") != origin
        or detail_headers.get("cache-control") != "no-store"
    ):
        raise RuntimeError("completed run detail GET failed")
    try:
        detail = json.loads(detail_body)
    except json.JSONDecodeError as error:
        raise RuntimeError("completed run detail is malformed") from error
    detail_data = detail.get("data") if isinstance(detail, dict) else None
    if (
        detail.get("contract_version") != 1
        or not isinstance(detail_data, dict)
        or not isinstance(detail_data.get("write_counts"), dict)
        or detail_data.get("incomplete_stages")
        or not isinstance(detail_data.get("run"), dict)
        or detail_data["run"].get("id") != run_id
        or detail_data["run"].get("status") != "completed"
        or not detail_data["run"].get("finished_at")
    ):
        raise RuntimeError("completed run detail lacks a complete receipt chain")
    return {
        "status": "verified",
        "unauthenticated_status": denied_status,
        "owner_route_count": validated["route_count"] + 1,
        "run_id_digest": hashlib.sha256(run_id.encode()).hexdigest()[:16],
        "method": CANARY_METHOD,
        "financial_write_routes": 0,
        "friend_invitations": "disabled",
        "brokerage_authority": "none",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--origin", required=True)
    arguments = parser.parse_args()
    token = os.environ.get("DASHBOARD_OWNER_ACCESS_TOKEN", "").strip()
    receipt = run_http_canary(arguments.api_url, arguments.origin, token)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
