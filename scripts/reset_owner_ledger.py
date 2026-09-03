#!/usr/bin/env python3
"""Offline, owner-confirmed ledger reset with a mandatory age-encrypted export."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import psycopg


class ResetRejected(RuntimeError):
    """Raised when any reset precondition or postcondition fails."""


def validate_confirmation(owner_id: UUID, value: str) -> str:
    expected = f"RESET {owner_id}"
    if value != expected:
        raise ValueError("typed reset confirmation does not exactly match the owner")
    return expected


def validate_age_recipient(value: str) -> str:
    if not value.startswith("age1") or len(value) < 56 or len(value) > 200 or not value.islower():
        raise ValueError("age recipient is malformed")
    return value


def encrypt_with_age(plaintext: bytes, destination: Path, recipient: str) -> None:
    validate_age_recipient(recipient)
    if destination.exists():
        raise FileExistsError("encrypted export destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        completed = subprocess.run(
            ["age", "--encrypt", "--recipient", recipient, "--output", str(temporary)],
            input=plaintext,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise ResetRejected("age did not produce the mandatory encrypted export")
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, destination)
    except (OSError, subprocess.SubprocessError) as error:
        raise ResetRejected("age encryption failed") from error
    finally:
        temporary.unlink(missing_ok=True)


def _preview(connection: Any, owner_id: UUID, step_up_receipt_id: UUID) -> dict[str, int]:
    row = connection.execute(
        "SELECT app.operator_preview_ledger_reset(%s, %s)",
        (owner_id, step_up_receipt_id),
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise ResetRejected("ledger reset preview was unavailable")
    expected = {"holdings", "transactions", "commands", "plans", "dry_powder"}
    if set(row[0]) != expected or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in row[0].values()
    ):
        raise ResetRejected("ledger reset preview had an unexpected shape")
    return row[0]


def reset_connection(
    connection: Any,
    *,
    owner_id: UUID,
    step_up_receipt_id: UUID,
    output: Path,
    age_recipient: str,
    confirmation: str,
    encryptor: Callable[[bytes, Path, str], None] = encrypt_with_age,
) -> dict[str, Any]:
    validate_confirmation(owner_id, confirmation)
    validate_age_recipient(age_recipient)
    if output.exists():
        raise FileExistsError("encrypted export destination already exists")
    _preview(connection, owner_id, step_up_receipt_id)
    export_row = connection.execute(
        "SELECT app.export_owner_account(%s, '{}'::jsonb)", (owner_id,)
    ).fetchone()
    export = export_row[0] if export_row else None
    if not isinstance(export, dict) or export.get("format") != "json" or not isinstance(export.get("body"), str):
        raise ResetRejected("account export was unavailable")
    plaintext = export["body"].encode()
    if len(plaintext) > 5 * 1024 * 1024:
        raise ResetRejected("account export exceeded the reviewed size")
    encryptor(plaintext, output, age_recipient)
    if not output.is_file() or output.stat().st_size == 0:
        raise ResetRejected("mandatory encrypted export was not created")
    output.chmod(stat.S_IRUSR | stat.S_IWUSR)
    ciphertext_digest = hashlib.sha256(output.read_bytes()).hexdigest()
    result_row = connection.execute(
        "SELECT app.operator_apply_ledger_reset(%s, %s, %s, %s)",
        (owner_id, step_up_receipt_id, ciphertext_digest, confirmation),
    ).fetchone()
    result = result_row[0] if result_row else None
    if not isinstance(result, dict) or result.get("status") != "reset":
        raise ResetRejected("database did not acknowledge the ledger reset")
    verification = connection.execute(
        "SELECT count(*) FROM app.holdings WHERE owner_id = %s",
        (owner_id,),
    ).fetchone()
    if verification is None or verification[0] != 0:
        raise ResetRejected("empty holding projection was not verified")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", type=UUID, required=True)
    parser.add_argument("--step-up-receipt-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--age-recipient", required=True)
    parser.add_argument("--confirm")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-url-env", default="STOCK_AGENT_OPERATOR_DATABASE_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database_url = os.environ.get(args.db_url_env, "")
        if not database_url:
            raise ResetRejected(f"{args.db_url_env} is not configured")
        with psycopg.connect(database_url, autocommit=False) as connection:
            if args.dry_run:
                counts = _preview(connection, args.owner_id, args.step_up_receipt_id)
                connection.rollback()
                print(json.dumps({"status": "previewed", "row_counts": counts}, sort_keys=True))
                return 0
            if args.confirm is None:
                raise ResetRejected(f"confirmation is required: RESET {args.owner_id}")
            with connection.transaction():
                result = reset_connection(
                    connection,
                    owner_id=args.owner_id,
                    step_up_receipt_id=args.step_up_receipt_id,
                    output=args.output,
                    age_recipient=args.age_recipient,
                    confirmation=args.confirm,
                )
        print(json.dumps({
            "status": result["status"],
            "reset_receipt_id": result.get("reset_receipt_id"),
            "encrypted_export": str(args.output),
        }, sort_keys=True))
        return 0
    except (FileExistsError, OSError, ResetRejected, ValueError) as error:
        print(f"ledger reset rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
