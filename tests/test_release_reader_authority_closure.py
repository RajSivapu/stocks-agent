from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import scripts.release_reader_authority_closure as closure
from lib.release_reader_closure_contract import (
    CLOSURE_READ_TABLES,
    CLOSURE_READER_CONTRACT,
)
from scripts.deploy_owner_dashboard_api import (
    candidate_migration_manifest,
    normalize_migration_statements,
    reconciliation_baseline_manifest,
)
from scripts.managed_isolated_restore import canonical_json
from scripts.release_reader_authority_closure import (
    CLOSURE_LEASE_OWNER,
    EXPECTED_FUNCTION_AUTHORITY_ISSUES,
    apply_closure_transaction,
    classify_migration_state,
    closure_migration_manifest,
    validate_diagnostic_receipt,
    validate_extension_inventory,
    verify_reader_state,
)
from scripts.protected_evidence import (
    PostgresReadOnlySource,
    ReleaseReaderFunctionAuthorityError,
)
from scripts.release_reader_diagnostic import FORMAT as DIAGNOSTIC_FORMAT


PROJECT_REF = "p" * 20
MAIN_SHA = "a" * 40


def _diagnostic_receipt(main_sha: str = MAIN_SHA) -> dict[str, object]:
    receipt: dict[str, object] = {
        "format": DIAGNOSTIC_FORMAT,
        "main_sha": main_sha,
        "production_binding_sha256": hashlib.sha256(
            (DIAGNOSTIC_FORMAT + "\0" + PROJECT_REF).encode()
        ).hexdigest(),
        "credential": {"status": "valid"},
        "preflight": {
            "status": "failed",
            "error_code": "function_authority_mismatch",
            "function_authority": {
                "issue_count": len(EXPECTED_FUNCTION_AUTHORITY_ISSUES),
                "issues": [dict(item) for item in EXPECTED_FUNCTION_AUTHORITY_ISSUES],
                "truncated": False,
            },
        },
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        canonical_json(receipt).encode()
    ).hexdigest()
    return receipt


def test_diagnostic_validator_accepts_only_the_exact_self_hashed_failure():
    receipt = _diagnostic_receipt()

    assert validate_diagnostic_receipt(receipt, PROJECT_REF, MAIN_SHA) == {
        "receipt_sha256": receipt["receipt_sha256"],
        "issue_count": 16,
    }

    for mutation in (
        lambda value: value.__setitem__("main_sha", "b" * 40),
        lambda value: value.__setitem__("production_binding_sha256", "0" * 64),
        lambda value: value["preflight"]["function_authority"].__setitem__(
            "truncated", True
        ),
        lambda value: value["preflight"]["function_authority"]["issues"].pop(),
        lambda value: value.__setitem__("receipt_sha256", "0" * 64),
    ):
        altered = copy.deepcopy(receipt)
        mutation(altered)
        with pytest.raises(RuntimeError, match="diagnostic receipt"):
            validate_diagnostic_receipt(altered, PROJECT_REF, MAIN_SHA)


def _baseline_rows(*, closed: bool = False):
    manifest = candidate_migration_manifest()
    baseline = reconciliation_baseline_manifest()
    suffix = [item for item in manifest if item["version"] > baseline["version"]]
    if not closed:
        suffix = suffix[:-1]
    private = [(baseline["path"], baseline["version"], baseline["sha256"])]
    private.extend((item["path"], item["version"], item["sha256"]) for item in suffix)
    native = [(
        baseline["version"],
        normalize_migration_statements(
            Path(baseline["path"]).read_text(encoding="utf-8")
        ),
    )]
    return manifest, private, native


def _manifest_with_future_migration() -> list[dict[str, str]]:
    manifest = candidate_migration_manifest()
    return [*manifest, {
        "path": "sql/migrations/20261018_future_release.sql",
        "version": "20261018",
        "sha256": "f" * 64,
    }]


def test_migration_classifier_accepts_only_the_closure_as_pending():
    manifest, private, native = _baseline_rows()

    receipt = classify_migration_state(private, native, manifest)

    assert receipt["state"] == "pending"
    assert receipt["pending"] == [manifest[-1]]
    assert receipt["closure"] == manifest[-1]


def test_migration_classifier_accepts_an_exact_already_closed_retry():
    manifest, private, native = _baseline_rows(closed=True)

    receipt = classify_migration_state(private, native, manifest)

    assert receipt["state"] == "already_closed"
    assert receipt["pending"] == []


def test_already_closed_retry_uses_pinned_prefix_when_main_has_later_migrations():
    current, private, native = _baseline_rows(closed=True)
    manifest = _manifest_with_future_migration()

    receipt = classify_migration_state(private, native, manifest)

    assert receipt["state"] == "already_closed"
    assert receipt["closure"] == current[-1]
    assert closure_migration_manifest(manifest) == current[-1]

    private_with_later_production_row = [*private, (
        manifest[-1]["path"], manifest[-1]["version"], manifest[-1]["sha256"],
    )]
    with pytest.raises(RuntimeError, match="private migration ledger"):
        classify_migration_state(
            private_with_later_production_row, native, manifest,
        )


@pytest.mark.parametrize("mutate", (
    lambda private, _native, _manifest: private.pop(-2),
    lambda private, _native, _manifest: private[-1].__class__(
        (private[-1][0], private[-1][1], "0" * 64)
    ),
    lambda _private, native, _manifest: native.append(("20990101", ["SELECT 1"])),
))
def test_migration_classifier_rejects_gaps_hash_drift_and_extra_native_rows(mutate):
    manifest, private, native = _baseline_rows()
    result = mutate(private, native, manifest)
    if isinstance(result, tuple):
        private[-1] = result

    with pytest.raises(RuntimeError, match="migration"):
        classify_migration_state(private, native, manifest)


def test_migration_classifier_accepts_an_exact_normal_private_prefix():
    manifest = candidate_migration_manifest()
    private = [
        (item["path"], item["version"], item["sha256"])
        for item in manifest[:-1]
    ]

    receipt = classify_migration_state(private, [], manifest)

    assert receipt["state"] == "pending"
    assert receipt["pending"] == [manifest[-1]]


def _inventory_rows(role: str) -> list[dict[str, object]]:
    rows = []
    for item in EXPECTED_FUNCTION_AUTHORITY_ISSUES:
        rows.append({
            "role": role,
            "schema_usage": True,
            "function": item["function"],
            "extension": None,
            "language": "sql",
            "security_definer": False,
            "grantable": False,
            "owner": "supabase_admin",
        })
    return rows


def test_extension_inventory_requires_the_exact_issue_set_for_both_roles():
    rows = _inventory_rows("stock_agent_release_reader") + _inventory_rows(
        "stock_agent_release_reader_runtime"
    )

    assert validate_extension_inventory(rows) == {
        "role_count": 2,
        "unexpected_function_count_per_role": 16,
    }

    for altered in (
        rows[:-1],
        [{**row, "grantable": True} if index == 0 else row
         for index, row in enumerate(rows)],
        [{**row, "owner": "stock_agent_release_reader"} if index == 0 else row
         for index, row in enumerate(rows)],
    ):
        with pytest.raises(RuntimeError, match="extension inventory"):
            validate_extension_inventory(altered)


def test_reader_state_distinguishes_the_exact_legacy_issue_from_strict_closure():
    class LegacySource:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            raise ReleaseReaderFunctionAuthorityError(
                [dict(item) for item in EXPECTED_FUNCTION_AUTHORITY_ISSUES], 16
            )

        def __exit__(self, *_args):
            return None

    legacy = verify_reader_state(
        "reader-url", PROJECT_REF, source_factory=LegacySource
    )
    assert legacy["state"] == "legacy_open"
    assert legacy["issue_count"] == 16
    assert len(legacy["authority_sha256"]) == 64

    class StrictSource(LegacySource):
        def __enter__(self):
            return self

        def identity(self):
            return {"read_only": True, "isolated_guard": False}

        def authority_receipt(self):
            return {"status": "verified", "read_table_count": 31}

        def pre_migration_scope(self):
            return {"absent_tables": [], "unreadable_tables": []}

    strict = verify_reader_state(
        "reader-url", PROJECT_REF, source_factory=StrictSource
    )
    assert strict["state"] == "strict_closed"
    assert strict["read_table_count"] == 31
    assert len(strict["authority_sha256"]) == 64


def test_closure_reader_contract_is_frozen_when_current_scope_adds_a_table(
    monkeypatch,
):
    import scripts.protected_evidence as evidence

    monkeypatch.setattr(
        evidence, "READ_TABLES", (*evidence.READ_TABLES, "future_table"),
    )
    source = PostgresReadOnlySource(
        "postgresql://stock_agent_release_reader_runtime:secret@"
        f"db.{PROJECT_REF}.supabase.co/postgres",
        PROJECT_REF,
        pre_migration_baseline=True,
        reader_contract=CLOSURE_READER_CONTRACT,
    )

    assert source._contract_read_tables == CLOSURE_READ_TABLES
    assert "future_table" not in source._contract_read_tables


def test_closure_verifier_requests_the_frozen_reader_contract():
    captured = {}

    class Source:
        def __init__(self, *_args, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def identity(self):
            return {"read_only": True, "isolated_guard": False}

        def authority_receipt(self):
            return {"status": "verified", "read_table_count": 31}

        def pre_migration_scope(self):
            return {"absent_tables": [], "unreadable_tables": []}

    receipt = verify_reader_state(
        "reader-url", PROJECT_REF, source_factory=Source,
    )

    assert receipt["state"] == "strict_closed"
    assert captured == {
        "pre_migration_baseline": True,
        "reader_contract": CLOSURE_READER_CONTRACT,
    }


def test_closure_transaction_executes_only_the_pinned_revoke_and_ledger_insert():
    manifest, private, native = _baseline_rows()
    inventory = _inventory_rows("stock_agent_release_reader") + _inventory_rows(
        "stock_agent_release_reader_runtime"
    )

    class Cursor:
        def __init__(self):
            self.statements = []
            self.result = []
            self.schema_reads = 0

        def execute(self, statement, params=None, **kwargs):
            self.statements.append((str(statement), params, kwargs))
            text = str(statement)
            if "closure_schema_authority" in text:
                self.schema_reads += 1
                usage = self.schema_reads == 1
                self.result = [
                    ("stock_agent_release_reader", usage),
                    ("stock_agent_release_reader_runtime", usage),
                ]
            elif "closure_extension_inventory" in text:
                self.result = inventory
            elif "FROM public.stock_agent_release_migration_ledger FOR UPDATE" in text:
                self.result = private
            elif "FROM supabase_migrations.schema_migrations" in text:
                self.result = native
            elif "closure_exact_ledger" in text:
                closure = manifest[-1]
                self.result = [(closure["path"], closure["version"], closure["sha256"])]
            elif "closure_extension_counts" in text:
                self.result = [
                    ("stock_agent_release_reader", 0, 0),
                    ("stock_agent_release_reader_runtime", 0, 0),
                ]
            else:
                self.result = []
            return self

        def fetchall(self):
            return self.result

    cursor = Cursor()

    receipt = apply_closure_transaction(cursor, manifest=manifest)

    assert receipt["state"] == "applied"
    assert len(receipt["pre_extension_inventory_sha256"]) == 64
    assert len(receipt["post_extension_authority_sha256"]) == 64
    executed = [statement for statement, _params, _kwargs in cursor.statements]
    assert sum(statement.startswith("REVOKE USAGE ON SCHEMA extensions") for statement in executed) == 1
    assert not any("GRANT USAGE ON SCHEMA extensions" in statement for statement in executed)
    assert any(
        statement.startswith(
            "INSERT INTO public.stock_agent_release_migration_ledger"
        )
        for statement in executed
    )
    revoke = next(row for row in cursor.statements if row[0].startswith("REVOKE USAGE"))
    assert revoke[2] == {"prepare": True}

    verify_only = Cursor()
    with pytest.raises(RuntimeError, match="verify-only"):
        apply_closure_transaction(
            verify_only, manifest=manifest, verify_only=True,
        )
    assert not any(
        statement.startswith("REVOKE USAGE")
        or statement.startswith(
            "INSERT INTO public.stock_agent_release_migration_ledger"
        )
        for statement, _params, _kwargs in verify_only.statements
    )


def test_already_closed_transaction_with_later_source_migration_is_read_only():
    current, private, native = _baseline_rows(closed=True)
    manifest = _manifest_with_future_migration()

    class Cursor:
        def __init__(self):
            self.statements = []
            self.result = []

        def execute(self, statement, params=None, **kwargs):
            self.statements.append((str(statement), params, kwargs))
            text = str(statement)
            if "closure_schema_authority" in text:
                self.result = [
                    ("stock_agent_release_reader", False),
                    ("stock_agent_release_reader_runtime", False),
                ]
            elif "closure_extension_inventory" in text:
                self.result = []
            elif "FROM public.stock_agent_release_migration_ledger FOR UPDATE" in text:
                self.result = private
            elif "FROM supabase_migrations.schema_migrations" in text:
                self.result = native
            elif "closure_exact_ledger" in text:
                closure = current[-1]
                self.result = [(
                    closure["path"], closure["version"], closure["sha256"],
                )]
            elif "closure_extension_counts" in text:
                self.result = [
                    ("stock_agent_release_reader", 0, 0),
                    ("stock_agent_release_reader_runtime", 0, 0),
                ]
            else:
                self.result = []
            return self

        def fetchall(self):
            return self.result

    cursor = Cursor()
    receipt = apply_closure_transaction(
        cursor, manifest=manifest, verify_only=True,
    )

    assert receipt["state"] == "already_closed"
    assert receipt["closure"] == current[-1]
    assert not any(
        statement.startswith("REVOKE USAGE")
        or statement.startswith(
            "INSERT INTO public.stock_agent_release_migration_ledger"
        )
        for statement, _params, _kwargs in cursor.statements
    )


def test_closure_authorization_step_is_exactly_the_protected_release_step():
    closure = Path(
        ".github/workflows/release-reader-authority-closure.yml"
    ).read_text()
    release = Path(".github/workflows/owner-dashboard-release.yml").read_text()

    def authorization(workflow: str) -> str:
        return workflow.split(
            "      - name: Authenticate exact reviewed main candidate without candidate code",
            1,
        )[1].split("      - uses: actions/checkout@", 1)[0]

    assert authorization(closure) == authorization(release)


def _closure_args(
    tmp_path: Path, *, diagnostic_main_sha: str = MAIN_SHA,
) -> SimpleNamespace:
    diagnostic = tmp_path / "release-reader-diagnostic.json"
    diagnostic.write_text(
        canonical_json(_diagnostic_receipt(diagnostic_main_sha)) + "\n"
    )
    return SimpleNamespace(
        project_ref=PROJECT_REF,
        main_sha=MAIN_SHA,
        reviewed_sha="b" * 40,
        repository="owner/repository",
        workflow_run_id="101",
        workflow_run_attempt="1",
        pr_ci_workflow_run_id="102",
        ci_workflow_run_id="103",
        pull_request_number="51",
        authorization_kind="owner_comment",
        authorization_id="104",
        diagnostic_workflow_run_id="105",
        diagnostic_workflow_run_attempt="1",
        diagnostic_main_sha=diagnostic_main_sha,
        diagnostic_artifact_id="106",
        diagnostic_artifact_name="production-release-reader-diagnostic-105-1",
        diagnostic_artifact_digest="sha256:" + "c" * 64,
        diagnostic=diagnostic,
        output=tmp_path / "release-reader-authority-closure.json",
        lease_owner=CLOSURE_LEASE_OWNER,
    )


def test_primary_closure_keeps_lease_unresolved_until_artifact_finalization(
    tmp_path, monkeypatch,
):
    args = _closure_args(tmp_path)
    states = iter((
        {"state": "legacy_open", "issue_count": 16, "authority_sha256": "d" * 64},
        {"state": "strict_closed", "read_table_count": 31, "authority_sha256": "e" * 64},
    ))
    monkeypatch.setattr(closure, "validate_release_admin_session_url", lambda *_: None)
    monkeypatch.setattr(closure, "validate_evidence_database_url", lambda *_: None)
    monkeypatch.setattr(closure, "verify_reader_state", lambda *_args, **_kwargs: next(states))
    monkeypatch.setattr(closure, "apply_closure_transaction", lambda _cursor, **_kwargs: {
        "state": "applied", "transaction_state": "verified_before_commit",
    })

    class Lease:
        def __init__(self):
            self.resolve_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def heartbeat(self):
            return None

        def resolve(self):
            self.resolve_calls += 1

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
    receipt = closure.close_release_reader_authority(
        args,
        {"SUPAVISOR_SESSION_URL": "admin", "RELEASE_READONLY_DATABASE_URL": "reader"},
        connector=lambda *_args, **_kwargs: Connection(),
        lease_factory=lambda *_args, **_kwargs: lease,
    )

    assert receipt["lease"]["state"] == "awaiting_artifact_finalization"
    assert lease.resolve_calls == 0
    assert args.output.stat().st_mode & 0o777 == 0o600


def test_ambiguous_commit_response_never_resolves_the_closure_lease(
    tmp_path, monkeypatch,
):
    args = _closure_args(tmp_path)
    monkeypatch.setattr(closure, "validate_release_admin_session_url", lambda *_: None)
    monkeypatch.setattr(closure, "validate_evidence_database_url", lambda *_: None)
    monkeypatch.setattr(closure, "verify_reader_state", lambda *_args, **_kwargs: {
        "state": "legacy_open", "issue_count": 16, "authority_sha256": "d" * 64,
    })
    monkeypatch.setattr(closure, "apply_closure_transaction", lambda _cursor, **_kwargs: {
        "state": "applied", "transaction_state": "verified_before_commit",
    })

    class Lease:
        def __init__(self):
            self.resolve_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def heartbeat(self):
            return None

        def resolve(self):
            self.resolve_calls += 1

    class CursorContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class TransactionContext:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            raise RuntimeError("commit response lost")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def transaction(self):
            return TransactionContext()

        def cursor(self):
            return CursorContext()

    lease = Lease()
    with pytest.raises(RuntimeError, match="commit response lost"):
        closure.close_release_reader_authority(
            args,
            {"SUPAVISOR_SESSION_URL": "admin", "RELEASE_READONLY_DATABASE_URL": "reader"},
            connector=lambda *_args, **_kwargs: Connection(),
            lease_factory=lambda *_args, **_kwargs: lease,
        )

    assert lease.resolve_calls == 0


def test_already_closed_recovery_accepts_an_older_ancestor_diagnostic(
    tmp_path, monkeypatch,
):
    diagnostic_sha = "c" * 40
    args = _closure_args(tmp_path, diagnostic_main_sha=diagnostic_sha)
    states = iter((
        {"state": "strict_closed", "read_table_count": 31,
         "authority_sha256": "d" * 64},
        {"state": "strict_closed", "read_table_count": 31,
         "authority_sha256": "e" * 64},
    ))
    monkeypatch.setattr(closure, "validate_release_admin_session_url", lambda *_: None)
    monkeypatch.setattr(closure, "validate_evidence_database_url", lambda *_: None)
    monkeypatch.setattr(
        closure, "verify_reader_state", lambda *_args, **_kwargs: next(states)
    )

    def verify_only(_cursor, *, verify_only=False):
        assert verify_only is True
        return {
            "state": "already_closed",
            "transaction_state": "verified_before_commit",
        }

    monkeypatch.setattr(closure, "apply_closure_transaction", verify_only)

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

    class Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def heartbeat(self):
            return None

    receipt = closure.close_release_reader_authority(
        args,
        {"SUPAVISOR_SESSION_URL": "admin", "RELEASE_READONLY_DATABASE_URL": "reader"},
        connector=lambda *_args, **_kwargs: Connection(),
        lease_factory=lambda *_args, **_kwargs: Lease(),
    )

    assert receipt["transaction"]["state"] == "already_closed"
    assert receipt["diagnostic"]["main_sha"] == diagnostic_sha


def test_pending_closure_rejects_an_older_diagnostic_before_mutation(
    tmp_path, monkeypatch,
):
    args = _closure_args(tmp_path, diagnostic_main_sha="c" * 40)
    monkeypatch.setattr(closure, "validate_release_admin_session_url", lambda *_: None)
    monkeypatch.setattr(closure, "validate_evidence_database_url", lambda *_: None)
    monkeypatch.setattr(closure, "verify_reader_state", lambda *_args, **_kwargs: {
        "state": "legacy_open", "issue_count": 16,
        "authority_sha256": "d" * 64,
    })

    class Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def heartbeat(self):
            raise AssertionError("heartbeat must follow diagnostic validation")

    with pytest.raises(RuntimeError, match="current-main diagnostic"):
        closure.close_release_reader_authority(
            args,
            {"SUPAVISOR_SESSION_URL": "admin", "RELEASE_READONLY_DATABASE_URL": "reader"},
            connector=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("database mutation must not be reached")
            ),
            lease_factory=lambda *_args, **_kwargs: Lease(),
        )


def test_authority_closure_workflow_is_reviewed_manual_and_narrow():
    path = Path(".github/workflows/release-reader-authority-closure.yml")
    parsed = yaml.safe_load(path.read_text())
    workflow = path.read_text()

    assert parsed["jobs"]["close"]["environment"] == "owner-dashboard-production"
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "group: protected-owner-dashboard-release-production" in workflow
    assert "OWNER_RELEASE_APPROVAL_V1" in workflow
    assert ".github/workflows/owner-dashboard-ci.yml" in workflow
    assert ".github/workflows/production-release-reader-diagnostic.yml" in workflow
    assert "Production release reader diagnostic" in workflow
    assert "function_authority_mismatch" in workflow
    assert "diagnostic_workflow_run_id" in workflow
    assert "SUPAVISOR_SESSION_URL: ${{ secrets.SUPAVISOR_SESSION_URL }}" in workflow
    assert "RELEASE_READONLY_DATABASE_URL: ${{ secrets.RELEASE_READONLY_DATABASE_URL }}" in workflow
    assert "SUPABASE_PROJECT_REF: ${{ secrets.SUPABASE_PROJECT_REF }}" in workflow
    assert "scripts.release_reader_authority_closure" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "release-reader-authority-closure-${{ github.run_id }}-${{ github.run_attempt }}" in workflow
    assert "CLOSURE_LEASE_OWNER=reader-closure-20261017-$CLOSURE_SHA" in workflow
    for forbidden in (
        "SUPABASE_ACCESS_TOKEN", "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_BOT_TOKEN", "GRANT USAGE ON SCHEMA extensions",
    ):
        assert forbidden not in workflow
    for line in workflow.splitlines():
        if "uses: actions/" in line:
            assert len(line.rsplit("@", 1)[1].strip()) == 40


def test_reader_ownership_is_checked_before_function_authority():
    source = Path("scripts/protected_evidence.py").read_text()
    assert source.index('snapshot.get("owned_objects")') < source.index(
        'snapshot.get("function_privileges")'
    )
