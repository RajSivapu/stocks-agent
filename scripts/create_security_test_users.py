#!/usr/bin/env python3
"""Create two staging Auth users for live cross-tenant PostgREST attacks."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID


MAX_RESPONSE_BYTES = 64 * 1024


def validate_supabase_url(url: str) -> str:
    try:
        parsed = urlparse(url)
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
    return url.rstrip("/")


def _bounded_json(response: Any) -> dict[str, Any]:
    declared = response.headers.get("Content-Length")
    if declared is not None and int(declared) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Supabase response exceeded the size limit")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Supabase response exceeded the size limit")
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Supabase returned malformed JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("Supabase returned an unexpected response")
    return parsed


def _post_json(
    url: str,
    service_role_key: str,
    payload: dict[str, Any],
    *,
    opener: Callable[..., Any],
) -> dict[str, Any]:
    request = Request(
        url,
        method="POST",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
            "Content-Type": "application/json",
        },
    )
    with opener(request, timeout=20) as response:
        return _bounded_json(response)


def _delete_user(
    endpoint: str,
    service_role_key: str,
    user_id: str,
    *,
    opener: Callable[..., Any],
) -> None:
    request = Request(
        f"{endpoint}/auth/v1/admin/users/{user_id}",
        method="DELETE",
        headers={
            "Authorization": f"Bearer {service_role_key}",
            "apikey": service_role_key,
            "Accept": "application/json",
        },
    )
    with opener(request, timeout=20) as response:
        if len(response.read(MAX_RESPONSE_BYTES + 1)) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Supabase response exceeded the size limit")


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if not normalized or len(normalized) > 320 or "@" not in normalized:
        raise ValueError("security-test email is malformed")
    return normalized


def create_security_users(
    supabase_url: str,
    service_role_key: str,
    email_a: str,
    email_b: str,
    *,
    password_factory: Callable[[], str] = lambda: secrets.token_urlsafe(36),
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    endpoint = validate_supabase_url(supabase_url)
    if not service_role_key or len(service_role_key) < 16:
        raise ValueError("service-role credential is missing or malformed")
    emails = (_validate_email(email_a), _validate_email(email_b))
    if emails[0] == emails[1]:
        raise ValueError("security-test users must have distinct emails")

    users: list[dict[str, Any]] = []
    created_ids: list[str] = []
    try:
        for label, email in zip(("owner_a", "owner_b"), emails, strict=True):
            password = password_factory()
            if not isinstance(password, str) or len(password) < 20:
                raise ValueError("generated security-test password is too short")
            created = _post_json(
                f"{endpoint}/auth/v1/admin/users",
                service_role_key,
                {
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"purpose": "stock-agent-cross-tenant-test"},
                },
                opener=opener,
            )
            try:
                user_id = str(UUID(str(created["id"])))
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError("Supabase did not return a valid test-user ID") from error
            created_ids.append(user_id)
            initialized = _post_json(
                f"{endpoint}/rest/v1/rpc/operator_initialize_invited_user",
                service_role_key,
                {"p_owner_id": user_id},
                opener=opener,
            )
            if initialized.get("status") != "invited":
                raise RuntimeError("security-test profile initialization failed")
            session = _post_json(
                f"{endpoint}/auth/v1/token?grant_type=password",
                service_role_key,
                {"email": email, "password": password},
                opener=opener,
            )
            access_token = session.get("access_token")
            expires_in = session.get("expires_in")
            if not isinstance(access_token, str) or not access_token:
                raise RuntimeError("Supabase did not return a test-user access token")
            if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
                raise RuntimeError("Supabase did not return a valid token lifetime")
            users.append(
                {
                    "label": label,
                    "id": user_id,
                    "email": email,
                    "access_token": access_token,
                    "expires_in": expires_in,
                }
            )
    except Exception:
        for user_id in reversed(created_ids):
            try:
                _delete_user(endpoint, service_role_key, user_id, opener=opener)
            except Exception:
                pass
        raise

    return {
        "version": 1,
        "supabase_url": endpoint,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner_a": {key: value for key, value in users[0].items() if key != "label"},
        "owner_b": {key: value for key, value in users[1].items() if key != "label"},
    }


def delete_security_users(
    supabase_url: str,
    service_role_key: str,
    bundle: dict[str, Any],
    *,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    endpoint = validate_supabase_url(supabase_url)
    if not service_role_key or len(service_role_key) < 16:
        raise ValueError("service-role credential is missing or malformed")
    if bundle.get("version") != 1 or bundle.get("supabase_url") != endpoint:
        raise ValueError("security-test bundle belongs to another project")
    identities: list[str] = []
    for label in ("owner_a", "owner_b"):
        record = bundle.get(label)
        if not isinstance(record, dict):
            raise ValueError("security-test bundle is malformed")
        try:
            identities.append(str(UUID(str(record.get("id")))))
        except (TypeError, ValueError) as error:
            raise ValueError("security-test bundle is malformed") from error
    if identities[0] == identities[1]:
        raise ValueError("security-test bundle identities are not distinct")
    for user_id in identities:
        _delete_user(endpoint, service_role_key, user_id, opener=opener)
    return {"status": "deleted", "count": len(identities)}


def write_private_bundle(destination: Path, bundle: dict[str, Any]) -> None:
    encoded = (json.dumps(bundle, sort_keys=True, indent=2) + "\n").encode()
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        finally:
            raise


def _wipe_private_bundle(path: Path) -> None:
    size = path.stat().st_size
    with path.open("r+b", buffering=0) as output:
        output.write(b"\0" * size)
        output.flush()
        os.fsync(output.fileno())
    path.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email-a")
    parser.add_argument("--email-b")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cleanup-bundle", type=Path)
    parser.add_argument("--supabase-url-env", default="STAGING_SUPABASE_URL")
    parser.add_argument("--service-role-key-env", default="STAGING_SUPABASE_SERVICE_ROLE_KEY")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        url = os.environ.get(args.supabase_url_env, "")
        key = os.environ.get(args.service_role_key_env, "")
        if args.cleanup_bundle:
            if args.output or args.email_a or args.email_b:
                raise ValueError("cleanup cannot be combined with create arguments")
            raw = args.cleanup_bundle.read_bytes()
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("security-test bundle is too large")
            bundle = json.loads(raw)
            if not isinstance(bundle, dict):
                raise ValueError("security-test bundle is malformed")
            result = delete_security_users(url, key, bundle)
            _wipe_private_bundle(args.cleanup_bundle)
            print(json.dumps(result, sort_keys=True))
            return 0
        if not args.email_a or not args.email_b or args.output is None:
            raise ValueError("create mode requires both emails and an output path")
        bundle = create_security_users(url, key, args.email_a, args.email_b)
        write_private_bundle(args.output, bundle)
        print(
            json.dumps(
                {
                    "status": "created",
                    "path": str(args.output),
                },
                sort_keys=True,
            )
        )
        return 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as error:
        print(f"security-user setup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
