import stat
from pathlib import Path

import pytest

from scripts import deploy_owner_dashboard_api as deploy


PROJECT_REF = "hlxpxbxhqctwsqizwjjy"
OWNER_ID = "6903b3cc-05b7-4f90-bbc2-7e80a3a59e22"
ORIGIN = "https://stocks.example.com"
DATABASE_URL = (
    "postgresql://stock_agent_dashboard_runtime.hlxpxbxhqctwsqizwjjy:"
    "dashboard-password-longer-than-24@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)
ROLE_RECEIPT = {
    "status": "verified", "runtime_role": "stock_agent_dashboard_runtime",
    "privilege_role": "stock_agent_dashboard", "table_count": 15,
    "write_privileges": 0, "application_function_execute": 0, "owned_objects": 0,
}


def configuration(**overrides):
    values = {
        "project_ref": PROJECT_REF,
        "owner_user_id": OWNER_ID,
        "allowed_origin": ORIGIN,
        "database_url": DATABASE_URL,
        "role_receipt": ROLE_RECEIPT,
        "secret_names": deploy.DASHBOARD_SECRET_NAMES,
    }
    values.update(overrides)
    return values


def test_valid_configuration_is_normalized_without_secret_values():
    result = deploy.validate_deployment_configuration(**configuration())
    assert result["project_ref_digest"] != PROJECT_REF
    assert result["allowed_origin"] == ORIGIN
    assert "dashboard-password" not in str(result)


def test_static_configuration_can_be_validated_before_database_mutation():
    result = deploy.validate_static_configuration(
        PROJECT_REF, OWNER_ID, ORIGIN, deploy.DASHBOARD_SECRET_NAMES,
    )
    assert result["allowed_origin"] == ORIGIN
    with pytest.raises(ValueError, match="owner UUID"):
        deploy.validate_static_configuration(PROJECT_REF, "", ORIGIN, deploy.DASHBOARD_SECRET_NAMES)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("project_ref", "project-ref", "project reference"),
        ("owner_user_id", "not-a-uuid", "owner UUID"),
        ("allowed_origin", "https://stocks.example.com/path", "exact HTTPS origin"),
        ("database_url", "postgresql://postgres:secret@db.example.com/postgres", "Supavisor"),
        ("role_receipt", {**ROLE_RECEIPT, "write_privileges": 1}, "role verifier"),
        ("secret_names", (*deploy.DASHBOARD_SECRET_NAMES, "SUPABASE_SERVICE_ROLE_KEY"), "secret manifest"),
    ),
)
def test_configuration_rejects_every_unsafe_input(field, value, message):
    with pytest.raises(ValueError, match=message):
        deploy.validate_deployment_configuration(**configuration(**{field: value}))


def test_git_release_refuses_dirty_and_unpushed_commits(tmp_path):
    outputs = iter([" M file.ts\n", "abc\n", "abc\n"])

    def dirty_runner(*_args, **_kwargs):
        return type("Result", (), {"returncode": 0, "stdout": next(outputs), "stderr": ""})()

    with pytest.raises(RuntimeError, match="clean"):
        deploy.verify_git_release(tmp_path, dirty_runner)

    values = iter(["", "local\n", "remote\n"])

    def unpushed_runner(*_args, **_kwargs):
        return type("Result", (), {"returncode": 0, "stdout": next(values), "stderr": ""})()

    with pytest.raises(RuntimeError, match="pushed"):
        deploy.verify_git_release(tmp_path, unpushed_runner)


def test_local_suite_failure_stops_deployment(tmp_path):
    runner = lambda *_args, **_kwargs: type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
    with pytest.raises(RuntimeError, match="local verification"):
        deploy.run_local_verification(tmp_path, runner)


def test_secret_manifest_uses_a_private_file_and_never_command_arguments(tmp_path, monkeypatch):
    observed = {}
    monkeypatch.setattr(deploy.tempfile, "gettempdir", lambda: str(tmp_path))

    def runner(command, **_options):
        secret_path = Path(command[command.index("--env-file") + 1])
        observed["mode"] = stat.S_IMODE(secret_path.stat().st_mode)
        observed["contents"] = secret_path.read_text()
        observed["command"] = command
        observed["path"] = secret_path
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    deploy.publish_dashboard_secrets(
        PROJECT_REF,
        {
            "DASHBOARD_DATABASE_URL": DATABASE_URL,
            "DASHBOARD_OWNER_USER_ID": OWNER_ID,
            "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
        },
        runner,
    )
    assert observed["mode"] == 0o600
    assert DATABASE_URL in observed["contents"]
    assert DATABASE_URL not in " ".join(observed["command"])
    assert not observed["path"].exists()


def test_function_deploy_is_pinned_and_returns_a_bounded_receipt(tmp_path):
    commands = []
    list_count = 0

    def runner(command, **_options):
        nonlocal list_count
        commands.append(command)
        if "list" in command:
            list_count += 1
            output = "[]" if list_count == 1 else '[{"name":"owner-dashboard-api","version":1}]'
        else:
            output = "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert receipt["git_sha"] == "a" * 40
    assert receipt["function_version"] == 1
    assert receipt["rollback_function_version"] is None
    assert any(command[-2:] == ["--no-verify-jwt", "--use-api"] for command in commands)
    assert all("service_role" not in " ".join(command).lower() for command in commands)


def test_initial_deploy_refuses_to_overwrite_an_existing_function(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        return type("Result", (), {
            "returncode": 0,
            "stdout": '[{"name":"owner-dashboard-api","version":4}]',
            "stderr": "",
        })()

    with pytest.raises(RuntimeError, match="already exists"):
        deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert not any("deploy" in command for command in commands)


def test_failed_initial_canary_deletes_only_the_new_dashboard_function(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    receipt = deploy.rollback_initial_function(PROJECT_REF, tmp_path, runner)
    assert receipt == {
        "status": "rolled_back", "function": "owner-dashboard-api",
        "dashboard_secrets_unset": list(deploy.DASHBOARD_SECRET_NAMES),
    }
    assert commands == [
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "secrets", "unset",
            *deploy.DASHBOARD_SECRET_NAMES, "--project-ref", PROJECT_REF, "--yes",
        ],
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "functions", "list",
            "--project-ref", PROJECT_REF, "--output", "json",
        ],
        [
            "npx", "--yes", f"supabase@{deploy.SUPABASE_CLI_VERSION}", "functions", "delete",
            "owner-dashboard-api", "--project-ref", PROJECT_REF, "--yes",
        ],
    ]


def test_rollback_skips_delete_when_no_dashboard_function_exists(tmp_path):
    commands = []

    def runner(command, **_options):
        commands.append(command)
        output = "[]" if "list" in command else "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.rollback_initial_function(PROJECT_REF, tmp_path, runner)
    assert receipt["status"] == "rolled_back"
    assert not any("delete" in command for command in commands)


def test_rollback_attempts_edge_cleanup_even_if_runtime_login_disable_fails():
    events = []

    def connector(*_args, **_kwargs):
        events.append("disable")
        raise RuntimeError("database unavailable")

    def edge_rollback(*_args, **_kwargs):
        events.append("edge")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="rollback was incomplete"):
        deploy.rollback_initial_deployment(
            PROJECT_REF,
            "postgresql://admin:secret@example.com/postgres",
            edge_rollback=edge_rollback,
            connector=connector,
        )
    assert events == ["disable", "edge"]


def test_post_publication_deploy_failure_rolls_back_before_propagating():
    events = []

    def publisher(*_args, **_kwargs):
        events.append("publish")

    def deployer(*_args, **_kwargs):
        events.append("deploy")
        raise RuntimeError("function deploy failed")

    def rollback(*_args, **_kwargs):
        events.append("rollback")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="function deploy failed"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=lambda *_args, **_kwargs: events.append("preflight"),
            publisher=publisher,
            deployer=deployer,
            rollback=rollback,
        )
    assert events == ["preflight", "publish", "deploy", "rollback"]


def test_pre_publication_failure_does_not_run_destructive_rollback():
    events = []

    def publisher(*_args, **_kwargs):
        events.append("publish")
        raise RuntimeError("secret publication failed")

    with pytest.raises(RuntimeError, match="secret publication failed"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=lambda *_args, **_kwargs: events.append("preflight"),
            publisher=publisher,
            deployer=lambda *_args, **_kwargs: events.append("deploy"),
            rollback=lambda *_args, **_kwargs: events.append("rollback"),
        )
    assert events == ["preflight", "publish"]


def test_existing_function_stops_before_secret_publication_or_rollback():
    events = []

    def preflight(*_args, **_kwargs):
        events.append("preflight")
        raise RuntimeError("initial dashboard function already exists")

    with pytest.raises(RuntimeError, match="already exists"):
        deploy.publish_and_deploy_or_rollback(
            PROJECT_REF,
            {
                "DASHBOARD_DATABASE_URL": DATABASE_URL,
                "DASHBOARD_OWNER_USER_ID": OWNER_ID,
                "DASHBOARD_ALLOWED_ORIGINS": ORIGIN,
            },
            "a" * 40,
            "postgresql://admin:secret@example.com/postgres",
            preflight=preflight,
            publisher=lambda *_args, **_kwargs: events.append("publish"),
            deployer=lambda *_args, **_kwargs: events.append("deploy"),
            rollback=lambda *_args, **_kwargs: events.append("rollback"),
        )
    assert events == ["preflight"]


def test_canary_failure_invokes_rollback_before_propagating():
    events = []

    def verifier(*_args):
        events.append("canary")
        raise RuntimeError("production canary failed")

    def rollback(*_args):
        events.append("rollback")
        return {"status": "rolled_back"}

    with pytest.raises(RuntimeError, match="production canary failed"):
        deploy.verify_initial_deployment_or_rollback(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            verifier=verifier, rollback=rollback,
        )
    assert events == ["canary", "rollback"]


def test_post_deploy_canary_keeps_runtime_database_url_and_auth_token_out_of_receipt():
    observed = {}

    def token_factory(project_url, owner_email, redirect_origin, service_key, publishable_key):
        observed["auth"] = (project_url, owner_email, redirect_origin, service_key, publishable_key)
        return "owner-access-token"

    def source_collector(database_url, api_url, run_id):
        observed["source"] = (database_url, api_url, run_id)
        return {"source": "receipt"}

    def canary(api_url, origin, token, *, source_reader):
        observed["canary"] = (api_url, origin, token)
        assert source_reader(OWNER_ID) == {"source": "receipt"}
        return {
            "status": "verified", "source_reconciliation": "verified",
            "financial_write_routes": 0, "brokerage_authority": "none",
            "friend_invitations": "disabled",
        }

    def session_revoker(project_url, token, publishable_key):
        observed["revoked"] = (project_url, token, publishable_key)

    receipt = deploy.run_post_deploy_canary(
        PROJECT_REF, ORIGIN, DATABASE_URL, OWNER_EMAIL := "owner@example.com",
        "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
        token_factory=token_factory, source_collector=source_collector, canary=canary,
        session_revoker=session_revoker,
    )
    assert receipt["status"] == "verified"
    assert DATABASE_URL not in str(receipt)
    assert OWNER_EMAIL not in str(receipt)
    assert "owner-access-token" not in str(receipt)
    assert observed["source"][0] == DATABASE_URL
    assert observed["revoked"][1] == "owner-access-token"


def test_post_deploy_canary_revokes_owner_session_when_canary_fails():
    events = []

    def canary(*_args, **_kwargs):
        events.append("canary")
        raise RuntimeError("receipt mismatch")

    def session_revoker(*_args):
        events.append("revoke")

    with pytest.raises(RuntimeError, match="receipt mismatch"):
        deploy.run_post_deploy_canary(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            token_factory=lambda *_args: "owner-access-token",
            source_collector=lambda *_args: {},
            canary=canary,
            session_revoker=session_revoker,
        )
    assert events == ["canary", "revoke"]


def test_post_deploy_canary_rejects_an_incomplete_receipt():
    with pytest.raises(RuntimeError, match="production canary"):
        deploy.run_post_deploy_canary(
            PROJECT_REF, ORIGIN, DATABASE_URL, "owner@example.com",
            "sb_secret_" + "s" * 40, "sb_publishable_" + "p" * 32,
            token_factory=lambda *_args: "owner-token",
            source_collector=lambda *_args: {},
            canary=lambda *_args, **_kwargs: {"status": "verified", "source_reconciliation": "missing"},
            session_revoker=lambda *_args: None,
        )
