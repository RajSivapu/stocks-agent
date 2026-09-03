from __future__ import annotations

import stat
from pathlib import Path
from urllib.parse import urlparse

from scripts import provision_runtime_roles as provision


SESSION_TEMPLATE = (
    "postgresql://postgres.projectref:admin-password@"
    "aws-0-us-east-1.pooler.supabase.com:5432/postgres"
)


def test_database_provisioning_creates_one_login_per_privilege_role(tenant_database):
    passwords = iter([f"runtime-password-{index:02d}-long" for index in range(4)])
    with tenant_database.transaction(force_rollback=True):
        secrets = provision.provision_database_roles(
            tenant_database,
            SESSION_TEMPLATE,
            password_factory=passwords.__next__,
        )

        assert set(secrets) == {
            "AGENT_DATABASE_URL",
            "SCHEDULER_DATABASE_URL",
            "TELEGRAM_DATABASE_URL",
            "BACKUP_DATABASE_URL",
        }
        for name, database_url in secrets.items():
            parsed = urlparse(database_url)
            assert parsed.hostname == "aws-0-us-east-1.pooler.supabase.com"
            assert parsed.port == 5432
            assert parsed.username.endswith(".projectref")
            assert parsed.username != "postgres.projectref"
            assert name not in database_url

        rows = tenant_database.execute(
            """
            SELECT member.rolname, granted.rolname, member.rolcanlogin,
                   member.rolsuper, member.rolbypassrls
            FROM pg_auth_members membership
            JOIN pg_roles member ON member.oid = membership.member
            JOIN pg_roles granted ON granted.oid = membership.roleid
            WHERE member.rolname LIKE 'stock_agent_%_runtime'
            ORDER BY member.rolname
            """
        ).fetchall()
        assert len(rows) == 4
        assert all(can_login and not superuser and not bypass for _, _, can_login, superuser, bypass in rows)
        assert {(member, granted) for member, granted, *_ in rows} == {
            ("stock_agent_gateway_runtime", "stock_agent_gateway"),
            ("stock_agent_scheduler_runtime", "stock_agent_scheduler"),
            ("stock_agent_telegram_runtime", "stock_agent_telegram"),
            ("stock_agent_backup_runtime", "stock_agent_backup"),
        }


def test_secret_file_is_0600_passed_by_path_and_removed(monkeypatch, tmp_path):
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

    names = provision.publish_secrets(
        {
            "AGENT_DATABASE_URL": "postgresql://contains-secret",
            "TELEGRAM_DATABASE_URL": "postgresql://contains-other-secret",
        },
        project_ref="projectref",
        runner=runner,
    )

    assert names == ["AGENT_DATABASE_URL", "TELEGRAM_DATABASE_URL"]
    assert observed["mode"] == 0o600
    assert "contains-secret" in observed["contents"]
    assert "contains-secret" not in " ".join(observed["command"])
    assert observed["command"][:3] == ["npx", "--yes", "supabase@2.116.0"]
    assert not observed["path"].exists()
