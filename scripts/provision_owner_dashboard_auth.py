#!/usr/bin/env python3
"""Provision exactly one confirmed Supabase Auth owner without logging identity or secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from typing import Callable, Mapping
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PROJECT_HOST = re.compile(r"^[a-z0-9]{20}\.supabase\.co$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)


def validate_configuration(project_url: str, owner_email: str, service_key: str) -> tuple[str, str, str]:
    parsed = urlparse(project_url)
    normalized_email = owner_email.strip().lower()
    key_kind = service_key.startswith("sb_secret_") or service_key.startswith("eyJ")
    if (
        parsed.scheme != "https"
        or not PROJECT_HOST.fullmatch(parsed.hostname or "")
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
        or project_url != f"https://{parsed.netloc}"
    ):
        raise ValueError("project URL must be an exact Supabase origin")
    if len(normalized_email) > 254 or not EMAIL.fullmatch(normalized_email):
        raise ValueError("owner email is invalid")
    if len(service_key) < 40 or not key_kind or any(character.isspace() for character in service_key):
        raise ValueError("service credential is invalid")
    return project_url, normalized_email, service_key


def _request(method: str, url: str, headers: Mapping[str, str], body: bytes | None):
    request = Request(url, method=method, headers=dict(headers), data=body)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def _parse(status: int, body: bytes) -> dict[str, object]:
    if status < 200 or status >= 300:
        raise RuntimeError("Supabase Auth admin request failed")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RuntimeError("Supabase Auth admin response is malformed") from error
    if not isinstance(value, dict):
        raise RuntimeError("Supabase Auth admin response is malformed")
    return value


def _verified_owner(users: object, owner_email: str) -> dict[str, object] | None:
    if not isinstance(users, list):
        raise RuntimeError("Supabase Auth user inventory is malformed")
    if len(users) == 0:
        return None
    if len(users) != 1 or not isinstance(users[0], dict):
        raise RuntimeError("Auth must contain exactly one confirmed owner")
    user = users[0]
    confirmed = user.get("email_confirmed_at") or user.get("confirmed_at")
    if (
        str(user.get("email", "")).strip().lower() != owner_email
        or not UUID.fullmatch(str(user.get("id", "")))
        or not isinstance(confirmed, str)
        or not confirmed
    ):
        raise RuntimeError("Auth must contain exactly one confirmed owner")
    return user


def provision_owner_account(
    project_url: str,
    owner_email: str,
    service_key: str,
    *,
    requester: Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]] = _request,
) -> dict[str, object]:
    project_url, owner_email, service_key = validate_configuration(project_url, owner_email, service_key)
    headers = {
        "apikey": service_key,
        "authorization": f"Bearer {service_key}",
        "content-type": "application/json",
    }
    inventory_url = f"{project_url}/auth/v1/admin/users?page=1&per_page=2"

    status, body = requester("GET", inventory_url, headers, None)
    initial = _parse(status, body)
    owner = _verified_owner(initial.get("users"), owner_email)
    action = "already_present"
    if owner is None:
        create_body = json.dumps({"email": owner_email, "email_confirm": True}, separators=(",", ":")).encode()
        create_status, create_response = requester(
            "POST", f"{project_url}/auth/v1/admin/users", headers, create_body,
        )
        _parse(create_status, create_response)
        verify_status, verify_body = requester("GET", inventory_url, headers, None)
        verified = _parse(verify_status, verify_body)
        owner = _verified_owner(verified.get("users"), owner_email)
        if owner is None:
            raise RuntimeError("Auth owner creation has no verification receipt")
        action = "created"

    owner_id = str(owner["id"])
    return {
        "status": "verified",
        "action": action,
        "auth_user_count": 1,
        "owner_id_digest": hashlib.sha256(owner_id.encode()).hexdigest()[:16],
        "owner_email_digest": hashlib.sha256(owner_email.encode()).hexdigest()[:16],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-url", required=True)
    arguments = parser.parse_args()
    email = os.environ.get("DASHBOARD_OWNER_EMAIL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    receipt = provision_owner_account(arguments.project_url, email, service_key)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
