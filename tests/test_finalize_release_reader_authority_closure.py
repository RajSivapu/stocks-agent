from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

import scripts.finalize_release_reader_authority_closure as finalizer
from scripts.extract_release_reader_authority_closure_artifact import (
    extract_closure_artifact,
)
from scripts.deploy_owner_dashboard_api import candidate_migration_manifest
from scripts.managed_isolated_restore import canonical_json
from scripts.release_reader_authority_closure import (
    CLOSURE_LEASE_OWNER,
    FORMAT as CLOSURE_FORMAT,
)


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40
RUN_ID = "201"
RUN_ATTEMPT = "1"
LEASE_OWNER = CLOSURE_LEASE_OWNER


def _closure_receipt() -> dict[str, object]:
    closure = candidate_migration_manifest()[-1]
    receipt: dict[str, object] = {
        "format": CLOSURE_FORMAT,
        "repository": "owner/repository",
        "main_sha": MAIN_SHA,
        "reviewed_sha": "b" * 40,
        "production_binding_sha256": hashlib.sha256(
            (CLOSURE_FORMAT + "\0" + PROJECT_REF).encode()
        ).hexdigest(),
        "workflow": {"run_id": int(RUN_ID), "run_attempt": int(RUN_ATTEMPT)},
        "authorization": {
            "kind": "owner_comment", "id": 1, "pull_request_number": 2,
            "pr_ci_workflow_run_id": 3, "ci_workflow_run_id": 4,
        },
        "diagnostic": {
            "receipt_sha256": "c" * 64, "issue_count": 16,
            "main_sha": MAIN_SHA,
            "workflow_run_id": 5, "workflow_run_attempt": 1,
            "artifact_id": 6,
            "artifact_name": "production-release-reader-diagnostic-5-1",
            "artifact_digest": "sha256:" + "d" * 64,
        },
        "pre_reader": {
            "state": "legacy_open", "issue_count": 16,
            "authority_sha256": "e" * 64,
        },
        "transaction": {
            "state": "applied", "closure": closure,
            "pre_extension_inventory": {
                "role_count": 2, "unexpected_function_count_per_role": 16,
            },
            "pre_extension_inventory_sha256": "f" * 64,
            "post_extension_function_count": 0,
            "post_extension_relation_count": 0,
            "post_extension_authority_sha256": "1" * 64,
            "transaction_state": "committed",
        },
        "post_reader": {
            "state": "strict_closed", "read_table_count": 31,
            "authority_sha256": "2" * 64,
        },
        "lease": {
            "owner": LEASE_OWNER, "kind": "recovery",
            "state": "awaiting_artifact_finalization",
        },
        "started_at": "2026-09-09T00:00:00+00:00",
        "completed_at": "2026-09-09T00:01:00+00:00",
        "status": "verified",
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode()
    ).hexdigest()
    return receipt


def _args(tmp_path: Path) -> SimpleNamespace:
    receipt_path = tmp_path / "release-reader-authority-closure.json"
    receipt_path.write_text(canonical_json(_closure_receipt()) + "\n")
    return SimpleNamespace(
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        repository="owner/repository",
        workflow_run_id=RUN_ID,
        workflow_run_attempt=RUN_ATTEMPT,
        closure_artifact_id="202",
        closure_artifact_name=f"release-reader-authority-closure-{RUN_ID}-{RUN_ATTEMPT}",
        closure_artifact_digest="sha256:" + "3" * 64,
        closure_receipt=receipt_path,
        output=tmp_path / "release-reader-authority-closure-finalization.json",
        lease_owner=LEASE_OWNER,
    )


def test_finalizer_accepts_only_the_exact_uploaded_primary_receipt(tmp_path):
    args = _args(tmp_path)
    receipt = _closure_receipt()

    verified = finalizer.validate_closure_receipt(receipt, args)

    assert verified == {
        "receipt_sha256": receipt["receipt_sha256"],
        "transaction_state": "applied",
    }
    for mutate in (
        lambda value: value.__setitem__("main_sha", "b" * 40),
        lambda value: value["lease"].__setitem__("state", "resolved"),
        lambda value: value["transaction"].__setitem__(
            "post_extension_function_count", 1
        ),
        lambda value: value.__setitem__("receipt_sha256", "0" * 64),
    ):
        altered = copy.deepcopy(receipt)
        mutate(altered)
        with pytest.raises(RuntimeError, match="closure receipt"):
            finalizer.validate_closure_receipt(altered, args)


def test_finalizer_resolves_only_after_exact_closed_readback(tmp_path, monkeypatch):
    args = _args(tmp_path)
    monkeypatch.setattr(finalizer, "validate_release_admin_session_url", lambda *_: None)
    monkeypatch.setattr(finalizer, "validate_evidence_database_url", lambda *_: None)
    states = iter((
        {"state": "strict_closed", "read_table_count": 31, "authority_sha256": "4" * 64},
        {"state": "strict_closed", "read_table_count": 31, "authority_sha256": "5" * 64},
    ))
    monkeypatch.setattr(
        finalizer, "verify_reader_state", lambda *_args, **_kwargs: next(states)
    )
    def verify_only_transaction(_cursor, *, verify_only=False):
        assert verify_only is True
        return {
            "state": "already_closed",
            "closure": candidate_migration_manifest()[-1],
            "post_extension_function_count": 0,
            "post_extension_relation_count": 0,
            "post_extension_authority_sha256": "6" * 64,
            "transaction_state": "verified_before_commit",
        }

    monkeypatch.setattr(
        finalizer, "apply_closure_transaction", verify_only_transaction,
    )

    class Lease:
        def __init__(self):
            self.events = []

        def __enter__(self):
            self.events.append("acquired")
            return self

        def __exit__(self, *_args):
            self.events.append("released_lock")

        def heartbeat(self):
            self.events.append("heartbeat")

        def resolve(self):
            self.events.append("resolved")

    class Context:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, *_args):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def transaction(self):
            return Context(None)

        def cursor(self):
            return Context(object())

    lease = Lease()
    receipt = finalizer.finalize_closure(
        args,
        {"SUPAVISOR_SESSION_URL": "admin", "RELEASE_READONLY_DATABASE_URL": "reader"},
        connector=lambda *_args, **_kwargs: Connection(),
        lease_factory=lambda *_args, **_kwargs: lease,
    )

    assert receipt["lease"]["state"] == "resolved"
    assert lease.events.index("resolved") > lease.events.index("heartbeat")
    assert args.output.stat().st_mode & 0o777 == 0o600


def test_workflow_uploads_primary_before_finalization_and_final_receipt():
    workflow = Path(
        ".github/workflows/release-reader-authority-closure.yml"
    ).read_text()

    assert workflow.index(
        "Upload immutable release-reader authority closure receipt"
    ) < workflow.index("Finalize closure after immutable artifact upload")
    assert workflow.index(
        "Finalize closure after immutable artifact upload"
    ) < workflow.index("Upload closure finalization receipt")
    assert "steps.closure_artifact.outputs.artifact-id" in workflow
    assert "steps.closure_artifact.outputs.artifact-digest" not in workflow
    assert 'ARTIFACT_DIGEST="$(jq -r .digest <<<"$ARTIFACT")"' in workflow
    assert "finalize_release_reader_authority_closure" in workflow
    assert "extract_release_reader_authority_closure_artifact" in workflow
    assert "immutable-primary/release-reader-authority-closure.json" in workflow


def test_artifact_extractor_rejects_valid_local_bytes_that_differ_from_archive(
    tmp_path,
):
    local_receipt = _closure_receipt()
    artifact_receipt = copy.deepcopy(local_receipt)
    artifact_receipt["completed_at"] = "2026-09-09T00:02:00+00:00"
    artifact_receipt.pop("receipt_sha256")
    artifact_receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json(artifact_receipt).encode()
    ).hexdigest()
    local = tmp_path / "local.json"
    local.write_text(canonical_json(local_receipt) + "\n")
    archive = tmp_path / "artifact.zip"
    payload = canonical_json(artifact_receipt).encode() + b"\n"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("release-reader-authority-closure.json", payload)
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="differs from uploaded artifact"):
        extract_closure_artifact(
            archive, digest, local,
            tmp_path / "immutable-primary" / "release-reader-authority-closure.json",
        )

    local.write_bytes(payload)
    output = tmp_path / "immutable-primary" / "release-reader-authority-closure.json"
    extract_closure_artifact(archive, digest, local, output)
    assert output.read_bytes() == payload
    assert output.stat().st_mode & 0o777 == 0o600
