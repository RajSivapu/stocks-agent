from __future__ import annotations

from uuid import uuid4

from psycopg.types.json import Jsonb

from tests.security.test_postgrest_isolation import OWNER_A, OWNER_B, as_user


def dispatch(connection, owner_id: str, route: str, body: dict, ip: str = "c" * 64):
    connection.execute("SET LOCAL ROLE authenticated")
    connection.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (owner_id,))
    try:
        return connection.execute(
            "SELECT api.app_dispatch(%s, %s, %s, %s)",
            (route, uuid4(), ip, Jsonb(body)),
        ).fetchone()[0]
    finally:
        connection.execute("RESET ROLE")


def test_settings_update_is_owner_bound_iana_validated_and_has_no_cron_authority(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        request = {
            "display_name": "  Raj  ",
            "timezone": "America/New_York",
            "notify_pre_market": False,
            "notify_intraday": True,
            "notify_post_market": False,
            "notify_operational": True,
            "schedule_pre_market": True,
            "schedule_intraday": False,
            "schedule_post_market": True,
        }
        assert tenant_database.execute(
            "SELECT status FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("active",)
        result = dispatch(tenant_database, OWNER_A, "PATCH /settings", request)
        assert result["ok"] is True, result
        assert result["data"] == {"status": "updated"}
        assert tenant_database.execute(
            "SELECT display_name, timezone FROM app.profiles WHERE id = %s", (OWNER_A,)
        ).fetchone() == ("Raj", "America/New_York")
        assert tenant_database.execute(
            """
            SELECT pre_market_enabled, intraday_enabled, post_market_enabled, operational_enabled
            FROM app.notification_preferences WHERE owner_id = %s
            """,
            (OWNER_A,),
        ).fetchone() == (False, True, False, True)
        assert tenant_database.execute(
            """
            SELECT timezone, pre_market_enabled, intraday_enabled, post_market_enabled
            FROM app.analysis_schedules WHERE owner_id = %s
            """,
            (OWNER_A,),
        ).fetchone() == ("America/New_York", True, False, True)
        assert tenant_database.execute(
            "SELECT timezone FROM app.profiles WHERE id = %s", (OWNER_B,)
        ).fetchone() == ("America/Chicago",)

        invalid_timezone = dispatch(tenant_database, OWNER_A, "PATCH /settings", {
            "timezone": "America/Not_A_Real_Zone",
        }, "d" * 64)
        assert invalid_timezone["ok"] is False
        assert invalid_timezone["error"]["code"] == "INVALID_REQUEST"
        cron = dispatch(tenant_database, OWNER_A, "PATCH /settings", {
            "cron": "* * * * *",
        }, "e" * 64)
        assert cron["ok"] is False
        assert cron["error"]["code"] == "INVALID_REQUEST"


def test_settings_read_returns_only_the_authenticated_owner(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        result = dispatch(tenant_database, OWNER_B, "GET /settings", {})
        assert result["ok"] is True
        assert result["data"]["display_name"] == "Owner B"
        assert "owner_id" not in result["data"]
        assert "primary_connection_id" in result["data"]

        tenant_database.execute(
            "DELETE FROM app.analysis_schedules WHERE owner_id = %s", (OWNER_B,)
        )
        with as_user(tenant_database, OWNER_B) as connection:
            defaults = connection.execute(
                """
                SELECT schedule_timezone, schedule_pre_market, schedule_intraday,
                       schedule_post_market FROM api.settings
                """
            ).fetchone()
        assert defaults == ("America/Chicago", True, True, True)


def test_web_telegram_unlink_is_idempotent_owner_scoped_and_cancels_pending_authority(tenant_database):
    with tenant_database.transaction(force_rollback=True):
        result = dispatch(tenant_database, OWNER_A, "POST /telegram/unlink", {})
        assert result == {"ok": True, "data": {"status": "unlinked"}, "request_id": result["request_id"]}
        assert tenant_database.execute(
            "SELECT status FROM app.telegram_links WHERE owner_id = %s", (OWNER_A,)
        ).fetchone() == ("revoked",)
        assert tenant_database.execute(
            "SELECT status FROM app.telegram_links WHERE owner_id = %s", (OWNER_B,)
        ).fetchone() == ("active",)

        duplicate = dispatch(tenant_database, OWNER_A, "POST /telegram/unlink", {}, "d" * 64)
        assert duplicate["ok"] is True
        assert duplicate["data"] == {"status": "unlinked"}


def test_browser_views_never_expose_provider_or_telegram_credentials(tenant_database):
    sensitive = {
        "owner_id", "inbound_token_digest", "outbound_trigger_secret_id", "trigger_url",
        "telegram_chat_id", "telegram_user_id", "code_digest", "token_digest",
    }
    for view in ("connections", "telegram_status", "settings"):
        columns = {
            name for (name,) in tenant_database.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'api' AND table_name = %s
                """,
                (view,),
            ).fetchall()
        }
        assert not columns & sensitive
    with tenant_database.transaction(force_rollback=True):
        with as_user(tenant_database, OWNER_A) as connection:
            assert connection.execute("SELECT count(*) FROM api.connections").fetchone()[0] >= 1
