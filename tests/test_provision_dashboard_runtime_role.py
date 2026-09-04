from pathlib import Path
import stat
from urllib.parse import urlparse

import pytest

from scripts import provision_dashboard_runtime_role as provision


SESSION_TEMPLATE = (
    "postgresql://postgres.projectref:admin-password@"
    "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)


def test_runtime_url_uses_only_the_dashboard_login():
    value = provision.runtime_url(
        SESSION_TEMPLATE,
        "stock_agent_dashboard_runtime",
        "dashboard-password-longer-than-24",
    )
    parsed = urlparse(value)
    assert parsed.hostname == "aws-0-us-east-1.pooler.supabase.com"
    assert parsed.port == 5432
    assert parsed.username == "stock_agent_dashboard_runtime.projectref"
    assert parsed.password == "dashboard-password-longer-than-24"
    assert "admin-password" not in value


@pytest.mark.parametrize(
    "value",
    (
        "",
        "postgresql://postgres.projectref:secret@db.example.com:5432/postgres",
        "postgresql://postgres.projectref:secret@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
        "postgresql://postgres:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        "postgresql://postgres.projectref:secret@aws-0-us-east-1.pooler.supabase.com:5432/other",
    ),
)
def test_runtime_url_rejects_non_session_templates(value):
    with pytest.raises(ValueError, match="Supavisor"):
        provision.runtime_url(value, "stock_agent_dashboard_runtime", "x" * 30)


def test_secret_file_is_private_passed_by_path_and_removed(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(provision.tempfile, "gettempdir", lambda: str(tmp_path))

    def runner(command, **options):
        path = Path(command[command.index("--env-file") + 1])
        observed["command"] = command
        observed["mode"] = stat.S_IMODE(path.stat().st_mode)
        observed["contents"] = path.read_text()
        observed["path"] = path
        assert options["capture_output"] is True

        class Result:
            returncode = 0

        return Result()

    names = provision.publish_dashboard_secret(
        {"DASHBOARD_DATABASE_URL": "postgresql://contains-dashboard-secret"},
        project_ref="projectref",
        runner=runner,
    )

    assert names == ["DASHBOARD_DATABASE_URL"]
    assert observed["mode"] == 0o600
    assert "contains-dashboard-secret" in observed["contents"]
    assert "contains-dashboard-secret" not in " ".join(observed["command"])
    assert observed["command"][:3] == ["npx", "--yes", "supabase@2.116.0"]
    assert not observed["path"].exists()


def test_secret_publisher_rejects_any_extra_credential():
    with pytest.raises(ValueError, match="only DASHBOARD_DATABASE_URL"):
        provision.publish_dashboard_secret(
            {
                "DASHBOARD_DATABASE_URL": "postgresql://one",
                "SUPABASE_SERVICE_ROLE_KEY": "forbidden",
            },
            project_ref="projectref",
        )
