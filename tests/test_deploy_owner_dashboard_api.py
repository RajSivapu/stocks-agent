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

    def runner(command, **_options):
        commands.append(command)
        output = '[{"name":"owner-dashboard-api","version":3}]' if "list" in command else "ok"
        return type("Result", (), {"returncode": 0, "stdout": output, "stderr": ""})()

    receipt = deploy.deploy_function(PROJECT_REF, "a" * 40, tmp_path, runner)
    assert receipt["git_sha"] == "a" * 40
    assert receipt["function_version"] == 3
    assert any(command[-2:] == ["--no-verify-jwt", "--use-api"] for command in commands)
    assert all("service_role" not in " ".join(command).lower() for command in commands)
