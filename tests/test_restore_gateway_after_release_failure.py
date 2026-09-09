import json
import os
import sys

from cryptography.fernet import Fernet

from scripts import release_components
from scripts import restore_gateway_after_release_failure as restore


PROJECT_REF = "hlxpxbxhqctwsqizwjjy"
SESSION_TEMPLATE = (
    "postgresql://postgres.hlxpxbxhqctwsqizwjjy:admin-password-longer-than-24@"
    "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)
CANDIDATE_SHA = "a" * 40


def test_retained_recovery_binds_the_admin_pooler_to_the_native_adapter(
    monkeypatch, tmp_path,
):
    key = Fernet.generate_key()
    state = {
        "release_context": {
            "project_ref": PROJECT_REF,
            "candidate_sha": CANDIDATE_SHA,
            "release_run_id": "123",
            "release_run_attempt": "1",
        },
        "components": {},
    }
    encrypted = Fernet(key).encrypt(json.dumps(state).encode())
    captured = {}

    class Adapter:
        def recover_retained(self, run_id, run_attempt):
            assert (run_id, run_attempt) == (123, 1)
            return encrypted

        def retain(self, _payload):
            pass

    def load_adapter(_context):
        captured["adapter_url"] = os.environ.get("POSTGRES_URL")
        return Adapter()

    class Lease:
        def __init__(self, url, owner, kind):
            captured["lease"] = (url, owner, kind)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def heartbeat(self):
            captured["heartbeat"] = True

        def resolve(self):
            captured["resolved"] = True

    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("RELEASE_RECOVERY_KEY", key.decode())
    monkeypatch.setattr(
        restore.subprocess, "check_output", lambda *_args, **_options: CANDIDATE_SHA,
    )
    monkeypatch.setattr(release_components, "load_native_release_adapter", load_adapter)
    monkeypatch.setattr(
        release_components,
        "recover_components",
        lambda _adapter, recovered, persist: captured.update(recovered=recovered),
    )
    monkeypatch.setattr(restore, "DurableMutationLease", Lease)
    monkeypatch.setattr(sys, "argv", [
        "restore_gateway_after_release_failure.py",
        "--project-ref", PROJECT_REF,
        "--admin-url", SESSION_TEMPLATE,
        "--release-state", str(tmp_path / "release.enc"),
        "--release-run-id", "123",
        "--release-run-attempt", "1",
        "--candidate-sha", CANDIDATE_SHA,
        "--lease-owner", "recovery-123-1",
        "--retain-recovery-artifact",
    ])

    assert restore.main() == 0
    assert captured["adapter_url"] == SESSION_TEMPLATE
    assert captured["lease"] == (SESSION_TEMPLATE, "recovery-123-1", "recovery")
    assert captured["recovered"] == state
    assert captured["heartbeat"] is True
    assert captured["resolved"] is True


def test_manual_recovery_uses_failed_candidate_identity_not_repair_checkout(
    monkeypatch, tmp_path,
):
    key = Fernet.generate_key()
    repair_sha = "b" * 40
    state = {
        "release_context": {
            "project_ref": PROJECT_REF,
            "candidate_sha": CANDIDATE_SHA,
            "release_run_id": "123",
            "release_run_attempt": "1",
        },
        "components": {},
    }
    encrypted = Fernet(key).encrypt(json.dumps(state).encode())
    captured = {}

    class Adapter:
        def recover_retained(self, run_id, run_attempt):
            captured["lookup"] = (run_id, run_attempt)
            return encrypted

        def retain(self, _payload):
            pass

    def load_adapter(context):
        captured["context"] = context
        return Adapter()

    class Lease:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def heartbeat(self):
            pass

        def resolve(self):
            captured["resolved"] = True

    monkeypatch.setenv("RELEASE_RECOVERY_KEY", key.decode())
    monkeypatch.setattr(
        restore.subprocess, "check_output", lambda *_args, **_options: repair_sha,
    )
    monkeypatch.setattr(release_components, "load_native_release_adapter", load_adapter)
    monkeypatch.setattr(
        release_components,
        "recover_components",
        lambda _adapter, _state, persist: None,
    )
    monkeypatch.setattr(restore, "DurableMutationLease", Lease)
    monkeypatch.setattr(sys, "argv", [
        "restore_gateway_after_release_failure.py",
        "--project-ref", PROJECT_REF,
        "--admin-url", SESSION_TEMPLATE,
        "--release-state", str(tmp_path / "release.enc"),
        "--release-run-id", "123",
        "--release-run-attempt", "1",
        "--candidate-sha", CANDIDATE_SHA,
        "--lease-owner", "recovery-123-1",
    ])

    assert restore.main() == 0
    assert captured["lookup"] == (123, 1)
    assert captured["context"]["candidate_sha"] == CANDIDATE_SHA
    assert captured["resolved"] is True
