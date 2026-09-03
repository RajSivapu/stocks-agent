#!/usr/bin/env python3
"""Export, age-encrypt, verify, and upload one private stock-agent recovery backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg

from ops.backup.verify_archive import (
    DATASET_SPECS,
    SCHEMA_VERSION,
    ArchiveValidationError,
    build_archive,
    canonical_json_bytes,
)


AGE_RECIPIENT_RE = re.compile(r"^age1[0-9a-z]{48,100}$")
R2_KEY_RE = re.compile(r"^stock-agent/[a-z0-9/_-]+\.age$")
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
KEY_MATERIAL_NAMES = {
    "APP_RATE_LIMIT_PEPPER",
    "APP_STEP_UP_PEPPER",
    "EVIDENCE_SIGNING_KEY",
    "TELEGRAM_PAIRING_PEPPER",
}


class BackupFailed(RuntimeError):
    """The backup did not complete every fail-closed step."""


def _rpc(connection: psycopg.Connection, function: str, request: Mapping[str, Any]) -> dict[str, Any]:
    row = connection.execute(
        f"SELECT machine.{function}(%s::jsonb)",
        (json.dumps(dict(request), separators=(",", ":")),),
    ).fetchone()
    if row is None or not isinstance(row[0], dict):
        raise BackupFailed("backup RPC returned an unexpected response")
    return row[0]


def collect_archive(
    connection: psycopg.Connection,
    *,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = exported_at or datetime.now(timezone.utc)
    catalog = _rpc(
        connection,
        "backup_export_catalog",
        {"schema_version": SCHEMA_VERSION},
    )
    if catalog.get("schema_version") != SCHEMA_VERSION or not isinstance(catalog.get("tables"), list):
        raise BackupFailed("backup catalog does not match this exporter")
    included = {
        item.get("name")
        for item in catalog["tables"]
        if isinstance(item, dict) and item.get("disposition") == "include"
    }
    if included != set(DATASET_SPECS):
        raise BackupFailed("backup catalog dataset set does not match this exporter")

    datasets: dict[str, list[dict[str, Any]]] = {}
    for dataset in sorted(DATASET_SPECS):
        response = _rpc(
            connection,
            "backup_export_dataset",
            {"schema_version": SCHEMA_VERSION, "dataset": dataset},
        )
        if response.get("dataset") != dataset or not isinstance(response.get("rows"), list):
            raise BackupFailed(f"backup dataset {dataset} returned an unexpected response")
        datasets[dataset] = response["rows"]

    identities = _rpc(
        connection,
        "backup_export_dataset",
        {"schema_version": SCHEMA_VERSION, "dataset": "identity_recovery"},
    )
    if identities.get("dataset") != "identity_recovery" or not isinstance(identities.get("rows"), list):
        raise BackupFailed("identity recovery export returned an unexpected response")
    return build_archive(
        datasets=datasets,
        identity_rows=identities["rows"],
        exported_at=observed_at,
    )


def _wipe_file(path: Path) -> None:
    try:
        if path.exists() and path.is_file():
            size = path.stat().st_size
            with path.open("r+b", buffering=0) as output:
                output.write(b"\0" * size)
                output.flush()
                os.fsync(output.fileno())
    finally:
        path.unlink(missing_ok=True)


def _assert_age_ciphertext(path: Path, plaintext: bytes) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise BackupFailed("age did not create ciphertext")
    size = path.stat().st_size
    if size <= len(b"age-encryption.org/v1\n") or size > MAX_ARCHIVE_BYTES + 1024 * 1024:
        raise BackupFailed("age ciphertext size is invalid")
    with path.open("rb") as source:
        prefix = source.read(4096)
    if not prefix.startswith(b"age-encryption.org/v1\n"):
        raise BackupFailed("age ciphertext header is invalid")
    sensitive_fragments = [plaintext[: min(64, len(plaintext))]] if plaintext else []
    if any(fragment and fragment in prefix for fragment in sensitive_fragments):
        raise BackupFailed("age output contains plaintext")
    os.chmod(path, 0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"ciphertext_sha256": digest, "bytes": size}


def encrypt_payload(
    plaintext: bytes,
    *,
    recipient: str,
    destination: Path,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise BackupFailed("age recipient is malformed")
    if not plaintext or len(plaintext) > MAX_ARCHIVE_BYTES:
        raise BackupFailed("backup plaintext size is invalid")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stock-agent-backup-") as directory_name:
        directory = Path(directory_name)
        os.chmod(directory, 0o700)
        source = directory / "archive.json"
        try:
            descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(plaintext)
                output.flush()
                os.fsync(output.fileno())
            result = runner(
                [
                    "age",
                    "--encrypt",
                    "--recipient",
                    recipient,
                    "--output",
                    str(destination),
                    str(source),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                destination.unlink(missing_ok=True)
                raise BackupFailed("age encryption failed")
            return _assert_age_ciphertext(destination, plaintext)
        finally:
            _wipe_file(source)


def _r2_endpoint(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".r2.cloudflarestorage.com")
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise BackupFailed("R2 endpoint is malformed")
    return value.rstrip("/")


def upload_ciphertext(
    client: Any,
    *,
    bucket: str,
    object_key: str,
    ciphertext: Path,
    metadata: Mapping[str, Any],
) -> None:
    if not bucket or len(bucket) > 63 or not R2_KEY_RE.fullmatch(object_key):
        raise BackupFailed("R2 destination is malformed")
    safe_metadata = {
        "schema-version": str(SCHEMA_VERSION),
        "ciphertext-sha256": str(metadata["ciphertext_sha256"]),
    }
    with ciphertext.open("rb") as body:
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=body,
            ContentType="application/octet-stream",
            Metadata=safe_metadata,
        )
    head = client.head_object(Bucket=bucket, Key=object_key)
    if (
        int(head.get("ContentLength", -1)) != ciphertext.stat().st_size
        or head.get("Metadata", {}).get("ciphertext-sha256") != metadata["ciphertext_sha256"]
    ):
        raise BackupFailed("R2 ciphertext verification failed")


def prune_objects(client: Any, *, bucket: str, prefix: str, keep: int) -> list[str]:
    if keep < 1 or keep > 366 or not prefix.startswith("stock-agent/") or not prefix.endswith("/"):
        raise BackupFailed("R2 retention policy is malformed")
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix)
    if response.get("IsTruncated"):
        raise BackupFailed("R2 retention listing was truncated")
    objects = sorted(
        response.get("Contents", []),
        key=lambda item: (item.get("LastModified"), item.get("Key")),
        reverse=True,
    )
    removed: list[str] = []
    for item in objects[keep:]:
        key = item.get("Key")
        if not isinstance(key, str) or not key.startswith(prefix) or not key.endswith(".age"):
            raise BackupFailed("R2 retention encountered an unexpected object")
        client.delete_object(Bucket=bucket, Key=key)
        removed.append(key)
    return removed


def object_keys(now: datetime) -> tuple[str, str | None]:
    value = now.astimezone(timezone.utc)
    stamp = value.strftime("%Y-%m-%dT%H-%M-%SZ")
    daily = f"stock-agent/daily/{value:%Y/%m}/{stamp}.age"
    weekly = f"stock-agent/weekly/{value:%Y}/{stamp}.age" if value.weekday() == 6 else None
    return daily, weekly


def validated_key_material(raw: str, *, generated_at: datetime) -> dict[str, Any]:
    try:
        supplied = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BackupFailed("key material JSON is malformed") from error
    if not isinstance(supplied, dict) or set(supplied) != KEY_MATERIAL_NAMES:
        raise BackupFailed("key material set does not match the recovery contract")
    for name, value in supplied.items():
        if not isinstance(value, str) or len(value.encode()) < 32 or len(value) > 16_384:
            raise BackupFailed(f"key material value for {name} is malformed")
    return {
        "format": "stock-agent-key-material",
        "version": 1,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "continuity_values": {name: supplied[name] for name in sorted(supplied)},
        "rotate_after_restore": [
            "AGENT_DATABASE_URL",
            "BACKUP_DATABASE_URL",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "SCHEDULER_DATABASE_URL",
            "SCHEDULER_WEBHOOK_SECRET",
            "STOCK_AGENT_SUPABASE_SERVICE_ROLE_KEY",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_DATABASE_URL",
            "TELEGRAM_WEBHOOK_SECRET",
        ],
    }


def key_material_object_key(now: datetime) -> str:
    value = now.astimezone(timezone.utc)
    return f"stock-agent/key-material/{value:%Y/%m}/{value:%Y-%m-%dT%H-%M-%SZ}.age"


def record_backup_success(
    connection: psycopg.Connection,
    *,
    exported_at: datetime,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = _rpc(connection, "backup_record_success", {
        "schema_version": SCHEMA_VERSION,
        "exported_at": exported_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ciphertext_bytes": metadata["bytes"],
        "ciphertext_digest": metadata["ciphertext_sha256"],
    })
    if receipt.get("status") != "recorded" or set(receipt) != {"status", "last_success_at"}:
        raise BackupFailed("backup success receipt was rejected")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url-env", default="BACKUP_DATABASE_URL")
    parser.add_argument("--age-recipient-env", default="BACKUP_AGE_RECIPIENT")
    parser.add_argument("--r2-endpoint-env", default="R2_ENDPOINT")
    parser.add_argument("--r2-access-key-env", default="R2_ACCESS_KEY_ID")
    parser.add_argument("--r2-secret-key-env", default="R2_SECRET_ACCESS_KEY")
    parser.add_argument("--r2-bucket-env", default="R2_BACKUP_BUCKET")
    parser.add_argument("--key-material-env", default="BACKUP_KEY_MATERIAL_JSON")
    parser.add_argument("--skip-upload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        database_url = os.environ.get(args.database_url_env, "")
        recipient = os.environ.get(args.age_recipient_env, "")
        if not database_url:
            raise BackupFailed("backup database URL is not configured")
        observed_at = datetime.now(timezone.utc)
        key_material = validated_key_material(
            os.environ.get(args.key_material_env, ""), generated_at=observed_at
        )
        with psycopg.connect(database_url, autocommit=False) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.execute("SET LOCAL statement_timeout = '120s'")
            archive = collect_archive(connection, exported_at=observed_at)
            connection.rollback()
        metadata = encrypt_payload(
            canonical_json_bytes(archive), recipient=recipient, destination=args.output
        )
        key_material_path = args.output.with_name("stock-agent-key-material.age")
        key_material_metadata = encrypt_payload(
            canonical_json_bytes(key_material),
            recipient=recipient,
            destination=key_material_path,
        )
        uploaded: list[str] = []
        if not args.skip_upload:
            import boto3

            endpoint = _r2_endpoint(os.environ.get(args.r2_endpoint_env, ""))
            client = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=os.environ.get(args.r2_access_key_env, ""),
                aws_secret_access_key=os.environ.get(args.r2_secret_key_env, ""),
                region_name="auto",
            )
            bucket = os.environ.get(args.r2_bucket_env, "")
            daily, weekly = object_keys(observed_at)
            for key in (daily, weekly):
                if key:
                    upload_ciphertext(
                        client,
                        bucket=bucket,
                        object_key=key,
                        ciphertext=args.output,
                        metadata=metadata,
                    )
                    uploaded.append(key)
            upload_ciphertext(
                client,
                bucket=bucket,
                object_key=key_material_object_key(observed_at),
                ciphertext=key_material_path,
                metadata=key_material_metadata,
            )
            uploaded.append(key_material_object_key(observed_at))
            prune_objects(client, bucket=bucket, prefix="stock-agent/daily/", keep=14)
            prune_objects(client, bucket=bucket, prefix="stock-agent/weekly/", keep=4)
            prune_objects(client, bucket=bucket, prefix="stock-agent/key-material/", keep=14)
            with psycopg.connect(database_url, autocommit=False) as connection:
                connection.execute("SET LOCAL statement_timeout = '10s'")
                record_backup_success(
                    connection,
                    exported_at=observed_at,
                    metadata=metadata,
                )
                connection.commit()
        print(json.dumps({
            "status": "encrypted_and_verified",
            "schema_version": SCHEMA_VERSION,
            "ciphertext_sha256": metadata["ciphertext_sha256"],
            "bytes": metadata["bytes"],
            "uploaded_objects": len(uploaded),
            "key_material_ciphertext_sha256": key_material_metadata["ciphertext_sha256"],
        }, sort_keys=True))
        key_material_path.unlink(missing_ok=True)
        return 0
    except (ArchiveValidationError, BackupFailed, OSError, psycopg.Error) as error:
        try:
            args.output.with_name("stock-agent-key-material.age").unlink(missing_ok=True)
        except OSError:
            pass
        print(f"backup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
