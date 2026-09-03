#!/usr/bin/env python3
"""Create one trusted invite-only owner without sending Supabase's default invite email."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID


MAX_RESPONSE_BYTES = 64 * 1024
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def canonical_project_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("Supabase URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".supabase.co")
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Supabase URL must be a canonical HTTPS project URL")
    return value.rstrip("/")


def canonical_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) < 3 or len(email) > 254 or not EMAIL_RE.fullmatch(email):
        raise ValueError("invitation email is malformed")
    return email


def authorize_invitation(custom_smtp_verified: bool) -> None:
    if not custom_smtp_verified:
        raise ValueError("custom SMTP must be verified before invitations are enabled")


def _bounded_json(response: Any) -> dict[str, Any]:
    declared = response.headers.get("Content-Length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
        raise RuntimeError("Supabase response exceeded the size limit")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Supabase response exceeded the size limit")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Supabase returned malformed JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("Supabase returned an unexpected response")
    return value


def _request(
    url: str,
    service_role_key: str,
    *,
    method: str,
    body: dict[str, Any] | None,
    opener: Callable[..., Any],
    parse_response: bool = True,
) -> dict[str, Any]:
    request = Request(
        url,
        method=method,
        data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with opener(request, timeout=20) as response:
        return _bounded_json(response) if parse_response else {}


def _created_user_id(value: dict[str, Any]) -> UUID:
    candidate = value.get("id")
    if candidate is None and isinstance(value.get("user"), dict):
        candidate = value["user"].get("id")
    try:
        return UUID(str(candidate))
    except (TypeError, ValueError) as error:
        raise RuntimeError("Supabase did not return a valid new-user identity") from error


def create_invited_user(
    supabase_url: str,
    service_role_key: str,
    email: str,
    receipt_pepper: str,
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, str]:
    endpoint = canonical_project_url(supabase_url)
    canonical = canonical_email(email)
    if len(service_role_key) < 16 or len(service_role_key) > 16_384:
        raise ValueError("offline service-role credential is missing or malformed")
    if len(receipt_pepper.encode()) < 32:
        raise ValueError("invitation receipt pepper must contain at least 32 bytes")

    created = _request(
        f"{endpoint}/auth/v1/admin/users",
        service_role_key,
        method="POST",
        body={"email": canonical, "email_confirm": True},
        opener=opener,
    )
    owner_id = _created_user_id(created)
    try:
        initialized = _request(
            f"{endpoint}/rest/v1/rpc/operator_initialize_invited_user",
            service_role_key,
            method="POST",
            body={"p_owner_id": str(owner_id)},
            opener=opener,
        )
        if initialized.get("status") != "invited":
            raise RuntimeError("profile initialization was not acknowledged")
    except Exception as error:
        try:
            _request(
                f"{endpoint}/auth/v1/admin/users/{owner_id}",
                service_role_key,
                method="DELETE",
                body=None,
                opener=opener,
                parse_response=False,
            )
        except Exception:
            pass
        raise RuntimeError("invitation profile initialization failed; Auth rollback was attempted") from error

    observed_at = datetime.now(timezone.utc).isoformat()
    receipt = hmac.new(
        receipt_pepper.encode(),
        f"stock-agent-invite-v1:{canonical}:{owner_id}:{observed_at}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"status": "invited", "receipt": receipt, "created_at": observed_at}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--smtp-verified", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--supabase-url-env", default="STOCK_AGENT_SUPABASE_URL")
    parser.add_argument("--service-role-key-env", default="STOCK_AGENT_SERVICE_ROLE_KEY")
    parser.add_argument("--receipt-pepper-env", default="INVITE_RECEIPT_PEPPER")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        authorize_invitation(args.smtp_verified)
        canonical_email(args.email)
        if args.dry_run:
            print(json.dumps({"status": "validated", "network_calls": 0}, sort_keys=True))
            return 0
        result = create_invited_user(
            os.environ.get(args.supabase_url_env, ""),
            os.environ.get(args.service_role_key_env, ""),
            args.email,
            os.environ.get(args.receipt_pepper_env, ""),
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"invitation rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
