import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
import yaml


PROJECT_REF = "p" * 20


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _site_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    files = {
        ".openai/hosting.json": json.dumps({
            "project_id": "appgprj_test",
            "static": {"directory": "dist", "not_found_handling": "single-page-application"},
        }),
        "apps/web/src/main.tsx": "export const app = 'v1';\n",
        "packages/dashboard-contracts/src/index.ts": "export type Status = 'ready';\n",
        "package.json": '{"private":true}\n',
        "package-lock.json": '{"lockfileVersion":3}\n',
    }
    for name, contents in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "site source")
    source_sha = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("documentation only\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "docs")
    return repo, source_sha, _git(repo, "rev-parse", "HEAD")


def _site_receipt(source_sha: str) -> dict[str, object]:
    return {
        "format": "stocks-native-sites-attestation-v1",
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "trust_domain": "codex-native-sites-connector",
        "site": {
            "project_id": "appgprj_test",
            "status": "active",
            "live_url": "https://example.chatgpt.site",
            "latest_version_number": 5,
            "current_user_role": "owner",
            "access_mode": "custom",
            "allowed_owner_count": 1,
            "external_visitor_count": 0,
            "allowed_group_count": 0,
        },
        "active_version": {
            "id": "appgver_test",
            "version_number": 5,
            "source_commit_sha": source_sha,
            "archive_format": "tar",
            "archive_content_hash": "sha256:" + "a" * 64,
            "file_count": 15,
            "size_bytes": 512000,
        },
        "active_deployment": {
            "id": "appgdep_test",
            "version_id": "appgver_test",
            "type": "publish",
            "status": "succeeded",
            "url": "https://example.chatgpt.site",
        },
        "live_bundle": {
            "html_sha256": "b" * 64,
            "script_assets": [{
                "url": "https://example.chatgpt.site/assets/index-test.js",
                "sha256": "c" * 64,
                "bytes": 100,
            }],
            "supabase_project_ref": PROJECT_REF,
            "dashboard_api_url": f"https://{PROJECT_REF}.supabase.co/functions/v1/owner-dashboard-api",
            "project_ref_present": True,
            "api_url_present": True,
        },
    }


def test_native_site_receipt_accepts_docs_only_descendant_and_rejects_web_drift(tmp_path):
    from scripts.attest_existing_v1_runtime import validate_native_site_receipt

    repo, source_sha, candidate_sha = _site_repo(tmp_path)
    verified = validate_native_site_receipt(_site_receipt(source_sha), candidate_sha, PROJECT_REF, repo)
    assert verified["status"] == "verified"
    assert verified["version_number"] == 5
    assert verified["source_commit_sha"] == source_sha

    (repo / "apps/web/src/main.tsx").write_text("export const app = 'changed';\n")
    _git(repo, "add", "apps/web/src/main.tsx")
    _git(repo, "commit", "-qm", "web drift")
    with pytest.raises(RuntimeError, match="Site source differs"):
        validate_native_site_receipt(
            _site_receipt(source_sha), _git(repo, "rev-parse", "HEAD"), PROJECT_REF, repo,
        )


def test_native_site_receipt_rejects_any_non_owner_access(tmp_path):
    from scripts.attest_existing_v1_runtime import validate_native_site_receipt

    repo, source_sha, candidate_sha = _site_repo(tmp_path)
    receipt = _site_receipt(source_sha)
    receipt["site"]["external_visitor_count"] = 1
    with pytest.raises(RuntimeError, match="owner-only"):
        validate_native_site_receipt(receipt, candidate_sha, PROJECT_REF, repo)


def test_native_site_receipt_rejects_stale_or_wrong_backend_binding(tmp_path):
    from scripts.attest_existing_v1_runtime import validate_native_site_receipt

    repo, source_sha, candidate_sha = _site_repo(tmp_path)
    stale = _site_receipt(source_sha)
    stale["captured_at"] = (datetime.now(UTC) - timedelta(hours=25)).isoformat().replace("+00:00", "Z")
    with pytest.raises(RuntimeError, match="fresh"):
        validate_native_site_receipt(stale, candidate_sha, PROJECT_REF, repo)

    wrong_backend = _site_receipt(source_sha)
    wrong_backend["live_bundle"]["supabase_project_ref"] = "q" * 20
    with pytest.raises(RuntimeError, match="backend"):
        validate_native_site_receipt(wrong_backend, candidate_sha, PROJECT_REF, repo)


class FakeFunctionAdapter:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.captured = []

    def capture(self, name):
        self.captured.append(name)
        return self.snapshots[name]


def _function_repo(tmp_path: Path) -> tuple[Path, str, dict[str, dict[str, object]]]:
    repo = tmp_path / "functions"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    lines = ['project_id = "stocks-agent"', ""]
    snapshots = {}
    for index, name in enumerate(("market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio"), start=1):
        source = f"export const version = {index};\n".encode()
        path = repo / "supabase/functions" / name / "index.ts"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
        (path.parent / "index_test.ts").write_text("// not deployed\n")
        lines.extend([
            f'[functions."{name}"]',
            "enabled = true",
            "verify_jwt = false",
            f'entrypoint = "./functions/{name}/index.ts"',
            "",
        ])
        snapshots[name] = {
            "exists": True,
            "identity": f"id-{index}",
            "version": str(index),
            "configuration": {"verify_jwt": False, "entrypoint": "index.ts", "import_map": None},
            "files": {"index.ts": base64.b64encode(source).decode()},
            "values": {},
        }
    (repo / "supabase/config.toml").write_text("\n".join(lines))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "functions")
    return repo, _git(repo, "rev-parse", "HEAD"), snapshots


def test_edge_attestation_compares_downloaded_bytes_and_ignores_only_test_sources(tmp_path):
    from scripts.attest_existing_v1_runtime import attest_edge_functions

    repo, candidate_sha, snapshots = _function_repo(tmp_path)
    adapter = FakeFunctionAdapter(snapshots)
    rows = attest_edge_functions(adapter, candidate_sha, repo)

    assert adapter.captured == ["market-briefing-gateway", "owner-dashboard-api", "telegram-portfolio"]
    assert [row["version"] for row in rows] == ["1", "2", "3"]
    assert all(row["source_file_count"] == 1 for row in rows)

    snapshots["owner-dashboard-api"]["files"]["index.ts"] = base64.b64encode(b"drift\n").decode()
    with pytest.raises(RuntimeError, match="deployed bytes differ"):
        attest_edge_functions(FakeFunctionAdapter(snapshots), candidate_sha, repo)


def test_schema_attestation_requires_every_fixed_relation_and_valid_receipt_hash():
    from scripts.attest_existing_v1_runtime import validate_schema_inventory
    from scripts.inspect_production_schema_baseline import RELATION_PRESENCE

    receipt = {
        "format": "stocks-production-schema-inventory-v1",
        "main_sha": "b" * 40,
        "production_binding_sha256": hashlib.sha256(
            ("stocks-production-schema-inventory-v1\\0" + PROJECT_REF).encode()
        ).hexdigest(),
        "relation_presence": {f"{schema}.{name}": True for schema, name in RELATION_PRESENCE},
        "catalog": {"relations": [{"name": "stock_agent_release_migration_ledger"}]},
        "root_algorithm": "sha256-sorted-row-hashes-v1",
        "protected_roots": [{
            "relation": "stock_agent_release_migration_ledger", "count": 1, "root_sha256": "c" * 64,
        }],
    }
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
    receipt["receipt_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()
    assert validate_schema_inventory(receipt, PROJECT_REF, "b" * 40)["all_required_relations"] is True

    receipt["relation_presence"]["public.analysis_runs"] = False
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(RuntimeError, match="required production relation"):
        validate_schema_inventory(receipt, PROJECT_REF, "b" * 40)


def test_reconciliation_continuity_rejects_any_catalog_or_root_drift():
    from scripts.attest_existing_v1_runtime import validate_reconciliation_continuity
    from scripts.inspect_production_schema_baseline import RELATION_PRESENCE
    from scripts.verify_owner_dashboard_deployment import migration_statements_sha256, normalize_migration_statements

    repo = Path.cwd()
    candidate_sha = _git(repo, "rev-parse", "HEAD")
    current = {
        "format": "stocks-production-schema-inventory-v1",
        "main_sha": candidate_sha,
        "production_binding_sha256": hashlib.sha256(
            ("stocks-production-schema-inventory-v1\\0" + PROJECT_REF).encode()
        ).hexdigest(),
        "relation_presence": {f"{schema}.{name}": True for schema, name in RELATION_PRESENCE},
        "catalog": {"relations": [{"name": "stock_agent_release_migration_ledger"}]},
        "root_algorithm": "sha256-sorted-row-hashes-v1",
        "protected_roots": [{
            "relation": "stock_agent_release_migration_ledger", "count": 1, "root_sha256": "d" * 64,
        }],
    }
    current["receipt_sha256"] = hashlib.sha256(
        json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sql_path = "sql/reconciliation/20261004_production_schema_reconciliation.sql"
    reconciliation = {
        "after_inventory_receipt_sha256": current["receipt_sha256"],
        "after_projected_roots_sha256": "e" * 64,
        "before_projected_roots_sha256": "e" * 64,
        "ci_workflow_run_id": "1",
        "expected_relation_count": len(RELATION_PRESENCE),
        "format": "stocks-production-schema-reconciliation-v1",
        "main_sha": candidate_sha,
        "prior_inventory_receipt_sha256": "f" * 64,
        "prior_inventory_run_id": "2",
        "reconciliation_path": sql_path,
        "reconciliation_sha256": migration_statements_sha256(
            normalize_migration_statements((repo / sql_path).read_text())
        ),
        "row_count": 1,
        "snapshot_artifact_sha256": "1" * 64,
        "snapshot_plaintext_sha256": "2" * 64,
        "table_count": 1,
        "transaction_status": "committed",
        "writer_attempts": 1,
    }
    reconciliation["receipt_sha256"] = hashlib.sha256(
        json.dumps(reconciliation, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result = validate_reconciliation_continuity(
        current, reconciliation, PROJECT_REF, candidate_sha, repo,
    )
    assert result["state_unchanged_since_reconciliation"] is True
    assert result["continuity_scope"] == "one_time_full_inventory_baseline"

    current["protected_roots"][0]["root_sha256"] = "3" * 64
    current["receipt_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in current.items() if k != "receipt_sha256"}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(RuntimeError, match="changed since reconciliation"):
        validate_reconciliation_continuity(current, reconciliation, PROJECT_REF, candidate_sha, repo)


def test_auth_attestation_is_read_only_and_redacts_owner_identity():
    from scripts.attest_existing_v1_runtime import inspect_single_owner, validate_auth_configuration

    origin = "https://personal-stock-agent.example.chatgpt.site"
    config = {
        "disable_signup": True,
        "external_email_enabled": True,
        "jwt_exp": 900,
        "mailer_autoconfirm": False,
        "mailer_allow_unverified_email_sign_ins": False,
        "mailer_otp_exp": 600,
        "mailer_otp_length": 6,
        "mailer_secure_email_change_enabled": True,
        "site_url": origin,
        "uri_allow_list": origin,
        "mailer_templates_magic_link_content": "Your code is {{ .Token }}",
    }
    assert validate_auth_configuration(config, origin)["email_flow"] == "code"
    assert validate_auth_configuration(config, origin)["jwt_expiry_seconds"] == 900

    calls = []

    def read_owner(url, headers):
        calls.append((url, headers))
        return 200, json.dumps({"users": [{
            "id": "12345678-1234-4123-8123-123456789abc",
            "email": "owner@example.com",
            "email_confirmed_at": "2026-09-01T00:00:00Z",
        }]}).encode()

    receipt = inspect_single_owner(
        "https://pppppppppppppppppppp.supabase.co",
        "OWNER@example.com",
        "sb_secret_" + "s" * 40,
        requester=read_owner,
    )
    assert len(calls) == 1 and "/admin/users?page=1&per_page=2" in calls[0][0]
    assert receipt["auth_user_count"] == 1
    assert "email" not in receipt and "id" not in receipt
    assert len(receipt["owner_secret_sha256"]) == 64

    config["disable_signup"] = False
    with pytest.raises(RuntimeError, match="unsafe"):
        validate_auth_configuration(config, origin)


def test_managed_secret_bindings_match_live_owner_origin_and_preserved_database_url():
    from scripts.attest_existing_v1_runtime import attest_managed_secret_bindings

    origin = "https://personal-stock-agent.example.chatgpt.site"
    owner_digest = hashlib.sha256(b"12345678-1234-4123-8123-123456789abc").hexdigest()
    database_digest = hashlib.sha256(b"postgresql://preserved").hexdigest()

    class Secrets:
        def managed_secret_digests(self):
            return [
                {"name": "DASHBOARD_ALLOWED_ORIGINS", "digest": hashlib.sha256(origin.encode()).hexdigest()},
                {"name": "DASHBOARD_DATABASE_URL", "digest": database_digest},
                {"name": "DASHBOARD_OWNER_USER_ID", "digest": owner_digest},
            ]

    result = attest_managed_secret_bindings(Secrets(), origin, owner_digest, database_digest)
    assert result == {
        "status": "verified", "managed_secret_count": 3,
        "owner_binding": True, "origin_binding": True, "database_url_unchanged": True,
    }

    with pytest.raises(RuntimeError, match="binding differs"):
        attest_managed_secret_bindings(Secrets(), origin, "a" * 64, database_digest)


def test_anonymous_api_canary_requires_401_exact_cors_and_no_store():
    from scripts.attest_existing_v1_runtime import verify_anonymous_denial

    origin = "https://personal-stock-agent.example.chatgpt.site"
    receipt = verify_anonymous_denial(
        "https://pppppppppppppppppppp.supabase.co/functions/v1/owner-dashboard-api",
        origin,
        requester=lambda _url, _headers: (
            401,
            {"Access-Control-Allow-Origin": origin, "Cache-Control": "no-store"},
            b'{"error":{"code":"unauthorized"}}',
        ),
    )
    assert receipt == {"status": "verified", "method": "GET", "anonymous_status": 401}

    with pytest.raises(RuntimeError, match="headers are unsafe"):
        verify_anonymous_denial(
            "https://pppppppppppppppppppp.supabase.co/functions/v1/owner-dashboard-api",
            origin,
            requester=lambda _url, _headers: (
                401,
                {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"},
                b'{"error":{"code":"unauthorized"}}',
            ),
        )


def test_authenticated_owner_canary_reads_database_and_always_revokes_session():
    from scripts.attest_existing_v1_runtime import verify_authenticated_owner_read

    origin = "https://personal-stock-agent.example.chatgpt.site"
    calls = []
    payload = {
        "contract_version": 1,
        "data_as_of": "2026-09-06T00:00:00Z",
        "freshness": "fresh",
        "data": {"boundaries": {
            "owner_only": True, "suggestion_only": True,
            "friend_invitations": "disabled", "brokerage_authority": "none",
        }},
    }

    def revoke_current_session(*_args, **kwargs):
        calls.append(kwargs.get("scope"))
        return {"status": "revoked", "scope": kwargs.get("scope")}

    result = verify_authenticated_owner_read(
        "https://pppppppppppppppppppp.supabase.co/functions/v1/owner-dashboard-api",
        origin,
        "owner@example.com",
        "sb_secret_" + "s" * 40,
        "sb_publishable_" + "p" * 24,
        obtain_token=lambda *_args: "t" * 40,
        revoke_token=revoke_current_session,
        requester=lambda _url, _headers: (
            200,
            {"Access-Control-Allow-Origin": origin, "Cache-Control": "no-store"},
            json.dumps(payload).encode(),
        ),
    )
    assert result["owner_status"] == 200 and result["database_read"] is True
    assert result["ephemeral_session"] == "current_session_revoked"
    assert calls == ["local"]


def test_attestation_workflow_is_manual_exact_main_and_has_no_production_mutation_commands():
    path = Path(".github/workflows/existing-v1-runtime-attestation.yml")
    workflow = yaml.safe_load(path.read_text())
    assert workflow["name"] == "One-time existing V1 runtime baseline attestation"
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    job = workflow["jobs"]["attest"]
    assert job["environment"] == "owner-dashboard-production"
    assert job["permissions"] if "permissions" in job else workflow["permissions"]
    commands = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "scripts/attest_existing_v1_runtime.py" in commands
    assert '"${GITHUB_SHA:-}" = "$MAIN_SHA"' in commands
    assert "actions/runs/$CI_WORKFLOW_RUN_ID" in commands
    assert "actions/artifacts/$RECONCILIATION_ARTIFACT_ID/zip" in commands
    assert "sha256sum" in commands
    assert "/database/query/read-only" not in commands  # implemented inside the tested Python boundary
    forbidden = (
        "functions deploy", "functions delete", "db push", "secrets set", "secrets unset",
        "start_run", "collect_market_intelligence.py", "deploy_site", "save_site_version",
        "provision_owner_dashboard_auth.py", "generate_link", "ALTER ROLE", "CREATE ROLE",
    )
    assert not any(token in commands for token in forbidden)
    secret_refs = {
        match for match in __import__("re").findall(r"secrets\.([A-Z0-9_]+)", path.read_text())
    }
    assert secret_refs == {
        "DASHBOARD_OWNER_EMAIL", "SUPABASE_ACCESS_TOKEN", "SUPABASE_PROJECT_REF",
        "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SERVICE_ROLE_KEY",
    }
    assert any(str(step.get("uses", "")).startswith("actions/upload-artifact@") for step in job["steps"])
